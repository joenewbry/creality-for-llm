"""Abrupt host death at durable operation boundaries; no device is opened.

The append-only dispatch witness is a simulated external effect, independent
of SQLite. SIGKILL deliberately skips backend.close and Python cleanup.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

import pytest

from printer_control.ownership import OwnershipError
from printer_control.service import OperationService, Outcome
from test_service import FakeBackend, REQUEST


BOUNDARIES = ('accepted', 'claimed', 'dispatched', 'completion_transaction', 'completed')


def run_child(path, boundary):
    witness = Path(path).with_suffix('.dispatch')

    def reached(name):
        if name == boundary:
            print(json.dumps({'boundary': name}), flush=True)
            # Parent kills us while this pipe is open. Never run cleanup.
            sys.stdin.read()
            raise RuntimeError('parent must kill child, not release the boundary')

    class WitnessBackend(FakeBackend):
        def dispatch(self, operation_id, operation, arguments, *, deadline):
            with witness.open('ab') as output:
                output.write((operation_id + '\n').encode())
                output.flush()
                os.fsync(output.fileno())
            reached('dispatched')
            self.outcomes[operation_id] = Outcome('completed', {
                'evidence': 'software-only pause boundary', 'resumable': False,
            })

    service = OperationService(path, WitnessBackend(), clock=lambda: 10)
    for method, name in (('accept', 'accepted'), ('claim', 'claimed'), ('finish', 'completed')):
        original = getattr(service.journal, method)

        def wrapped(*args, original=original, name=name, **kwargs):
            result = original(*args, **kwargs)
            reached(name)
            return result

        setattr(service.journal, method, wrapped)

    # finish() updates the row and then appends its event in the SAME SQLite
    # transaction. Kill after that update but before commit to test rollback.
    original_event = service.journal._event

    def event(db, operation_id, status, now, result):
        original_event(db, operation_id, status, now, result)
        if status == 'completed':
            reached('completion_transaction')

    service.journal._event = event
    receipt = service.submit(REQUEST)
    service.poll(receipt['operation_id'])
    raise RuntimeError('selected boundary was not reached')


@pytest.mark.parametrize('boundary', BOUNDARIES)
def test_sigkill_never_replays_uncertain_pause_or_partial_completion(tmp_path, boundary):
    path = tmp_path / 'journal.sqlite'
    env = dict(os.environ)
    root = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = os.pathsep.join((str(root / 'tests'), str(root)))
    child = subprocess.Popen(
        [sys.executable, '-c',
         'from test_crash_recovery import run_child; import sys; run_child(*sys.argv[1:])',
         str(path), boundary],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=root,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), 'child did not reach selected boundary'
            line = child.stdout.readline()
        assert line, 'child exited before selected boundary'
        assert json.loads(line) == {'boundary': boundary}
        with pytest.raises(OwnershipError):
            OperationService(path, FakeBackend(), clock=lambda: 11)
        child.kill()
        assert child.wait(timeout=5) < 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()

    replacement = FakeBackend()
    with OperationService(path, replacement, clock=lambda: 11) as restarted:
        receipt = restarted.submit(REQUEST)
        terminal = 'completed' if boundary == 'completed' else 'outcome_unknown'
        assert receipt['status'] == terminal
        assert restarted.recovered == (boundary != 'completed')
        assert restarted.poll(receipt['operation_id']) == receipt
        assert restarted.submit(REQUEST) == receipt
        assert replacement.dispatched == []
        if terminal == 'outcome_unknown':
            assert receipt['result'] == {'reason': 'host_restarted', 'requires_reconciliation': True}
        else:
            assert receipt['result']['resumable'] is False
        statuses = [e['status'] for e in restarted.journal.events()]
        expected = ['accepted']
        if boundary != 'accepted':
            expected.append('running')
        assert statuses == expected + [terminal]

    witness = path.with_suffix('.dispatch')
    expected_writes = 0 if boundary in ('accepted', 'claimed') else 1
    writes = witness.read_text().splitlines() if witness.exists() else []
    assert len(writes) == expected_writes
    if writes:
        assert writes == [receipt['operation_id']]
    # A second startup must not append another recovery event or change results.
    with OperationService(path, FakeBackend(), clock=lambda: 12) as again:
        assert again.recovered == 0
        assert again.submit(REQUEST) == receipt
        assert [e['status'] for e in again.journal.events()] == statuses
