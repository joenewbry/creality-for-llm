import os
import select
import subprocess
import time
import pytest
from printer_control.prompt_channel import PromptChannel
from test_firmware_host_actions import actions_binary

SESSION = 0x123456789abcdef0123456789abcdef0


def receipt(request, *, session=0, result=0, active=True):
    return (f'//llm:v1 Q{request:08x} C{result:02x} D0 T{session:032x} P00000001 '
            f'A{int(active)} H{int(active)} B{int(active):02x} R{2 if active else 0:02x} W{int(active)}\n').encode()


def prepared():
    writes = []
    def write(data):
        writes.append(data)
        return len(data)
    channel = PromptChannel(write, 'c1', session=SESSION)
    channel.dispatch('inspect', now=1., deadline=10.)
    channel.feed(receipt(1), now=2.)
    channel.dispatch('bind', now=3., deadline=10.)
    channel.feed(receipt(2, session=SESSION), now=4.)
    return channel, writes


def test_discovery_binding_and_single_flight():
    writes = []
    channel = PromptChannel(lambda data: writes.append(data) or len(data), 'c1', session=SESSION)
    with pytest.raises(ValueError):
        channel.dispatch('bind', now=1., deadline=10.)
    assert not writes
    channel.dispatch('inspect', now=1., deadline=10.)
    with pytest.raises(ValueError):
        channel.dispatch('inspect', now=2., deadline=10.)
    channel.feed(b'ok\n', now=2.)
    assert channel.state == 'busy'
    channel.feed(receipt(99), now=2.)
    assert channel.state == 'busy'
    channel.feed(receipt(1), now=2.)
    assert channel.latest.status == 'accepted' and not channel.bound


@pytest.mark.parametrize('fault', ['partial', 'exception', 'unconfirmed'])
def test_write_failures_never_retry(fault):
    calls = []
    def writer(data):
        calls.append(data)
        if fault == 'exception':
            raise OSError('failure after possible write')
        return len(data)-1 if fault == 'partial' else None
    channel = PromptChannel(writer, 'c1')
    assert channel.dispatch('inspect', now=1., deadline=2.).status == 'outcome_unknown'
    with pytest.raises(ValueError):
        channel.dispatch('inspect', now=1., deadline=2.)
    assert len(calls) == 1


@pytest.mark.parametrize('fault', ['deadline', 'disconnect', 'overflow', 'malformed', 'reset', 'clock'])
def test_uncertain_transaction_invalidates_session_and_never_replays(fault):
    channel, writes = prepared()
    channel.dispatch('respond', prompt=1, now=5., deadline=8.)
    if fault == 'deadline': channel.tick(8.)
    elif fault == 'disconnect': channel.disconnect(6.)
    elif fault == 'overflow': channel.feed(receipt(0, session=SESSION, result=0x81), now=6.)
    elif fault == 'malformed': channel.feed(b'//llm:v1 broken\n', now=6.)
    elif fault == 'reset': channel.feed(b'start\n', now=6.)
    else:
        with pytest.raises(ValueError): channel.tick(4.)
    assert channel.latest.status == 'outcome_unknown' and not channel.bound
    channel.feed(receipt(3, session=SESSION, active=False), now=9.)
    assert channel.latest.status == 'outcome_unknown' and len(writes) == 3


def test_response_requires_fresh_current_prompt_and_rejection_requires_inspection():
    channel, writes = prepared()
    for prompt, choice in [(2,0), (1,1)]:
        with pytest.raises(ValueError):
            channel.dispatch('respond', prompt=prompt, choice=choice, now=5., deadline=10.)
    assert len(writes) == 2
    channel.dispatch('respond', prompt=1, now=5., deadline=10.)
    channel.feed(receipt(3, session=SESSION, result=9), now=6.)
    assert channel.latest.status == 'rejected'
    with pytest.raises(ValueError):
        channel.dispatch('respond', prompt=1, now=7., deadline=10.)
    channel.dispatch('inspect', now=7., deadline=10.)
    channel.feed(receipt(4, session=SESSION), now=8.)
    with pytest.raises(ValueError):
        channel.dispatch('respond', prompt=1, now=14., deadline=20.)


def test_existing_binding_does_not_authorize_reconnect():
    channel = PromptChannel(lambda data: len(data), 'new', session=SESSION)
    channel.dispatch('inspect', now=1., deadline=10.)
    channel.feed(receipt(1, session=SESSION), now=2.)
    with pytest.raises(ValueError): channel.dispatch('bind', now=3., deadline=10.)
    with pytest.raises(ValueError): channel.dispatch('respond', prompt=1, now=3., deadline=10.)


def test_host_channel_drives_live_native_firmware_dispatcher(actions_binary):
    process = subprocess.Popen([str(actions_binary), 'runtime'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    writes = []
    try:
        def writer(data):
            writes.append(data)
            process.stdin.write(data + b'~') # test-only main-idle scheduling hook
            process.stdin.flush()
            return len(data)
        from printer_control.shared_writer import SharedFrameWriter
        shared = SharedFrameWriter(writer)
        channel = PromptChannel(shared.write_prompt, 'native-process', session=SESSION)
        for now, kind in enumerate(('inspect', 'bind', 'respond'), start=1):
            channel.dispatch(kind, now=float(now), deadline=10., prompt=1 if kind == 'respond' else 0)
            data = b''
            end = time.monotonic() + 5
            while b'STATE ' not in data or not data.endswith(b'\n'):
                assert select.select([process.stdout], [], [], max(0, end-time.monotonic()))[0], 'native response timeout'
                chunk = os.read(process.stdout.fileno(), 4096)
                assert chunk, 'native process exited before receipt'
                data += chunk
            channel.feed(data, now=float(now))
            assert channel.latest.status == 'accepted'
        assert len(writes) == 3 and channel.latest.receipt.active is False
        assert channel.bound and channel.state == 'ready'
        assert channel.latest.reason == 'protocol_acceptance_only'
    finally:
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
        process.stdout.close(); process.stderr.close()


def test_reentrant_receipt_followed_by_write_error_is_uncertain():
    channel = None
    def writer(data):
        channel.feed(receipt(1), now=1.)
        raise OSError('transport failed after callback')
    channel = PromptChannel(writer, 'c1', session=SESSION)
    result = channel.dispatch('inspect', now=1., deadline=10.)
    assert result.status == 'outcome_unknown' and channel.state == 'uncertain'


def test_concurrent_writers_cannot_overlap():
    import threading
    entered, release = threading.Event(), threading.Event()
    writes, errors = [], []
    def writer(data):
        writes.append(data)
        entered.set()
        assert release.wait(5)
        return len(data)
    channel = PromptChannel(writer, 'c1')
    def send():
        try: channel.dispatch('inspect', now=1., deadline=10.)
        except ValueError as exc: errors.append(str(exc))
    first, second = threading.Thread(target=send), threading.Thread(target=send)
    first.start()
    assert entered.wait(5)
    second.start()
    release.set()
    first.join(5); second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert len(writes) == 1 and len(errors) == 1


@pytest.mark.parametrize('fault', [b'Error:Thermal Runaway\n', b'Error:Heating failed\n',
    b'Error:Printer halted. kill() called!\n', b'Error:Homing Failed\n',
    b'Error:Unrecognized fatal condition\n', b'ok T:-15.00 /0.00 B:17.34 /0.00\n'])
@pytest.mark.parametrize('position', ['idle', 'before_receipt', 'after_receipt', 'fragmented'])
def test_fault_observation_latches_and_cannot_be_cleared_by_receipts(fault, position):
    channel, writes = prepared()
    if position != 'idle':
        channel.dispatch('respond', prompt=1, now=5., deadline=10.)
    resolved = receipt(3, session=SESSION, active=False)
    if position == 'fragmented':
        for byte in fault:
            channel.feed(bytes([byte]), now=6.)
        channel.feed(resolved, now=6.)
    else:
        data = (resolved + fault if position == 'after_receipt' else fault + resolved)
        channel.feed(data, now=6.)
    assert channel.state == 'uncertain' and not channel.bound
    assert channel.latest.status == 'outcome_unknown'
    assert channel.snapshot() == (None, None)
    assert channel.blocker.kind == ('sensor_suspect' if b'T:-15' in fault else 'fault')
    reason = channel.invalidation_reason
    channel.feed(b'ok T:20.00 /0.00\n' + resolved, now=7.)
    assert channel.state == 'uncertain' and channel.invalidation_reason == reason
    count = len(writes)
    for operation in ('inspect', 'bind', 'respond'):
        with pytest.raises(ValueError):
            channel.dispatch(operation, prompt=1 if operation == 'respond' else 0, now=7., deadline=10.)
    assert len(writes) == count


def test_checksum_resend_and_busy_are_not_machine_faults():
    channel, writes = prepared()
    channel.feed(b'Error:checksum mismatch\nResend: 42\necho:busy: processing\n', now=5.)
    assert channel.state == 'ready' and channel.bound and channel.blocker is None
