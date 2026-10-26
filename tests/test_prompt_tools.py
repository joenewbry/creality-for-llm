import io
import json
import time
import pytest
from printer_control.tools import ToolRouter, serve
from test_prompt_backend import runtime
from test_firmware_host_actions import actions_binary


def call(router, tool, arguments):
    incoming = io.StringIO(json.dumps({'id': tool, 'tool': tool, 'arguments': arguments}) + '\n')
    outgoing = io.StringIO()
    serve(router, incoming, outgoing)
    return json.loads(outgoing.getvalue())


def request(router, tool, arguments):
    status = call(router, 'printer.status', {})['result']
    return {'request_id': tool, 'printer_id': status['printer_id'],
            'connection_generation': status['connection_generation'],
            'expected_state_version': status['state_version'], 'deadline': 20., 'arguments': arguments}


def complete(router, result):
    end = time.monotonic() + 5
    while result['status'] == 'running' and time.monotonic() < end:
        result = call(router, 'operation.get', {'operation_id': result['operation_id']})['result']
        time.sleep(.005)
    assert result['status'] == 'completed'
    return result


def test_native_prompt_lifecycle_runs_through_json_tools(runtime):
    service, backend, transport, now = runtime
    router = ToolRouter(service)
    description = call(router, 'printer.describe', {})['result']
    assert {tool['name'] for tool in description['tools']} == {
        'prompt.inspect', 'prompt.bind', 'prompt.respond', 'printer.describe',
        'printer.status', 'operation.get', 'printer.events', 'printer.observations'}
    assert description['physical_control_qualified'] is False
    assert transport.writes == []
    for tool, arguments in [('prompt.inspect', {}), ('prompt.bind', {}), ('prompt.respond', {'prompt': 1, 'choice': 0})]:
        envelope = request(router, tool, arguments)
        result = complete(router, call(router, tool, envelope)['result'])
        writes = len(transport.writes)
        assert call(router, tool, envelope)['result'] == result
        assert len(transport.writes) == writes
    status = call(router, 'printer.status', {})['result']
    assert status['session_bound'] and status['snapshot_fresh']
    assert not status['prompt_snapshot']['waiting'] and not status['prompt_snapshot']['active']
    assert status['physical_recovery_qualified'] is False
    events = call(router, 'printer.events', {'after': 0, 'limit': 100})['result']
    assert events['scope'] == 'operation_receipts'
    assert [event['status'] for event in events['events']] == ['accepted', 'running', 'completed'] * 3


def test_prompt_tools_publish_precise_schemas_and_reject_job_or_raw_calls(runtime):
    service, backend, transport, now = runtime
    router = ToolRouter(service)
    definitions = {tool['name']: tool for tool in router.definitions()}
    for name in ('prompt.inspect', 'prompt.bind'):
        assert definitions[name]['inputSchema']['properties']['arguments']['properties'] == {}
    fields = definitions['prompt.respond']['inputSchema']['properties']['arguments']
    assert fields['properties']['choice']['const'] == 0
    assert fields['additionalProperties'] is False
    for name in ('job.inspect', 'job.start', 'printer.raw', 'printer.set_temperature'):
        assert 'error' in call(router, name, {})
    assert not transport.writes


def test_prompt_read_tools_do_not_issue_wire_commands_and_report_staleness(runtime):
    service, backend, transport, now = runtime
    router = ToolRouter(service)
    complete(router, call(router, 'prompt.inspect', request(router, 'prompt.inspect', {}))['result'])
    assert len(transport.writes) == 1
    now[0] = 16.
    status = call(router, 'printer.status', {})['result']
    assert status['snapshot_fresh'] is False and status['snapshot_age_seconds'] == 6.
    assert len(transport.writes) == 1
