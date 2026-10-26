import json
from pathlib import Path
import subprocess
import sys


def test_restoration_reaches_extended_virtual_handlers_through_sender():
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario','restore_serial'],
                       cwd=root,text=True,capture_output=True,timeout=30)
    assert run.returncode == 0, run.stderr + run.stdout[-3000:]
    evidence=json.loads(run.stdout)
    assert evidence['physical_serial_open_attempts'] == 0
    assert evidence['position_before'] == evidence['position_after']
    assert evidence['epoch_delta'] == 12
    assert evidence['job_still_frozen'] and evidence['old_epoch_rejected']
    assert not evidence['streamed_hot_print_resume_tested']
    assert evidence['observed_modal']['e_relative'] is True
    assert evidence['observed_modal']['fan_pwm'] == 128

    assert evidence['restoration_receipt_status'] == 'completed'
    assert not evidence['resume_authorized']
