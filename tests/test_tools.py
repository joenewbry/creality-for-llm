import io
import json
import threading
import pytest
from printer_control.journal import RequestRejected
from printer_control.service import OperationService, Context, Outcome
from printer_control.tools import ToolRouter, serve
from printer_control.preflight import cold_virtual_job


class Backend:
    supported_operations = ('job.stage', 'job.start', 'job.pause', 'job.resume', 'job.cancel')

    def __init__(self):
        self.lock = threading.RLock()
        self.commands = []

    def guard(self): return self.lock
    def context(self): return Context('virtual', 'g1', 1, 10)
    def inspect(self): return {'physical_printer': False, 'sender_state': 'Paused'}
    def capabilities(self): return {'physical_printer': False, 'unsupported': ['prompt.respond']}
    def validate(self, op, args):
        if args != {'job_id': 'job'}: raise RequestRejected('invalid_arguments')
    def dispatch(self, *args, deadline): self.commands.append(args)
    def poll(self, op): return Outcome('completed', {'evidence_type': 'test_fixture'})
    def close(self): pass


@pytest.fixture
def router(tmp_path):
    with OperationService(tmp_path / 'operations.sqlite', Backend(), clock=lambda: 10) as service:
        yield ToolRouter(service)


def request():
    return dict(request_id='r1', printer_id='virtual', connection_generation='g1',
                expected_state_version=1, deadline=20, arguments={'job_id': 'job'})


def test_tools_publish_strict_schemas_and_do_not_advertise_unimplemented_features(router):
    described = router.call('printer.describe', {})
    assert described['physical_printer'] is False
    assert len(described['tools']) == 12
    assert all(t['inputSchema']['additionalProperties'] is False for t in described['tools'])
    assert 'prompt.respond' in described['unsupported']
    assert 'printer.jog' not in {t['name'] for t in described['tools']}


def test_mutation_receipt_and_event_pagination_through_tool_transport(router):
    args = request()
    receipt = router.call('job.pause', args)
    assert receipt['status'] == 'running'
    assert router.call('job.pause', args) == receipt
    assert len(router.service.backend.commands) == 1
    result = router.call('operation.get', {'operation_id': receipt['operation_id']})
    assert result['status'] == 'completed'
    first = router.call('printer.events', {'after': 0, 'limit': 2})
    second = router.call('printer.events', {'after': first['next_after'], 'limit': 2})
    assert [e['status'] for e in first['events'] + second['events']] == ['accepted', 'running', 'completed']
    assert all(isinstance(e['detail'], dict) for e in second['events'])


@pytest.mark.parametrize('tool,args', [('printer.gcode', {'gcode': 'M112'}),
    ('job.pause', {**request(), 'operation': 'printer.reset'}), ('job.pause', {**request(), 'deadline': float('nan')}),
    ('printer.status', {'port': '/dev/fake'}), ('operation.get', {'operation_id': 1}),
    ('printer.events', {'after': True, 'limit': 1})])
def test_unsupported_or_malformed_tools_cannot_dispatch(router, tool, args):
    with pytest.raises(RequestRejected):
        router.call(tool, args)
    assert router.service.backend.commands == []


def test_line_transport_recovers_from_oversize_and_duplicate_keys(router):
    bad = 'x' * 300 + '\n'
    duplicate = '{"id":"a","id":"b","tool":"job.pause","arguments":{}}\n'
    valid = json.dumps({'id': 3, 'tool': 'printer.status', 'arguments': {}}) + '\n'
    output = io.StringIO()
    serve(router, io.StringIO(bad + duplicate + valid), output, max_line_bytes=256)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r.get('error', {}).get('code') for r in rows] == ['invalid_request', 'invalid_request', None]
    assert rows[2]['id'] == 3 and rows[2]['result']['physical_printer'] is False
    assert router.service.backend.commands == []


@pytest.mark.parametrize('line', ['{"id":true,"tool":"printer.status","arguments":{}}',
    '{"id":1,"tool":"printer.status","arguments":{"x":NaN}}', '[]', '{'])
def test_invalid_json_envelopes_rejected(router, line):
    output = io.StringIO()
    serve(router, io.StringIO(line + '\n'), output)
    assert json.loads(output.getvalue())['error']['code'] == 'invalid_request'


BASE = b'G21\nG90\nM82\nT0\nM106 S0\nM104 S0\nM140 S0\nG1 X1 E1 F1200\n'


def test_cold_profile_preflight_accepts_initialized_fixture():
    cold_virtual_job(BASE)


@pytest.mark.parametrize('extra', [b'M104 S200', b'M106 S255', b'M83', b'G20', b'M112', b'G1 Xnan', b'\xff'])
def test_cold_profile_preflight_rejects_unqualified_effects(extra):
    with pytest.raises(RequestRejected):
        cold_virtual_job(BASE + extra)


def test_status_is_durable_and_history_reads_do_not_poll_or_dispatch(router):
    snapshot = router.call('printer.status', {})
    history = router.call('printer.observations', {'after': 0, 'limit': 10})
    assert history['scope'] == 'polled_state_observations'
    assert history['continuous_capture'] is False
    assert history['observations'][0]['snapshot'] == snapshot
    assert router.call('printer.observations', {'after': 0, 'limit': 10}) == history
    assert router.service.backend.commands == []
