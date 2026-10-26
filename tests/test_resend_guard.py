import pytest
from printer_control.resend_guard import VirtualResendGuard


def ready():
    writes = []
    def write(frame):
        writes.append(frame)
        return len(frame)
    guard = VirtualResendGuard(write)
    guard.write(b'N0 M110 N0*125\n')
    guard.received('ok\n')
    guard.write(b'N1 G1 X1*0\n')
    guard.received('ok\n')
    return guard, writes


def test_current_resend_keeps_acknowledgment_credit_separate():
    guard, writes = ready()
    frame = b'N2 G1 X2*0\n'
    guard.write(frame)
    assert guard.received('Resend:2\n') == 'Resend:2\n'
    guard.write(frame)  # can race before the resend's synthetic ok is read
    guard.received('ok\n')
    assert guard.acknowledged == 1
    guard.received('ok\n')
    assert guard.acknowledged == 2
    assert not guard.fault


@pytest.mark.parametrize('requested', [0, 1, 3, 999])
def test_old_or_unsent_resend_fault_blocks_writes_but_allows_stop(requested):
    guard, writes = ready()
    guard.write(b'N2 G1 X2*0\n')
    assert guard.received(f'Resend:{requested}\n').startswith('Error:Host')
    count = len(writes)
    with pytest.raises(OSError): guard.write(b'N1 G1 X1*0\n')
    assert len(writes) == count
    guard.received('start\n')
    assert guard.fault  # reset is not recovery authorization
    assert guard.write(b'M112\n') == 5


def test_no_acknowledged_baseline_cannot_authorize_resend():
    guard = VirtualResendGuard(lambda frame: len(frame))
    guard.write(b'N1 G1 X1*0\n')
    assert guard.received('Resend:1\n').startswith('Error:Host')


def test_prompt_receipt_lane_does_not_consume_job_ack_slot():
    guard, _ = ready()
    guard.write(b'M876 V1 I Q00000001\n')
    assert not guard.pending
    guard.write(b'N2 M114*0\n')
    guard.received('ok\n')
    assert guard.acknowledged == 2


def test_short_write_and_bounded_queue_invalidate_boundary():
    guard = VirtualResendGuard(lambda frame: 0)
    with pytest.raises(OSError): guard.write(b'N1 G1 X1*0\n')
    assert guard.fault == 'unconfirmed_write'
    guard = VirtualResendGuard(lambda frame: len(frame))
    for _ in range(256): guard.write(b'M105\n')
    with pytest.raises(OSError): guard.write(b'M105\n')
    assert guard.fault == 'acknowledgment_window_overflow'


def test_multiple_outstanding_commands_do_not_guess_acknowledgment_order():
    guard, _ = ready()
    guard.write(b'N2 G1 X2*0\n')
    guard.write(b'N3 G1 X3*0\n')
    assert guard.received('Resend:2\n').startswith('Error:Host')
    assert guard.fault == 'resend_outside_unacknowledged_window'
