"""Official MCP client, actual OctoPrint sender and bundled virtual firmware."""
import asyncio
from pathlib import Path
import sys
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_job_pause_resume_executes_every_simulated_move_once(tmp_path):
    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'tests/octoprint_virtual_smoke.py'), '--mcp', '--journal', str(tmp_path/'job.sqlite')], cwd=str(ROOT))
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing, read_timeout_seconds=20.) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert len(tools.tools) == 14
                assert 'prompt.respond' not in {tool.name for tool in tools.tools}
                async def read():
                    result = await client.call_tool('job.inspect', {})
                    assert not result.is_error
                    return result.structured_content
                async def submit(name):
                    status = await read()
                    args = {'job_id': status['job_id']}
                    if name == 'job.stage': args['file_sha256'] = status['file_sha256']
                    request = {'request_id': name, 'printer_id': status['printer_id'],
                        'connection_generation': status['connection_generation'],
                        'expected_state_version': status['state_version'], 'deadline': time.time()+30, 'arguments': args}
                    result = (await client.call_tool(name, request)).structured_content
                    end = time.monotonic()+15
                    while result['status'] == 'running' and time.monotonic() < end:
                        await asyncio.sleep(.02)
                        result = (await client.call_tool('operation.get', {'operation_id': result['operation_id']})).structured_content
                    assert result['status'] == 'completed', result
                    assert (await client.call_tool(name, request)).structured_content == result
                    return result
                await submit('job.stage')
                await submit('job.start')
                end = time.monotonic()+10
                while (await read())['simulation_evidence']['completed_moves'] < 5:
                    assert time.monotonic() < end
                    await asyncio.sleep(.02)
                await submit('job.pause')
                paused = await read()
                assert paused['job_state'] == 'paused' and paused['resumable']
                pause_status=(await client.call_tool('job.pause_status',{})).structured_content
                assert pause_status['phase']=='paused' and pause_status['checkpoint_id']
                assert pause_status['resume_request_available'] and not pause_status['resume_authorized']
                assert pause_status['resume_gate']=='fresh_recheck_required'
                before = paused['simulation_evidence']['completed_moves']
                await asyncio.sleep(.5)
                assert (await read())['simulation_evidence']['completed_moves'] == before
                await submit('job.resume')
                end = time.monotonic()+30
                status = await read()
                while status['job_state'] != 'completed' and time.monotonic() < end:
                    await asyncio.sleep(.05)
                    status = await read()
                assert status['job_state'] == 'completed'
                assert status['simulation_evidence'] == {'completed_moves': 80, 'expected_moves': 80,
                    'moves_match_expected_prefix': True, 'all_moves_completed_once_in_order': True,
                    'physical_serial_open_attempts': 0}
    asyncio.run(scenario())
