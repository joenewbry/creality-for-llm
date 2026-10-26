"""Receive copying must preserve sender replies and make loss explicit."""
import pytest
from printer_control.shared_receiver import SharedReceiveBuffer


def test_exact_order_and_fragmentation_without_ack_injection():
    receiver = SharedReceiveBuffer()
    chunks = ['ok\n', '//llm:v1 Q0000', '0001 C00\n', 'Error:Thermal Runaway\n', 'Resend: 42\n']
    for chunk in chunks:
        assert receiver.received_hook(None, chunk) is chunk
    assert receiver.drain() == ''.join(chunks).encode()
    assert receiver.drain() == b''


@pytest.mark.parametrize('line', ['x'*129, 'ok T:20°C\n', None, b'ok\n'])
def test_loss_invalidates_entire_copy_but_never_changes_sender_input(line):
    receiver = SharedReceiveBuffer(capacity=128)
    receiver.received_hook(None, 'old snapshot\n')
    assert receiver.received_hook(None, line) is line
    assert receiver.fault
    with pytest.raises(OSError): receiver.drain()
    assert receiver.received_hook(None, 'ok\n') == 'ok\n'
    with pytest.raises(OSError): receiver.drain()


def test_capacity_is_restored_only_by_lossless_drain():
    receiver = SharedReceiveBuffer(capacity=128)
    receiver.received_hook(None, 'a'*128)
    assert receiver.drain() == b'a'*128
    receiver.received_hook(None, 'b'*128)
    receiver.received_hook(None, 'c')
    with pytest.raises(OSError): receiver.drain()


def test_close_discards_queued_authority_and_cannot_be_revived():
    receiver = SharedReceiveBuffer()
    receiver.received_hook(None, '//llm:v1 stale receipt\n')
    receiver.close()
    assert receiver.fault == 'receive_connection_closed'
    receiver.received_hook(None, 'start\n')
    with pytest.raises(OSError): receiver.drain()


def test_concurrent_drain_preserves_stream_order():
    import threading
    receiver = SharedReceiveBuffer()
    first, release = threading.Event(), threading.Event()
    def producer():
        for i in range(100):
            receiver.received_hook(None, f'{i}\n')
            if i == 49:
                first.set()
                assert release.wait(5)
    thread = threading.Thread(target=producer)
    thread.start(); assert first.wait(5)
    before = receiver.drain()
    release.set(); thread.join(5)
    assert not thread.is_alive()
    assert before+receiver.drain() == ''.join(f'{i}\n' for i in range(100)).encode()


def test_observed_drain_preserves_oldest_arrival_and_empty_has_no_time():
    now=[10.]
    receiver=SharedReceiveBuffer(clock=lambda:now[0])
    receiver.received_hook(None,'first\n')
    now[0]=12.;receiver.received_hook(None,'second\n')
    now[0]=20.;batch=receiver.drain_observed()
    assert batch.data==b'first\nsecond\n' and batch.observed_at==10.
    assert receiver.drain_observed().observed_at is None
    receiver.received_hook(None,'third\n')
    assert receiver.drain_observed().observed_at==20.


@pytest.mark.parametrize('bad',[True,None,float('nan'),float('inf'),9.])
def test_invalid_or_backward_arrival_clock_discards_buffer_and_preserves_sender_input(bad):
    now=[10.];receiver=SharedReceiveBuffer(clock=lambda:now[0])
    receiver.received_hook(None,'old\n');now[0]=bad
    line='new\n';assert receiver.received_hook(None,line) is line
    assert receiver.fault=='receive_copy_clock_invalid'
    with pytest.raises(OSError):receiver.drain_observed()
    now[0]=30.;receiver.received_hook(None,'later\n')
    with pytest.raises(OSError):receiver.drain_observed()
