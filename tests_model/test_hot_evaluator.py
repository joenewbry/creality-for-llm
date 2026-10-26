import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from model_hot_scenario import evaluate_hot


def evidence():
    status=dict(job_state='completed',physical_printer=False,simulation_evidence=dict(
        physical_serial_open_attempts=0,job_moves=80,expected_job_moves=80,model_moves=82,all_job_moves_once_in_order=True))
    status['setup']={'temperature_observations':{heater:{'target_celsius':0} for heater in ('nozzle','bed')}}
    kinds=['virtual_file_selected','virtual_sender_started','virtual_sender_pause_barrier_and_position','virtual_in_place_resume']
    events={'events':[dict(status='completed',detail={'evidence_type':kind}) for kind in kinds]}
    paused=dict(job_state='paused',pause_policy={'thermal_state':'standby'},simulation_evidence={'job_moves':10})
    observations=[dict(at=t,status=copy.deepcopy(paused)) for t in (1.,2.)]
    pause=dict(phase='inactive',blockers=[],physical_pause_qualified=False,monitor={'running':False},
        resume_workflow={'active':False},thermal={'active':False,'durable_latest':dict(state='consumed',checkpoint_id='checkpoint',effects=[
            dict(status='completed',nozzle_target=t,bed_target=60) for t in (100,200)])},
        restoration={'active':False,'durable_latest':dict(state='resumed',invalidated=None,checkpoint_id='checkpoint')})
    return status,events,observations,pause


def test_accept_independent_hot_workflow_evidence():
    assert evaluate_hot(*evidence())


@pytest.mark.parametrize('fault',['early','physical','moving','brief','no_standby','extra_resume',
    'missing_effect','unconfirmed_effect','different_checkpoint','invalid_restore','monitor_running','duplicate_moves','missing_targets','hot_bed','active_thermal'])
def test_reject_missing_or_contradictory_hot_evidence(fault):
    status,events,observations,pause=evidence()
    if fault=='early':status['job_state']='running'
    if fault=='physical':status['simulation_evidence']['physical_serial_open_attempts']=1
    if fault=='moving':observations[-1]['status']['simulation_evidence']['job_moves']=11
    if fault=='brief':observations[-1]['at']=1.1
    if fault=='no_standby':observations[0]['status']['pause_policy']['thermal_state']='holding'
    if fault=='extra_resume':events['events'].append(events['events'][-1])
    if fault=='missing_effect':pause['thermal']['durable_latest']['effects'].pop()
    if fault=='unconfirmed_effect':pause['thermal']['durable_latest']['effects'][0]['status']='outcome_unknown'
    if fault=='different_checkpoint':pause['restoration']['durable_latest']['checkpoint_id']='other'
    if fault=='invalid_restore':pause['restoration']['durable_latest']['invalidated']='lost_position'
    if fault=='monitor_running':pause['monitor']['running']=True
    if fault=='duplicate_moves':status['simulation_evidence']['model_moves']=83
    if fault=='missing_targets':status.pop('setup')
    if fault=='hot_bed':status['setup']['temperature_observations']['bed']['target_celsius']=60
    if fault=='active_thermal':pause['thermal']['active']=True
    assert not evaluate_hot(status,events,observations,pause)
