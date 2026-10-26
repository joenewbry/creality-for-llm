import json
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('scenario', ['active_disconnect', 'active_reset', 'active_old_resend'])
def test_active_stream_loss_never_replays_start_or_claims_completion(scenario):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, 'tests/octoprint_virtual_smoke.py',
                          '--service', '--scenario', scenario],
                         cwd=root, text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr[-5000:]
    evidence = json.loads(run.stdout)
    assert evidence['starting_state']['job_state'] == 'running'
    assert evidence['ending_state']['job_state'] == 'outcome_unknown'
    assert evidence['new_serial_writes_after_disconnect'] == 0
    assert evidence['original_start_receipt_preserved']
    assert not evidence['stop_confirmed'] and not evidence['reconnect_attempted']
    assert evidence['physical_serial_open_attempts'] == 0
    assert len(evidence['rejected_operations']) == 4
