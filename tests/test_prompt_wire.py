"""Host encoding against the real C++ grammar and prompt handler.

The native stdin loop is a test dispatcher, not a firmware USB mailbox.
"""
import json
import subprocess
import pytest

from printer_control.prompt_wire import PromptWireCommand
from test_firmware_host_actions import actions_binary

SESSION = 0x123456789abcdef0123456789abcdef0


def exchange(binary, lines):
    result = subprocess.run([str(binary), 'wire'], input=b''.join(lines), capture_output=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_encoded_session_and_response_execute_actual_firmware_handler(actions_binary):
    replies = exchange(actions_binary, [
        PromptWireCommand('inspect', 1).encode(),
        PromptWireCommand('bind', 2, SESSION).encode(),
        PromptWireCommand('respond', 3, SESSION + 1, 1, 0).encode(),
        PromptWireCommand('respond', 4, SESSION, 2, 0).encode(),
        PromptWireCommand('respond', 5, SESSION, 1, 1).encode(),
        PromptWireCommand('respond', 6, SESSION, 1, 0).encode(),
        PromptWireCommand('respond', 7, SESSION, 1, 0).encode(),
    ])
    assert [r['request'] for r in replies] == list(range(1, 8))
    assert all(r['parsed'] for r in replies)
    assert all(r['waiting'] and r['active'] for r in replies[:5])
    assert all(r['result'] != 0 for r in replies[2:5])
    assert replies[5]['result'] == 0 and not replies[5]['waiting'] and not replies[5]['active']
    assert replies[6]['result'] != 0 and not replies[6]['active']


def test_malformed_frames_never_dispatch_or_partially_update_output(actions_binary):
    valid = PromptWireCommand('respond', 2, SESSION, 1, 0).encode()[:-1]
    assert len(valid) == 65
    malformed = [valid[:n] for n in range(len(valid))]
    malformed += [valid + suffix for suffix in (b' ', b'\r', b';comment', b'*12', b' Q00000003', b'\0')]
    malformed += [b'N1 ' + valid, valid.replace(b'V1', b'V2'), valid.replace(b' S00', b' S-1'),
                  valid.replace(b' P00000001', b' P00000000'), valid.replace(b' Q00000002', b' Q00000000'),
                  valid.replace(b'abcdef', b'ABCDEF'), valid.replace(b' R', b' X'),
                  valid.replace(f'{SESSION:032x}'.encode(), b'0' * 32), b'x' * 100000]
    # Every non-ASCII byte and NUL is invalid in every field position.
    malformed += [valid[:9] + bytes([byte]) + valid[10:] for byte in [0, *range(128, 256)]]
    lines = [PromptWireCommand('bind', 1, SESSION).encode()]
    lines += [line + b'\n' for line in malformed]
    lines += [valid + b'\n']
    replies = exchange(actions_binary, lines)
    assert len(replies) == len(lines)
    for reply in replies[1:-1]:
        assert reply == {'parsed': 0, 'request': 0, 'result': -1, 'active': 1, 'waiting': 1}
    assert replies[-1]['result'] == 0 and not replies[-1]['waiting']


@pytest.mark.parametrize('command', [
    PromptWireCommand('inspect', 0), PromptWireCommand('inspect', True),
    PromptWireCommand('inspect', 2**32), PromptWireCommand('inspect', 1, 1),
    PromptWireCommand('bind', 1), PromptWireCommand('bind', 1, -1),
    PromptWireCommand('bind', 1, 2**128), PromptWireCommand('bind', 1, 1, 1),
    PromptWireCommand('respond', 1, 1), PromptWireCommand('respond', 1, 1, 2**32),
    PromptWireCommand('respond', 1, 1, 1, -1), PromptWireCommand('respond', 1, 1, 1, 256),
    PromptWireCommand('respond', 1, 1, 1, True), PromptWireCommand('raw', 1),
])
def test_encoder_rejects_invalid_fields(command):
    with pytest.raises(ValueError):
        command.encode()


def test_maximum_width_fields_parse_without_overflow(actions_binary):
    replies = exchange(actions_binary, [
        PromptWireCommand('bind', 0xffffffff, (1 << 128) - 1).encode(),
        PromptWireCommand('respond', 0xffffffff, (1 << 128) - 1, 0xffffffff, 255).encode(),
    ])
    assert all(r['parsed'] and r['request'] == 0xffffffff for r in replies)
    assert replies[1]['result'] != 0 and replies[1]['waiting']


def test_truncated_final_frame_never_dispatches(actions_binary):
    replies = exchange(actions_binary, [
        PromptWireCommand('bind', 1, SESSION).encode(),
        PromptWireCommand('respond', 2, SESSION, 1).encode()[:-1],
    ])
    assert len(replies) == 1 and replies[0]['waiting']


def test_overflow_discards_through_delimiter_before_recovery(actions_binary):
    valid = PromptWireCommand('respond', 3, SESSION, 1).encode()
    replies = exchange(actions_binary, [
        PromptWireCommand('bind', 1, SESSION).encode(),
        b'x' * 200 + valid, valid,
    ])
    assert len(replies) == 3
    assert not replies[1]['parsed'] and replies[1]['waiting']
    assert replies[2]['parsed'] and not replies[2]['waiting']
