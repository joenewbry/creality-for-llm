import json
import os
import select
import sqlite3
import subprocess
import time
import pytest
from printer_control.prompt_backend import PromptBackend
from printer_control.prompt_channel import PromptChannel
from printer_control.service import OperationService
from test_firmware_host_actions import actions_binary


class NativeTransport:
    def __init__(self, binary, journal, scenario='runtime'):
        self.process = subprocess.Popen([str(binary), scenario], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.journal = journal
        self.writes = []
        self.keep_waiting = False
        self.partial = False

    def write(self, data):
        with sqlite3.connect(self.journal) as db:
            rows = db.execute("SELECT status FROM operations WHERE status='running'").fetchall()
        assert len(rows) == 1, 'wire write must follow durable claim'
        self.writes.append(data)
        self.process.stdin.write(data + b'~')
        self.process.stdin.flush()
        return len(data)-1 if self.partial else len(data)

    def pump(self):
        if not select.select([self.process.stdout], [], [], 0)[0]: return b''
        data = os.read(self.process.stdout.fileno(), 4096)
        if self.keep_waiting:
            data = data.replace(b'A0 H0 B00 R00 W0', b'A0 H0 B00 R00 W1')
        return data

    def close(self):
        if self.process.stdin.closed: return
        self.process.stdin.close()
        try: self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=5)
        self.process.stdout.close(); self.process.stderr.close()


@pytest.fixture
def runtime(tmp_path, actions_binary, request):
    journal = tmp_path / 'prompt.sqlite'
    transport = NativeTransport(actions_binary, journal, getattr(request, 'param', 'runtime'))
    now = [10.]
    channel = PromptChannel(transport.write, 'native-g1', session=123)
    backend = PromptBackend(channel, printer_id='native-only', clock=lambda: now[0],
                            pump=transport.pump, close_transport=transport.close)
    service = OperationService(journal, backend, clock=lambda: now[0])
    yield service, backend, transport, now
    service.close()


def submit(service, backend, operation, arguments=None, *, deadline=20.):
    context = backend.context()
    return dict(request_id=operation, printer_id=context.printer_id, connection_generation=context.generation,
                expected_state_version=context.state_version, deadline=deadline, operation=operation, arguments=arguments or {})


def finish(service, result):
    end = time.monotonic() + 5
    while result['status'] == 'running' and time.monotonic() < end:
        result = service.poll(result['operation_id'])
        time.sleep(.005)
    assert result['status'] != 'running', 'native operation did not finish'
    return result


def bind(service, backend):
    for operation in ('prompt.inspect', 'prompt.bind'):
        result = finish(service, service.submit(submit(service, backend, operation)))
        assert result['status'] == 'completed'


def test_journaled_native_response_completes_with_wait_evidence_and_no_duplicate_write(runtime):
    service, backend, transport, now = runtime
    bind(service, backend)
    request = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    pending = service.submit(request)
    assert pending['status'] == 'running' and len(transport.writes) == 3
    complete = finish(service, pending)
    assert complete['status'] == 'completed'
    assert complete['result']['resolution'] == 'firmware_user_wait_cleared'
    assert complete['result']['physical_recovery_qualified'] is False
    assert service.submit(request) == complete and len(transport.writes) == 3
    events = service.journal.events()
    assert [e['status'] for e in events if e['operation_id'] == pending['operation_id']] == ['accepted', 'running', 'completed']


def test_acceptance_without_wait_resolution_stays_unknown(runtime):
    service, backend, transport, now = runtime
    bind(service, backend)
    transport.keep_waiting = True
    result = finish(service, service.submit(submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})))
    assert result['status'] == 'outcome_unknown'
    assert result['result']['reason'] == 'wait_resolution_not_observed'


def test_service_propagates_original_deadline_to_channel(runtime):
    service, backend, transport, now = runtime
    bind(service, backend)
    # Do not pump the native response until the original request deadline.
    request = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0}, deadline=11.)
    result = service.submit(request)
    now[0] = 11.
    result = service.poll(result['operation_id'])
    assert result['status'] == 'outcome_unknown'
    assert result['result']['reason'] == 'receipt_deadline_expired'
    assert service.submit(request) == result and len(transport.writes) == 3


def test_partial_write_is_durable_unknown(runtime):
    service, backend, transport, now = runtime
    bind(service, backend)
    transport.partial = True
    request = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    result = finish(service, service.submit(request))
    assert result['status'] == 'outcome_unknown'
    assert service.submit(request) == result and len(transport.writes) == 3


@pytest.mark.parametrize('arguments', [{'prompt': 2, 'choice': 0}, {'prompt': 1, 'choice': 1},
                                      {'prompt': True, 'choice': 0}, {'prompt': 1, 'choice': 0, 'raw': 'M108'}])
def test_invalid_response_is_rejected_before_wire(runtime, arguments):
    service, backend, transport, now = runtime
    bind(service, backend)
    result = service.submit(submit(service, backend, 'prompt.respond', arguments))
    assert result['status'] == 'rejected' and len(transport.writes) == 2


def test_restart_does_not_replay_a_journaled_prompt_response(tmp_path, actions_binary):
    path = tmp_path / 'restart.sqlite'
    def open_service(generation, session):
        transport = NativeTransport(actions_binary, path)
        channel = PromptChannel(transport.write, generation, session=session)
        backend = PromptBackend(channel, printer_id='native-only', clock=lambda: 10.,
                                pump=transport.pump, close_transport=transport.close)
        return OperationService(path, backend, clock=lambda: 10.), backend, transport
    first, backend, transport = open_service('g1', 123)
    try:
        bind(first, backend)
        request = submit(first, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
        running = first.submit(request)
        assert running['status'] == 'running'
    finally:
        first.close()
    second, _, successor = open_service('g2', 456)
    try:
        assert second.recovered == 1
        assert second.submit(request)['status'] == 'outcome_unknown'
        assert second.poll(running['operation_id'])['status'] == 'outcome_unknown'
        assert successor.writes == []
    finally:
        second.close()


@pytest.mark.parametrize('order', ['before', 'after'])
def test_native_response_with_injected_fault_is_unknown_and_visible(runtime, order):
    service, backend, transport, now = runtime
    bind(service, backend)
    original = transport.pump
    def faulty_pump():
        data = original()
        if data and b'//llm:v1' in data:
            fault = b'Error:Thermal Runaway\n'
            return fault + data if order == 'before' else data + fault
        return data
    backend.pump = faulty_pump
    request = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    result = finish(service, service.submit(request))
    assert result['status'] == 'outcome_unknown'
    assert result['result']['reason'] == 'firmware_fault'
    assert service.submit(request) == result
    status = backend.inspect()
    assert status['blocking_event']['kind'] == 'fault'
    assert status['blocking_event']['data']['message'] == 'Error:Thermal Runaway'
    assert status['snapshot_fresh'] is False and status['prompt_snapshot'] is None
    assert status['automatic_fault_recovery_supported'] is False
    fresh = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    fresh['request_id'] = 'response-after-fault'
    rejected = service.submit(fresh)
    assert rejected['status'] == 'rejected'
    assert rejected['result']['reason'] == 'prompt_channel_not_ready'
    assert len(transport.writes) == 3


def test_native_receipts_through_shared_receive_copy(runtime):
    from printer_control.shared_receiver import SharedReceiveBuffer
    service, backend, transport, now = runtime
    receiver = SharedReceiveBuffer()
    def pump():
        raw = transport.pump()
        if raw:
            line = raw.decode('ascii')
            assert receiver.received_hook(None, line) is line
        return receiver.drain()
    backend.pump = pump
    bind(service, backend)
    result = finish(service, service.submit(submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})))
    assert result['status'] == 'completed' and len(transport.writes) == 3
    assert receiver.fault is None


def test_receive_copy_loss_invalidates_snapshot_and_pending_operation(runtime):
    from printer_control.shared_receiver import SharedReceiveBuffer
    service, backend, transport, now = runtime
    bind(service, backend)
    request = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    pending = service.submit(request)
    receiver = SharedReceiveBuffer(capacity=128)
    receiver.received_hook(None, 'x'*129)
    backend.pump = receiver.drain
    result = service.poll(pending['operation_id'])
    assert result['status'] == 'outcome_unknown'
    status = backend.inspect()
    assert status['channel_state'] == 'uncertain' and status['prompt_snapshot'] is None
    assert status['invalidation_reason'] == 'receive_stream_unavailable'
    assert service.submit(request) == result and len(transport.writes) == 3


@pytest.mark.parametrize('runtime', ['runtime_runout'], indirect=True)
def test_native_runout_requires_physical_action_and_cannot_be_retried_away(runtime):
    service, backend, transport, now = runtime
    bind(service, backend)
    request = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    result = finish(service, service.submit(request))
    assert result['status'] == 'failed'
    assert result['result']['reason'] == 'firmware_rejection_09'
    assert result['result']['receipt']['waiting']
    guidance = backend.inspect()['prompt_guidance']
    assert guidance['category'] == 'user_wait'  # Creality runout uses this reason too
    assert guidance['requires_physical_action'] is True
    assert guidance['allowed_responses'] == []
    assert service.submit(request) == result
    writes = len(transport.writes)
    retry = submit(service, backend, 'prompt.respond', {'prompt': 1, 'choice': 0})
    retry['request_id'] = 'try-again'
    refused = service.submit(retry)
    assert refused['status'] == 'rejected'
    assert refused['result']['reason'] == 'physical_intervention_required'
    assert len(transport.writes) == writes
    # Successful read-only inspection must not erase physical intervention evidence.
    inspect = submit(service, backend, 'prompt.inspect')
    inspect['request_id'] = 'inspect-after-runout'
    assert finish(service, service.submit(inspect))['status'] == 'completed'
    assert backend.inspect()['prompt_guidance']['requires_physical_action'] is True
    assert backend.inspect()['prompt_snapshot']['waiting']


def test_guidance_offers_only_fresh_bound_generic_response(runtime):
    service, backend, transport, now = runtime
    assert backend.inspect()['prompt_guidance']['allowed_responses'] == []
    bind(service, backend)
    guidance = backend.inspect()['prompt_guidance']
    assert guidance['allowed_responses'] == [{'choice': 0, 'action': 'continue'}]
    # A generic reason alone cannot establish absence of a runout condition.
    assert guidance['requires_physical_action'] is None
    assert guidance['firmware_message_available'] is False
    now[0] += backend.channel.max_age + 1
    assert backend.inspect()['prompt_guidance']['allowed_responses'] == []


def test_diagnostic_rejection_does_not_become_accepted_snapshot(runtime):
    service, backend, transport, now = runtime
    bind(service, backend)
    from dataclasses import replace
    snapshot, _ = backend.channel.snapshot()
    backend._last_physical_receipt = replace(snapshot, result=9)
    backend._physical_required.add((snapshot.session, snapshot.prompt))
    backend.channel.invalidate('transport_lost', now=now[0])
    state = backend.inspect()
    assert state['prompt_snapshot'] is None and not state['snapshot_fresh']
    assert state['prompt_guidance']['requires_physical_action'] is True
    assert state['prompt_guidance']['allowed_responses'] == []
