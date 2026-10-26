"""Exercise policy expiry and clock correction through the real virtual sender."""
import json
from pathlib import Path
import subprocess
import sys


def test_expired_pause_stays_invalid_after_clock_correction():
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, 'tests/octoprint_virtual_smoke.py',
                          '--service', '--scenario', 'expired'],
                         cwd=root, text=True, capture_output=True, timeout=30)
    assert run.returncode == 0, run.stderr + run.stdout[-2000:]
    evidence = json.loads(run.stdout)
    before, expired, corrected = evidence['pause_policy_observations']
    assert before['resumable']
    assert not expired['resumable'] and not corrected['resumable']
    assert expired['state_version'] > before['state_version']
    assert corrected['pause_policy']['remaining_seconds'] == 0
    assert evidence['policy_clock_shift_sequence_seconds'] == [0, 61, 0]
    assert evidence['physical_serial_open_attempts'] == 0
