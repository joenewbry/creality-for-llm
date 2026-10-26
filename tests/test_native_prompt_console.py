"""Exercise the real CLI over JSON pipes, with persistent operation receipts."""
import json
from pathlib import Path
import select
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def start(journal):
    return subprocess.Popen([sys.executable, str(ROOT / 'tests/native_prompt_console.py'),
                             '--journal', str(journal)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def call(process, tool, arguments):
    process.stdin.write(json.dumps({'id': tool, 'tool': tool, 'arguments': arguments}) + '\n')
    process.stdin.flush()
    assert select.select([process.stdout], [], [], 15)[0], 'console response timeout'
    line = process.stdout.readline()
    assert line, 'console exited before reply'
    reply = json.loads(line)
    assert reply['id'] == tool
    return reply


def close(process):
    process.stdin.close()
    try:
        code = process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(timeout=5)
        raise
    errors = process.stderr.read()
    process.stdout.close(); process.stderr.close()
    assert code == 0, errors


def test_cli_lifecycle_and_restart_keeps_prior_receipts(tmp_path):
    journal = tmp_path / 'nested/operations.sqlite'
    process = start(journal)
    last_request = None
    try:
        description = call(process, 'printer.describe', {})['result']
        assert description['physical_printer'] is False
        assert description['fixture'] == 'one_generic_user_wait'
        assert len(description['firmware_harness_sha256']) == 64
        for operation, arguments in [('prompt.inspect', {}), ('prompt.bind', {}),
                                     ('prompt.respond', {'prompt': 1, 'choice': 0})]:
            state = call(process, 'printer.status', {})['result']
            request = dict(request_id=operation, printer_id=state['printer_id'],
                connection_generation=state['connection_generation'], expected_state_version=state['state_version'],
                deadline=time.time()+10, arguments=arguments)
            result = call(process, operation, request)['result']
            end = time.monotonic()+5
            while result['status'] == 'running' and time.monotonic() < end:
                result = call(process, 'operation.get', {'operation_id': result['operation_id']})['result']
            assert result['status'] == 'completed'
            last_request = request
        assert result['result']['resolution'] == 'firmware_user_wait_cleared'
        assert 'error' in call(process, 'printer.raw', {'command': 'M108'})
    finally:
        close(process)
    successor = start(journal)
    try:
        state = call(successor, 'printer.status', {})['result']
        assert state['connection_generation'] != last_request['connection_generation']
        assert not state['session_bound'] and state['prompt_snapshot'] is None
        assert call(successor, 'prompt.respond', last_request)['result'] == result
        assert not call(successor, 'printer.status', {})['result']['session_bound']
    finally:
        close(successor)
