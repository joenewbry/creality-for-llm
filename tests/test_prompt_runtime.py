"""Real firmware receive/process/receipt functions, native physical stubs."""
import subprocess
from printer_control.prompt_wire import PromptWireCommand
from test_firmware_host_actions import actions_binary

SESSION = 0x123456789abcdef0123456789abcdef0


def run(binary, batches):
    data = b''.join(batch + b'~' for batch in batches)
    result = subprocess.run([str(binary), 'runtime'], input=data, capture_output=True, check=True)
    lines = result.stdout.decode().splitlines()
    receipts = [{token[0]: token[1:] for token in line.split()[1:]} for line in lines if line.startswith('//llm:v1 ')]
    states = [line for line in lines if line.startswith('STATE ')]
    return receipts, states


def test_dispatch_receipts_retries_and_authority(actions_binary):
    bind = PromptWireCommand('bind', 1, SESSION).encode()
    respond = PromptWireCommand('respond', 2, SESSION, 1).encode()
    receipts, states = run(actions_binary, [bind, respond, respond,
        PromptWireCommand('respond', 2, SESSION, 1, 1).encode(), bind,
        PromptWireCommand('bind', 3, SESSION + 1).encode(),
        PromptWireCommand('inspect', 9).encode()])
    assert [r['C'] for r in receipts] == ['00', '00', '00', '82', '83', '02', '00']
    assert [r['D'] for r in receipts] == ['0', '0', '1', '0', '0', '0', '0']
    assert receipts[0]['A'] == '1' and receipts[0]['T'] == f'{SESSION:032x}'
    assert all(r['A'] == '0' for r in receipts[1:])
    assert states[0] == 'STATE 1 0'
    assert all(s == 'STATE 0 0' for s in states[1:])


def test_mailbox_overflow_has_explicit_loss_receipt(actions_binary):
    receipts, states = run(actions_binary, [
        PromptWireCommand('bind', 1, SESSION).encode() + PromptWireCommand('respond', 2, SESSION, 1).encode(),
        PromptWireCommand('inspect', 3).encode(),
    ])
    assert [(r['Q'], r['C']) for r in receipts] == [('00000000', '81'), ('00000001', '00'), ('00000003', '00')]
    assert all(s == 'STATE 1 0' for s in states)


def test_invalid_and_unbound_commands_cannot_poison_binding_sequence(actions_binary):
    receipts, states = run(actions_binary, [b'M876 V1 Bbad\n',
        PromptWireCommand('respond', 100, SESSION, 1).encode(),
        PromptWireCommand('bind', 1, SESSION).encode()])
    assert [r['C'] for r in receipts] == ['80', '01', '00']
    assert all(s == 'STATE 1 0' for s in states)


def test_receive_alone_never_executes(actions_binary):
    data = PromptWireCommand('bind', 1, SESSION).encode()
    output = subprocess.run([str(actions_binary), 'runtime'], input=data, capture_output=True, check=True)
    assert output.stdout == b''
