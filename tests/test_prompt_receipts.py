import subprocess
import pytest
from printer_control.prompt_wire import PromptWireCommand, PromptWireReceipt, assess_prompt_receipt
from printer_control.protocol import ProtocolObserver
from test_firmware_host_actions import actions_binary

SESSION = 0x123456789abcdef0123456789abcdef0
LINE = f'//llm:v1 Q00000002 C00 D0 T{SESSION:032x} P00000001 A0 H0 B00 R00 W0'


def assess(line=LINE, **kwargs):
    options = dict(expected_connection='c1', current_connection='c1', deadline=20., observed_at=10.)
    options.update(kwargs)
    return assess_prompt_receipt(PromptWireCommand('respond', 2, SESSION, 1), PromptWireReceipt.decode(line), **options)


def test_handler_acceptance_is_not_physical_completion():
    result = assess()
    assert result.outcome == 'accepted' and result.reason == 'protocol_acceptance_only'
    assert assess(LINE.replace('D0', 'D1')).outcome == 'accepted'


@pytest.mark.parametrize('code', [1, 2, 3, 4, 5, 6, 7, 8, 9])
def test_explicit_correlated_rejection(code):
    assert assess(LINE.replace('C00', f'C{code:02x}')).outcome == 'rejected'


@pytest.mark.parametrize('code', ['82', '83'])
def test_retry_rejection_does_not_disprove_prior_execution(code):
    assert assess(LINE.replace('C00', 'C' + code)).outcome == 'outcome_unknown'


@pytest.mark.parametrize('code', ['80', '81'])
def test_uncorrelated_loss_needs_reconciliation(code):
    assert assess(LINE.replace('C00', 'C' + code).replace('Q00000002', 'Q00000000')).outcome == 'outcome_unknown'


def test_wrong_correlation_session_connection_and_stale_evidence():
    assert assess(LINE.replace('Q00000002', 'Q00000003')).outcome == 'unrelated'
    assert assess(LINE.replace(f'T{SESSION:032x}', 'T' + '0'*32)).outcome == 'outcome_unknown'
    assert assess(current_connection='c2').outcome == 'outcome_unknown'
    assert assess(observed_at=21.).outcome == 'outcome_unknown'
    assert assess(observed_at=20.).outcome == 'outcome_unknown'
    assert assess(expected_connection=1, current_connection=1).outcome == 'outcome_unknown'
    for value in (float('nan'), float('inf'), True, '10'):
        assert assess(observed_at=value).outcome == 'outcome_unknown'


@pytest.mark.parametrize('line', [
    'ok', LINE + ' ', ' ' + LINE, LINE + '\n', LINE + ' C00', LINE.replace('v1', 'v2'),
    LINE.replace('C00', 'Cff'), LINE.replace('D0', 'D2'), LINE.replace('A0', 'A1'),
    LINE.replace('H0', 'H1'), LINE.replace('B00', 'B01'), LINE.replace('R00', 'R02'),
    LINE.replace('Q00000002', 'Q00000000'), LINE.replace('C00', 'C80'),
    LINE.replace('C00 D0', 'C82 D1'), LINE.replace('abcdef', 'ABCDEF'),
    LINE.replace('A0 H0 B00 R00', 'A1 H1 B01 R06'), 'x' * 100000,
])
def test_malformed_or_inconsistent_receipt_is_not_accepted(line):
    with pytest.raises(ValueError):
        PromptWireReceipt.decode(line)


def test_every_truncation_is_invalid():
    for end in range(len(LINE)):
        with pytest.raises(ValueError):
            PromptWireReceipt.decode(LINE[:end])


def test_observer_retains_strict_receipt_whitespace_and_no_automatic_capability():
    observer = ProtocolObserver('c1')
    event = observer.feed((LINE + '\n').encode(), 10.)[0]
    assert event.kind == 'prompt_receipt' and event.data['request'] == 2
    assert event.generation == 'c1' and not observer.capabilities
    for malformed in (' ' + LINE, LINE + ' ', LINE.replace('v1', 'v2')):
        events = observer.feed((malformed + '\n').encode(), 11.)
        assert events[0].kind == 'protocol_error'
    assert observer.feed(b'ok\n', 12.)[0].kind == 'ack'


def test_actual_firmware_receipts_through_host_observer_and_correlation(actions_binary):
    bind = PromptWireCommand('bind', 1, SESSION)
    respond = PromptWireCommand('respond', 2, SESSION, 1)
    payload = bind.encode() + b'~' + respond.encode() + b'~' + respond.encode() + b'~'
    output = subprocess.run([str(actions_binary), 'runtime'], input=payload, capture_output=True, check=True).stdout
    observer = ProtocolObserver('live-native-connection')
    events = []
    # Byte-by-byte transport fragmentation must retain exact receipts.
    for byte in output:
        events.extend(observer.feed(bytes([byte]), 10.))
    receipts = [PromptWireReceipt(**event.data) for event in events if event.kind == 'prompt_receipt']
    assert len(receipts) == 3
    assert receipts[0].active and receipts[0].shown and receipts[0].buttons == 1
    assert not receipts[1].active and receipts[2].duplicate
    for command, receipt in zip((bind, respond, respond), receipts):
        result = assess_prompt_receipt(command, receipt, expected_connection=observer.generation,
            current_connection=observer.generation, deadline=20., observed_at=10.)
        assert result.outcome == 'accepted'
    old_generation = observer.generation
    observer.reset(11.)
    assert assess_prompt_receipt(respond, receipts[-1], expected_connection=old_generation,
        current_connection=observer.generation, deadline=20., observed_at=12.).outcome == 'outcome_unknown'
