"""Actual serial sender targets; scripted temperatures and no physical access."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('scenario', ['thermal_serial', 'thermal_serial_missing_receipt', 'thermal_serial_monitor'])
def test_thermal_effects_route_once_and_require_correlated_serial_evidence(scenario):
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario',scenario],
        cwd=root,text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-3000:]
    evidence=json.loads(run.stdout)
    assert evidence['physical_serial_open_attempts']==0
    assert evidence['executed_moves']==0
    assert not evidence['streamed_hot_print_resume_tested']
    assert len(evidence['effects'])==(1 if scenario.endswith('missing_receipt') else 2)
