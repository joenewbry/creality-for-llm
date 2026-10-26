import json
from pathlib import Path
import subprocess
import sys


def test_streamed_hot_simulation_pauses_restores_and_resumes_exactly_once():
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario','hot_resume'],
        cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-3000:]
    result=json.loads(run.stdout)
    assert result['all_job_moves_once_in_order']
    assert result['physical_serial_open_attempts']==0 and not result['physical_qualified']
    assert result['scripted_temperatures'] and result['virtual_continuity_proofs']
    assert result['restore_receipt']['status']=='resumed'
    assert len(result['thermal_effects'])==2
    assert all(effect['status']=='completed' for effect in result['thermal_effects'])
    assert [r['request_id'].rsplit('-',1)[0] for r in result['operation_receipts']]==['job.stage','job.start','job.pause','job.resume']


def test_temperature_loss_after_restoration_keeps_streamed_job_paused():
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario','hot_resume_temp_loss'],
        cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-3000:]
    result=json.loads(run.stdout)
    assert result['physical_serial_open_attempts']==0
    assert result['job_still_paused'] and result['new_job_moves_after_temperature_loss']==0
    assert result['thermal_state']=='invalid' and result['restoration_invalidated']


def test_two_hot_pauses_retire_old_authority_and_resume_same_job():
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario','hot_resume_twice'],
        cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-3000:]
    result=json.loads(run.stdout)
    assert result['pause_count']==2 and result['restoration_only_moves']==2
    assert result['all_job_moves_once_in_order'] and result['physical_serial_open_attempts']==0
    assert len(result['thermal_effects'])==4
    first,second=result['cycles']
    assert first['checkpoint_id']!=second['checkpoint_id'] and first['cursor']<second['cursor']
    assert all(c['monitor_closed'] and c['thermal_retired'] and c['transport_retired'] for c in result['cycles'])
    assert all(c['restore_receipt']['status']=='resumed' for c in result['cycles'])
    assert [s['phase'] for s in result['pause_status_trace']]==['paused','standby','thermal_ready_restore_required','restored','inactive']*2


import pytest


@pytest.mark.parametrize('fault',['disconnect','reset','missing_receipt'])
def test_standby_fault_invalidates_hot_resume_without_reheat_or_replay(fault):
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario','hot_pause_'+fault],
        cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-4000:]
    result=json.loads(run.stdout)
    assert result['thermal_state']=='invalid'
    assert result['monitor_closed'] and not result['monitor_alive']
    assert result['new_job_moves_after_fault']==result['new_heater_commands_after_invalidation']==0
    assert result['physical_serial_open_attempts']==0
    assert result['generation_changed']==(fault!='missing_receipt')
    assert not result['heater_shutdown_confirmed']
    assert result['standby_target_writes']==2
    assert result['pause_status']['phase']=='recovery_required'


@pytest.mark.parametrize('scenario',['hot_resume_managed','hot_resume_managed_timeout'])
def test_one_typed_resume_coordinates_preparation_or_stops_at_deadline(scenario):
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario',scenario],
        cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-4000:]
    result=json.loads(run.stdout)
    assert result['physical_serial_open_attempts']==0
    if scenario.endswith('timeout'):
        assert result['resume_receipt']['status']=='outcome_unknown'
        assert result['job_still_paused'] and result['monitor_closed']
        assert result['thermal_state']=='invalid' and result['new_job_moves_after_fault']==0
    else:
        assert result['all_job_moves_once_in_order']
        assert result['restore_receipt']['status']=='resumed'
        assert len(result['thermal_effects'])==2
        assert result['pause_status_trace'][1]['resume_request_available']


@pytest.mark.parametrize('interruption',['cancel','cancel_missing_marker','shutdown','stop','stop_lost'])
def test_interruption_during_reheat_stops_worker_before_later_temperature_recovery(interruption):
    run=subprocess.run([sys.executable,'tests/octoprint_virtual_smoke.py','--service','--scenario',
        'hot_resume_managed_'+interruption],cwd=Path(__file__).resolve().parents[1],
        text=True,capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr+run.stdout[-4000:]
    result=json.loads(run.stdout)
    assert result['physical_serial_open_attempts']==0
    assert result['worker_stopped'] and result['monitor_closed']
    assert result['thermal_state']=='invalid' and result['new_job_moves_after_fault']==0
    assert result['heater_shutdown_confirmed']==(interruption in ('cancel','stop'))
    assert result['service_closed']==(interruption=='shutdown')
    if interruption.startswith(('cancel','stop')):
        expected='completed' if interruption in ('cancel','stop') else 'outcome_unknown'
        assert result['cancel_receipt']['status']==expected
