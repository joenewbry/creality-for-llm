"""Real sender/virtual-firmware fault injection through journaled job tools."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['thermal_fault', 'disconnect', 'homing_fault', 'probe_fault', 'heating_fault'])
def test_paused_connection_fault_never_authorizes_resume_or_claims_stop(scenario):
    run = subprocess.run([sys.executable, 'tests/octoprint_virtual_smoke.py', '--service', '--scenario', scenario],
                         cwd=ROOT, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr[-5000:]
    evidence = json.loads(run.stdout)
    assert evidence['physical_serial_open_attempts'] == 0
    statuses = [item['response']['result'] for item in evidence['tool_calls']
                if item['request']['tool'] == 'job.inspect']
    final = statuses[-1]
    assert final['job_state'] == 'outcome_unknown' and not final['resumable']
    assert final['automatic_fault_recovery_supported'] is False
    if scenario == 'thermal_fault':
        assert 'Thermal Runaway' in final['blocking_fault']['message']
    if scenario.endswith('_fault') and scenario != 'thermal_fault':
        injection = evidence['fault_injection']
        assert injection['scope'] == 'synthetic_serial_report_while_paused'
        assert injection['firmware_fault_detection_tested'] is False
        assert injection['message'] in final['blocking_fault']['message']
    results = [r for r in evidence['service_receipts'] if r['request_id'].endswith('-after-fault')]
    assert len(results) == 2 and all(r['status'] == 'rejected' for r in results)
    assert 0 < evidence['completed_simulated_moves_once_in_order'] < 80
