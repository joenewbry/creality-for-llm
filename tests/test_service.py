from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import os
import subprocess
import sys
import time

import pytest

from printer_control.journal import RequestRejected
from printer_control.ownership import ControllerLease, OwnershipError
from printer_control.service import Context, OperationService, Outcome


class FakeBackend:
    def __init__(self):
        self.lock = threading.RLock()
        self.snapshot = Context('development', 'g1', 1, 10)
        self.dispatched = []
        self.outcomes = {}
        self.closed = False
        self.fail_dispatch = False
        self.fail_close = False

    def guard(self):
        return self.lock

    def context(self):
        return self.snapshot

    def validate(self, operation, arguments):
        if operation != 'job.pause' or arguments != {'job_id': 'job1'}:
            raise RequestRejected('unsupported_operation_or_arguments')

    def dispatch(self, operation_id, operation, arguments, *, deadline):
        assert not self.closed
        self.dispatched.append((operation_id, operation, arguments))
        if self.fail_dispatch:
            raise OSError('write status unknown')

    def poll(self, operation_id):
        return self.outcomes.get(operation_id)

    def close(self):
        if self.fail_close:
            raise OSError('shutdown incomplete')
        self.closed = True


REQUEST = dict(request_id='r1', printer_id='development', connection_generation='g1',
               expected_state_version=1, deadline=20, operation='job.pause', arguments={'job_id': 'job1'})


@pytest.fixture
def setup(tmp_path):
    backend = FakeBackend()
    service = OperationService(tmp_path / 'journal.sqlite', backend, clock=lambda: 10)
    yield service, backend
    service.close()


def test_concurrent_retries_dispatch_once_and_wait_for_evidence(setup):
    service, backend = setup
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: service.submit(REQUEST), range(30)))
    assert len(backend.dispatched) == 1
    assert all(r == receipts[0] for r in receipts)
    op = receipts[0]['operation_id']
    assert service.poll(op)['status'] == 'running'
    backend.outcomes[op] = Outcome('completed', {'evidence': 'simulated queue drained', 'resumable': False})
    assert service.poll(op)['status'] == 'completed'
    assert service.submit(REQUEST)['status'] == 'completed'
    assert len(backend.dispatched) == 1


def test_second_owner_cannot_recover_first_owners_running_operation(setup):
    service, backend = setup
    receipt = service.submit(REQUEST)
    with pytest.raises(OwnershipError):
        OperationService(service.journal.path, FakeBackend(), clock=lambda: 11)
    assert service.journal.get(receipt['operation_id'])['status'] == 'running'


def test_restart_returns_unknown_without_replaying(tmp_path):
    path = tmp_path / 'journal.sqlite'
    with OperationService(path, FakeBackend(), clock=lambda: 10) as first:
        receipt = first.submit(REQUEST)
    replacement = FakeBackend()
    with OperationService(path, replacement, clock=lambda: 11) as second:
        assert second.recovered == 1
        assert second.submit(REQUEST)['status'] == 'outcome_unknown'
        assert second.poll(receipt['operation_id'])['status'] == 'outcome_unknown'
        assert replacement.dispatched == []


@pytest.mark.parametrize('change', [dict(generation='g2'), dict(printer_id='another')])
def test_reconnect_or_wrong_identity_cannot_complete_old_request(setup, change):
    service, backend = setup
    op = service.submit(REQUEST)['operation_id']
    backend.outcomes[op] = Outcome('completed', {'evidence': 'old callback'})
    backend.snapshot = replace(backend.snapshot, **change)
    assert service.poll(op)['status'] == 'outcome_unknown'


@pytest.mark.parametrize('observed', [0, 11, float('nan'), True])
def test_stale_or_invalid_observation_never_dispatches(setup, observed):
    service, backend = setup
    backend.snapshot = replace(backend.snapshot, observed_at=observed)
    assert service.submit(REQUEST)['status'] == 'rejected'
    assert backend.dispatched == []


def test_dispatch_exception_is_uncertain_not_retryable(setup):
    service, backend = setup
    backend.fail_dispatch = True
    assert service.submit(REQUEST)['status'] == 'outcome_unknown'
    assert service.submit(REQUEST)['status'] == 'outcome_unknown'
    assert len(backend.dispatched) == 1


def test_unknown_tool_and_fields_rejected_before_dispatch(setup):
    service, backend = setup
    assert service.submit({**REQUEST, 'arguments': {'job_id': 'job1', 'raw': 'M112'}})['status'] == 'rejected'
    assert backend.dispatched == []


def test_stale_completion_observation_waits_for_reconciliation(setup):
    service, backend = setup
    op = service.submit(REQUEST)['operation_id']
    backend.outcomes[op] = Outcome('completed', {'evidence': 'queue drained'})
    backend.snapshot = replace(backend.snapshot, observed_at=0)
    assert service.poll(op)['status'] == 'running'
    backend.snapshot = replace(backend.snapshot, observed_at=10)
    assert service.poll(op)['status'] == 'completed'


def test_empty_completion_evidence_not_accepted(setup):
    service, backend = setup
    op = service.submit(REQUEST)['operation_id']
    backend.outcomes[op] = Outcome('completed', {})
    with pytest.raises(RequestRejected):
        service.poll(op)
    assert service.journal.get(op)['status'] == 'running'


def test_backend_shutdown_failure_retains_owner_lock(tmp_path):
    path = tmp_path / 'journal.sqlite'
    backend = FakeBackend()
    service = OperationService(path, backend, clock=lambda: 10)
    backend.fail_close = True
    try:
        with pytest.raises(OSError):
            service.close()
        with pytest.raises(OwnershipError):
            OperationService(path, FakeBackend(), clock=lambda: 10)
    finally:
        backend.fail_close = False
        service.close()
    with pytest.raises(OwnershipError):
        service.submit(REQUEST)


def test_replaced_lock_file_is_detected(tmp_path):
    path = tmp_path / 'owner'
    lease = ControllerLease(path).acquire()
    try:
        path.rename(tmp_path / 'original')
        path.touch()
        with pytest.raises(OwnershipError):
            lease.check()
    finally:
        lease.close()


def test_symlink_lock_refused_and_lock_inode_survives_close(tmp_path):
    target = tmp_path / 'target'
    target.touch()
    link = tmp_path / 'owner'
    link.symlink_to(target)
    with pytest.raises(OSError):
        ControllerLease(link).acquire()
    with ControllerLease(target):
        inode = target.stat().st_ino
    assert target.stat().st_ino == inode


def test_actual_child_process_death_releases_ownership(tmp_path):
    path = tmp_path / 'owner'
    program = "from printer_control.ownership import ControllerLease; import sys; lease=ControllerLease(sys.argv[1]).acquire(); print('owned',flush=True); sys.stdin.read()"
    child = subprocess.Popen([sys.executable, '-c', program, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'owned'
        with pytest.raises(OwnershipError):
            ControllerLease(path).acquire()
        child.kill()
        child.wait(timeout=5)
        with ControllerLease(path) as successor:
            successor.check()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()


def test_forked_child_cannot_use_or_unlock_parent_lease(tmp_path):
    path = tmp_path / 'owner'
    with ControllerLease(path) as lease:
        pid = os.fork()
        if pid == 0:
            try:
                lease.check()
            except OwnershipError:
                lease.close()
                os._exit(0)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        lease.check()
        with pytest.raises(OwnershipError):
            ControllerLease(path).acquire()


def test_observation_sampled_during_context_read_is_not_future(tmp_path):
    backend = FakeBackend()
    backend.context = lambda: Context('development', 'g1', 1, time.time())
    with OperationService(tmp_path / 'journal.sqlite', backend) as service:
        result = service.submit({**REQUEST, 'deadline': time.time() + 10})
        assert result['status'] == 'running'


def test_validation_io_failure_cannot_leave_an_accepted_dispatch(setup):
    service, backend = setup
    def fail(*args):
        raise OSError('job disappeared')
    backend.validate = fail
    result = service.submit(REQUEST)
    assert result['status'] == 'failed'
    assert result['result']['dispatched'] is False
    assert backend.dispatched == []


def test_new_operation_waits_for_prior_durable_completion(setup):
    service, backend = setup
    first = service.submit(REQUEST)
    another = service.submit({**REQUEST, 'request_id': 'r2'})
    assert another['status'] == 'rejected'
    assert another['result']['reason'] == 'operation_in_progress'
    assert another['result']['pending_operation_ids'] == [first['operation_id']]
    assert 'operation.get' in another['result']['next_action']
    assert service.submit({**REQUEST, 'request_id': 'r2'}) == another
    assert len(backend.dispatched) == 1


def test_backend_overlap_policy_keeps_prompt_and_job_receipts_independent(setup):
    service, backend = setup
    backend.validate = lambda operation, arguments: None
    first = service.submit(REQUEST)
    backend.can_overlap = lambda operation, ids: operation == 'prompt.inspect' and ids == (first['operation_id'],)
    prompt_request = {**REQUEST, 'request_id': 'prompt', 'operation': 'prompt.inspect', 'arguments': {}}
    prompt = service.submit(prompt_request)
    assert prompt['status'] == 'running' and len(backend.dispatched) == 2
    assert service.submit({**prompt_request, 'request_id': 'second-prompt'})['status'] == 'rejected'
    assert service.submit({**REQUEST, 'request_id': 'second-job'})['status'] == 'rejected'
    backend.outcomes[prompt['operation_id']] = Outcome('completed', {'fixture': 'prompt-observed'})
    completed = service.poll(prompt['operation_id'])
    assert completed['status'] == 'completed'
    assert service.poll(first['operation_id'])['status'] == 'running'
    assert service.submit(prompt_request) == completed and len(backend.dispatched) == 2


@pytest.mark.parametrize('change,reason', [
    ({'generation': 'g2'}, 'connection_changed_before_dispatch'),
    ({'printer_id': 'another'}, 'connection_changed_before_dispatch'),
    ({'state_version': 2}, 'state_changed_before_dispatch'),
    ({'observed_at': 0}, 'stale_observation'),
    ({'observed_at': 11}, 'stale_observation'),
])
def test_committed_request_rechecks_context_before_dispatch(setup, monkeypatch, change, reason):
    service, backend = setup
    claim = service.journal.claim
    def changed_after_claim(*args, **kwargs):
        claimed = claim(*args, **kwargs)
        backend.snapshot = replace(backend.snapshot, **change)
        return claimed
    monkeypatch.setattr(service.journal, 'claim', changed_after_claim)
    result = service.submit(REQUEST)
    assert result['status'] == 'rejected'
    assert result['result'] == {'reason': reason, 'dispatched': False}
    assert not backend.dispatched and not service._pending
    assert service.submit(REQUEST) == result


def test_commit_latency_cannot_send_an_expired_request(setup, monkeypatch):
    service, backend = setup
    now = [10.]
    service.clock = lambda: now[0]
    claim = service.journal.claim
    def delayed_claim(*args, **kwargs):
        claimed = claim(*args, **kwargs)
        now[0] = REQUEST['deadline']
        return claimed
    monkeypatch.setattr(service.journal, 'claim', delayed_claim)
    result = service.submit(REQUEST)
    assert result['status'] == 'rejected'
    assert result['result'] == {'reason': 'deadline_expired_before_dispatch', 'dispatched': False}
    assert not backend.dispatched and not service._pending
    assert service.submit(REQUEST) == result
    assert [event['status'] for event in service.journal.events()] == ['accepted', 'running', 'rejected']


@pytest.mark.parametrize('failure', ['rejected', 'exception'])
def test_external_admission_is_rechecked_after_commit(setup, monkeypatch, failure):
    service, backend = setup
    validate = backend.validate
    calls = []
    def changing_admission(*args):
        calls.append(1)
        if len(calls) == 2:
            if failure == 'rejected':
                raise RequestRejected('clearance_no_longer_qualified')
            raise OSError('evidence source unavailable')
        return validate(*args)
    monkeypatch.setattr(backend, 'validate', changing_admission)
    result = service.submit(REQUEST)
    assert result['status'] == ('rejected' if failure == 'rejected' else 'failed')
    assert result['result'] == {'reason': 'clearance_no_longer_qualified' if failure == 'rejected'
                                else 'dispatch_admission_unavailable', 'dispatched': False}
    assert len(calls) == 2 and not backend.dispatched and not service._pending
    assert service.submit(REQUEST) == result and len(calls) == 2


def test_slow_second_validation_cannot_outlive_the_deadline(setup, monkeypatch):
    service, backend = setup
    now = [10.]
    service.clock = lambda: now[0]
    validate = backend.validate
    calls = []
    def delayed_admission(*args):
        calls.append(1)
        if len(calls) == 2:
            now[0] = REQUEST['deadline']
        return validate(*args)
    monkeypatch.setattr(backend, 'validate', delayed_admission)
    result = service.submit(REQUEST)
    assert result['status'] == 'rejected'
    assert result['result'] == {'reason': 'deadline_expired_before_dispatch', 'dispatched': False}
    assert not backend.dispatched


def test_changed_state_during_second_validation_is_not_dispatched(setup, monkeypatch):
    service, backend = setup
    validate = backend.validate
    calls = []
    def changed_admission(*args):
        calls.append(1)
        if len(calls) == 2:
            backend.snapshot = replace(backend.snapshot, state_version=2)
        return validate(*args)
    monkeypatch.setattr(backend, 'validate', changed_admission)
    result = service.submit(REQUEST)
    assert result['status'] == 'rejected'
    assert result['result'] == {'reason': 'state_changed_before_dispatch', 'dispatched': False}
    assert not backend.dispatched


def test_lease_loss_after_claim_leaves_intent_for_successor_without_dispatch(setup, monkeypatch):
    service, backend = setup
    claim = service.journal.claim
    check = service.lease.check
    def lost():
        raise OwnershipError('ownership withdrawn after commit')
    def lost_after_claim(*args, **kwargs):
        claimed = claim(*args, **kwargs)
        monkeypatch.setattr(service.lease, 'check', lost)
        return claimed
    monkeypatch.setattr(service.journal, 'claim', lost_after_claim)
    with pytest.raises(OwnershipError):
        service.submit(REQUEST)
    assert not backend.dispatched and not service._pending
    events = service.journal.events()
    assert [event['status'] for event in events] == ['accepted', 'running']
    assert service.journal.get(events[-1]['operation_id'])['status'] == 'running'
    monkeypatch.setattr(service.lease, 'check', check)
