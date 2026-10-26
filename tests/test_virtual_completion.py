"""EOF is not evidence of completed machine movement."""
import queue
from types import SimpleNamespace

from printer_control.octoprint_virtual import OctoPrintVirtualBackend


def fixture_backend():
    backend = OctoPrintVirtualBackend.__new__(OctoPrintVirtualBackend)
    backend._events = queue.SimpleQueue()
    backend._job_phase = 'running'
    backend._fault = None
    backend._printing_transitions = 0
    moves = queue.Queue()
    state = [3]
    backend.sender = SimpleNamespace(STATE_PRINTING=1, STATE_PAUSED=2,
        STATE_OPERATIONAL=4, getState=lambda: state[0],
        _serial=SimpleNamespace(buffered=moves))
    return backend, state, moves


def test_eof_requires_operational_state_and_completed_movement():
    backend, state, moves = fixture_backend()
    backend._events.put(('completed', None))
    backend._drain_events()
    assert backend._job_phase == 'completing'  # empty queue before final commands arrive
    moves.put('last move')
    moves.get()
    state[0] = 4
    backend._drain_events()
    assert backend._job_phase == 'completing'  # dequeued does not mean executed
    moves.task_done()
    backend._drain_events()
    assert backend._job_phase == 'completed'


def test_disconnect_does_not_upgrade_pending_completion():
    backend, state, moves = fixture_backend()
    backend._events.put(('completed', None))
    state[0] = 5
    backend.sender._serial = None
    backend._drain_events()
    assert backend._job_phase == 'completing'


def test_failure_overrides_pending_completion():
    backend, state, moves = fixture_backend()
    backend._events.put(('completed', None))
    backend._events.put(('failed', None))
    state[0] = 4
    backend._drain_events()
    assert backend._job_phase == 'failed'


def test_fault_latches_over_later_completion_and_invalidates_checkpoint():
    backend, state, moves = fixture_backend()
    backend.clock = lambda: 12.
    backend.checkpoint = object()
    backend._events.put(('fault', {'message': 'Thermal Runaway', 'reason': 'firmware'}))
    backend._events.put(('completed', None))
    state[0] = 4
    backend._drain_events()
    assert backend._job_phase == 'faulted'
    assert backend.checkpoint is None
    assert backend.resume_blockers() == ['observed_sender_fault_requires_recovery']
    assert backend._fault['observed_at'] == 12.
    backend._events.put(('state', backend.sender.STATE_PRINTING))
    backend._drain_events()
    assert backend._job_phase == 'faulted'


def test_error_callback_preserves_diagnostic_and_forwards_original():
    from printer_control.octoprint_virtual import _CallbackTap
    events, calls = queue.SimpleQueue(), []
    tap = _CallbackTap(SimpleNamespace(on_comm_error=lambda *a, **k: calls.append((a, k))), events)
    tap.on_comm_error('Thermal Runaway', 'firmware', consequence='disconnect')
    assert events.get_nowait() == ('fault', {'message': 'Thermal Runaway', 'reason': 'firmware'})
    assert calls == [(('Thermal Runaway', 'firmware'), {'consequence': 'disconnect'})]
