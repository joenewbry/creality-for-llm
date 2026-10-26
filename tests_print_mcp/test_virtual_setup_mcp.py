"""Real MCP calls and serial simulation for bounded setup before a job."""
import asyncio
from pathlib import Path
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_cold_home_and_jog_use_verified_barrier_and_reject_unsafe_requests(tmp_path):
    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT/'tests/octoprint_virtual_smoke.py'), '--mcp', '--model-fixture', '--journal', str(tmp_path/'setup.sqlite')], cwd=str(ROOT))
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing, read_timeout_seconds=20.) as client:
                await client.initialize()
                async def call(name, args):
                    response = await client.call_tool(name, args)
                    assert not response.is_error, response
                    return response.structured_content
                number = 0
                async def submit(name, args, expected='completed'):
                    nonlocal number
                    deadline = time.time()+10
                    for attempt in range(5):
                        number += 1
                        status = await call('printer.status', {})
                        request = dict(request_id=f'setup-{number}', printer_id=status['printer_id'],
                            connection_generation=status['connection_generation'], expected_state_version=status['state_version'],
                            deadline=deadline, arguments=args)
                        if expected == 'schema_error':
                            response = await client.call_tool(name, request)
                            assert response.is_error
                            assert response.structured_content['error']['message'] == 'invalid_tool_arguments'
                            return response.structured_content
                        result = await call(name, request)
                        if result['status'] == 'rejected' and result['result'].get('reason') == 'stale_state':
                            assert await call(name, request) == result
                            assert time.time() < deadline, result
                            continue
                        break
                    else:
                        raise AssertionError('state kept changing before dispatch')
                    end = time.monotonic()+12
                    while result['status'] == 'running' and time.monotonic() < end:
                        await asyncio.sleep(.01)
                        result = await call('operation.get', {'operation_id': result['operation_id']})
                    assert result['status'] == expected, result
                    assert await call(name, request) == result
                    return result

                jog = dict(axis='x', distance_mm=5, feedrate_mm_min=600)
                rejected = await submit('printer.jog', jog, 'rejected')
                assert rejected['result']['reason'] == 'setup_home_required_or_position_changed'
                home = await submit('printer.home', {})
                assert home['result']['position'] == {'x': 0., 'y': 0., 'z': 0.}
                assert home['result']['physical_motion_qualified'] is False
                for change in ({'axis':'e'}, {'distance_mm':11},
                               {'distance_mm':True}, {'feedrate_mm_min':601}, {'raw':'M112'}):
                    await submit('printer.jog', {**jog, **change}, 'schema_error')
                await submit('printer.jog', {**jog, 'distance_mm':-1}, 'rejected')
                moved = await submit('printer.jog', jog)
                assert moved['result']['position'] == {'x':5., 'y':0., 'z':0.}
                await submit('printer.jog', {**jog, 'distance_mm':-5})
                status = await call('printer.status', {})
                assert status['setup']['position'] == {'x':0., 'y':0., 'z':0.}
                # Two requests, not four: retries must not duplicate either jog.
                assert status['simulation_evidence']['completed_moves'] == 2
                assert status['simulation_evidence']['physical_serial_open_attempts'] == 0
                await submit('job.stage', {'job_id':status['job_id'], 'file_sha256':status['file_sha256']})
                await submit('job.start', {'job_id':status['job_id']})
                rejected = await submit('printer.home', {}, 'rejected')
                assert rejected['result']['reason'] == 'setup_requires_idle_before_first_job'
                await submit('job.pause', {'job_id':status['job_id']})
                await submit('printer.jog', jog, 'rejected')
                await submit('job.cancel', {'job_id':status['job_id']})
    asyncio.run(scenario())
