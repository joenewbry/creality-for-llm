"""Real sender resend handling must preserve the executed virtual job."""
import json
from pathlib import Path
import re
import subprocess
import sys
import pytest


@pytest.mark.parametrize('scenario', ['resend_checksum', 'resend_lineno'])
def test_serial_resend_then_pause_resume_executes_each_move_once(scenario):
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, 'tests/octoprint_virtual_smoke.py',
                          '--service', '--scenario', scenario],
                         cwd=root, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr[-5000:]
    result = json.loads(run.stdout)
    assert result['completed_simulated_moves_once_in_order'] == 80
    assert result['model_markers_in_order_once'] == 80
    assert result['duplicate_pause_checked'] and result['duplicate_resume_checked']
    assert result['physical_serial_open_attempts'] == 0
    assert result['resend_reports']
    transcript = (root/'evidence'/f'octoprint-virtual-service-{scenario}.log').read_text()
    requested = {int(n) for n in re.findall(r'Recv: Resend:\s*(\d+)', transcript)}
    frames = re.findall(r'^Send: (N(\d+) .*)$', transcript, re.M)
    assert requested
    for number in requested:
        repeated = [frame for frame, n in frames if int(n) == number]
        assert len(repeated) >= 2 and len(set(repeated)) == 1
    assert result['resend_injection'] == 'reject_next_unexecuted_numbered_line'
    assert result['arbitrary_old_line_resend_qualified'] is False
    assert any(r['result'].get('resumable') for r in result['service_receipts'])
