"""Hybrid software experiment with a real sender and actual native prompt handlers."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['native_prompt', 'unified_prompt'])
def test_streamed_job_recovers_native_prompt_without_duplicate_moves(scenario):
    run = subprocess.run([sys.executable, 'tests/octoprint_virtual_smoke.py', '--service',
                          '--scenario', scenario], cwd=ROOT, capture_output=True,
                          text=True, timeout=45)
    assert run.returncode == 0, run.stderr[-5000:]
    evidence = json.loads(run.stdout)
    native = evidence['native_prompt_recovery']
    assert native['blocked_moves'] == 10 and native['blocked_interval_seconds'] >= .25
    assert native['prompt_frames'] == 3
    assert native['ordinary_line_number_unchanged_during_inspect_bind']
    assert native['stale_response_rejected_without_write']
    assert all(r['status'] == 'completed' for r in native['receipts'])
    assert native['receipts'][-1]['result']['resolution'] == 'firmware_user_wait_cleared'
    assert evidence['completed_simulated_moves_once_in_order'] == 80
    assert evidence['model_markers_in_order_once'] == 80
    assert evidence['physical_serial_open_attempts'] == 0
    assert evidence['shared_receive_copy_verified']

    if scenario == 'unified_prompt':
        assert evidence['shared_fault_invalidation_verified']
        assert native['one_journal_with_pending_job_pause']
        events = [c['response']['result']['events'] for c in evidence['tool_calls'] if c['request']['tool'] == 'printer.events'][-1]
        completed = [e for e in events if e['status'] == 'completed']
        assert len(completed) == 7  # stage/start/pause/resume and three prompt transactions
