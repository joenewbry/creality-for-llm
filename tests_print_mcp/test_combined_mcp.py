"""One MCP server/journal drives a job and native prompt through one virtual stream."""
import asyncio
from pathlib import Path
import sys
import time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def test_combined_mcp_recovers_wait_while_pause_is_pending(tmp_path):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[
            str(ROOT/'tests/octoprint_virtual_smoke.py'), '--mcp', '--scenario', 'unified_prompt',
            '--journal', str(tmp_path/'combined.sqlite')], cwd=str(ROOT))
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing, read_timeout_seconds=20.) as client:
                await client.initialize()
                assert len((await client.list_tools()).tools) == 18
                async def call(name, args):
                    result = await client.call_tool(name, args)
                    assert not result.is_error, result
                    return result.structured_content
                async def submit(name, finish=True):
                    status = await call('printer.status', {})
                    arguments = {} if name.startswith('prompt.') else {'job_id': status['job_id']}
                    if name == 'job.stage': arguments['file_sha256'] = status['file_sha256']
                    if name == 'prompt.respond':
                        arguments = {'prompt': status['prompt']['prompt_snapshot']['prompt'], 'choice': 0}
                    req = dict(request_id=name, printer_id=status['printer_id'],
                        connection_generation=status['connection_generation'], expected_state_version=status['state_version'],
                        deadline=time.time()+10, arguments=arguments)
                    result = await call(name, req)
                    if finish:
                        result = await poll(result)
                        assert await call(name, req) == result
                    return result
                async def poll(result):
                    end = time.monotonic()+10
                    while result['status'] == 'running' and time.monotonic() < end:
                        await asyncio.sleep(.02)
                        result = await call('operation.get', {'operation_id': result['operation_id']})
                    assert result['status'] == 'completed', result
                    return result
                # A premature protocol probe is refused before writing, not a transport failure.
                early = await call('printer.status', {})
                rejected = await call('prompt.inspect', dict(request_id='early-inspection', printer_id=early['printer_id'],
                    connection_generation=early['connection_generation'], expected_state_version=early['state_version'],
                    deadline=time.time()+10, arguments={}))
                assert rejected['status'] == 'rejected' and rejected['result']['reason'] == 'no_observed_fixture_prompt'
                await submit('job.stage'); await submit('job.start')
                end = time.monotonic()+5
                while not (await call('job.inspect', {}))['fixture_prompt_waiting']:
                    assert time.monotonic() < end
                    await asyncio.sleep(.02)
                pause = await submit('job.pause', finish=False)
                assert pause['status'] == 'running'
                for name in ('prompt.inspect', 'prompt.bind', 'prompt.respond'):
                    await submit(name)
                await poll(pause)
                paused = await call('job.inspect', {})
                assert paused['resumable'] and paused['job_state'] == 'paused'
                await submit('job.resume')
                end = time.monotonic()+10
                while (status := await call('job.inspect', {}))['job_state'] != 'completed':
                    assert time.monotonic() < end
                    await asyncio.sleep(.02)
                assert status['simulation_evidence']['completed_moves'] == 80
                assert status['simulation_evidence']['all_moves_completed_once_in_order']
                assert status['simulation_evidence']['physical_serial_open_attempts'] == 0
                events = await call('printer.events', {'after': 0, 'limit': 100})
                assert len([e for e in events['events'] if e['status'] == 'completed']) == 7
                history = await call('printer.observations', {'after': 0, 'limit': 1000})
                assert history['continuous_capture'] is False
                snapshots = [e['snapshot'] for e in history['observations']]
                assert any(s.get('fixture_prompt_waiting') for s in snapshots)
                assert any(s.get('resumable') and s.get('job_state') == 'paused' for s in snapshots)
                assert snapshots[-1]['job_state'] == 'completed'
                cursor = history['next_after']
                assert not (await call('printer.observations', {'after': cursor, 'limit': 10}))['observations']
    asyncio.run(run())


def test_cancel_during_native_wait_does_not_claim_device_stopped(tmp_path):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=[
            str(ROOT/'tests/octoprint_virtual_smoke.py'), '--mcp', '--scenario', 'unified_prompt',
            '--journal', str(tmp_path/'cancel.sqlite')], cwd=str(ROOT))
        async with stdio_client(params) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing, read_timeout_seconds=20.) as client:
                await client.initialize()
                async def call(name, args):
                    result = await client.call_tool(name, args)
                    assert not result.is_error, result
                    return result.structured_content
                async def submit(name, seconds=10, request_id=None):
                    state = await call('printer.status', {})
                    arguments = {'job_id': state['job_id']}
                    if name == 'job.stage':
                        arguments['file_sha256'] = state['file_sha256']
                    request = dict(request_id=request_id or name, printer_id=state['printer_id'],
                        connection_generation=state['connection_generation'],
                        expected_state_version=state['state_version'], deadline=time.time()+seconds,
                        arguments=arguments)
                    return request, await call(name, request)
                for name in ('job.stage', 'job.start'):
                    _, result = await submit(name)
                    end = time.monotonic()+5
                    while result['status'] == 'running':
                        assert time.monotonic() < end
                        await asyncio.sleep(.02)
                        result = await call('operation.get', {'operation_id': result['operation_id']})
                    assert result['status'] == 'completed', result
                end = time.monotonic()+5
                while not (await call('printer.status', {}))['fixture_prompt_waiting']:
                    assert time.monotonic() < end
                    await asyncio.sleep(.02)
                request, result = await submit('job.cancel', seconds=2)
                end = time.monotonic()+4
                while result['status'] == 'running':
                    assert time.monotonic() < end
                    await asyncio.sleep(.05)
                    result = await call('operation.get', {'operation_id': result['operation_id']})
                assert result['status'] == 'outcome_unknown', result
                assert result['result']['reason'] == 'cancel_blocked_by_native_wait'
                assert result['result']['stop_confirmed'] is False
                assert result['result']['requires_recovery'] is True
                assert await call('job.cancel', request) == result
                state = await call('printer.status', {})
                assert state['fixture_prompt_waiting'] is True
                assert state['resumable'] is False
                assert state['simulation_evidence']['completed_moves'] == 10
                assert state['simulation_evidence']['physical_serial_open_attempts'] == 0
                _, resume = await submit('job.resume')
                assert resume['status'] == 'rejected'
                # A distinct stop may bypass the unresolved firmware wait after cancel.
                state = await call('printer.status', {})
                stop_request = dict(request_id='stop-after-cancel', printer_id=state['printer_id'],
                    connection_generation=state['connection_generation'],
                    expected_state_version=state['state_version'], deadline=time.time()+5, arguments={})
                stopped = await call('printer.stop', stop_request)
                end = time.monotonic()+5
                while stopped['status'] == 'running':
                    assert time.monotonic() < end
                    await asyncio.sleep(.02)
                    stopped = await call('operation.get', {'operation_id': stopped['operation_id']})
                assert stopped['status'] == 'completed', stopped
                assert stopped['result']['scope'] == 'software_virtual_device_only'
                assert stopped['result']['physical_stop_qualified'] is False
                assert await call('printer.stop', stop_request) == stopped
                state = await call('printer.status', {})
                assert state['job_state'] == 'stopped' and not state['resumable']
                assert state['simulation_evidence']['completed_moves'] == 10
                await asyncio.sleep(.1)
                assert (await call('printer.status', {}))['simulation_evidence']['completed_moves'] == 10
                _, resume = await submit('job.resume', request_id='resume-after-stop')
                assert resume['status'] == 'rejected'
                assert resume['result']['reason'] == 'stop_requires_recovery'
    asyncio.run(run())
