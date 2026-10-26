"""The independent evaluator must reject partial or misleading model success."""
import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from model_job_scenario import evaluate_job


def evidence():
    status = dict(job_state='completed', simulation_evidence=dict(completed_moves=80,
        expected_moves=80, moves_match_expected_prefix=True,
        all_moves_completed_once_in_order=True, physical_serial_open_attempts=0))
    kinds = ['virtual_file_selected', 'virtual_sender_started',
             'virtual_sender_pause_barrier_and_position', 'virtual_in_place_resume']
    events = {'events': [dict(status='completed', detail={'evidence_type': k}) for k in kinds]}
    paused = dict(job_state='paused', resumable=True, simulation_evidence={'completed_moves': 10})
    observations = [dict(at=t, status=copy.deepcopy(paused)) for t in (1., 2.)]
    return status, events, observations


def test_accept_complete_independent_evidence():
    assert evaluate_job(*evidence())


@pytest.mark.parametrize('fault', ['early', 'missing_pause', 'duplicate_resume', 'brief', 'moving', 'no_motion', 'physical', 'not_resumable'])
def test_reject_incomplete_or_unsafe_evidence(fault):
    status, events, observations = evidence()
    if fault == 'early': status['simulation_evidence']['completed_moves'] = 78
    if fault == 'missing_pause': events['events'].pop(2)
    if fault == 'duplicate_resume': events['events'].append(events['events'][-1])
    if fault == 'brief': observations[-1]['at'] = 1.1
    if fault == 'moving': observations[-1]['status']['simulation_evidence']['completed_moves'] = 11
    if fault == 'no_motion':
        for o in observations: o['status']['simulation_evidence']['completed_moves'] = 0
    if fault == 'physical': status['simulation_evidence']['physical_serial_open_attempts'] = 1
    if fault == 'not_resumable':
        for o in observations: o['status']['resumable'] = False
    assert not evaluate_job(status, events, observations)


def combined_evidence():
    status, events, observations = evidence()
    status.update(fixture_prompt_waiting=False, prompt={'channel_state': 'ready', 'prompt_snapshot': {'active': False, 'waiting': False}})
    events['events'].append({'status': 'completed', 'detail': {'resolution': 'firmware_user_wait_cleared'}})
    waiting = dict(fixture_prompt_waiting=True, simulation_evidence={'completed_moves': 10})
    observations = [dict(at=t, status=copy.deepcopy(waiting)) for t in (-2., -1.)]+observations
    return status, events, observations


def test_combined_evaluator_requires_both_job_and_prompt_evidence():
    from model_job_scenario import evaluate_combined
    assert evaluate_combined(*combined_evidence())


@pytest.mark.parametrize('fault', ['missing_resolution', 'duplicate_resolution', 'waiting', 'moving_while_blocked', 'uncertain', 'no_wait_observed'])
def test_combined_evaluator_rejects_missing_or_contradictory_evidence(fault):
    from model_job_scenario import evaluate_combined
    status, events, observations = combined_evidence()
    if fault == 'missing_resolution': events['events'].pop()
    if fault == 'duplicate_resolution': events['events'].append(events['events'][-1])
    if fault == 'waiting': status['prompt']['prompt_snapshot']['waiting'] = True
    if fault == 'moving_while_blocked': observations[0]['status']['simulation_evidence']['completed_moves'] = 11
    if fault == 'uncertain': status['prompt']['channel_state'] = 'uncertain'
    if fault == 'no_wait_observed': observations = observations[2:]
    assert not evaluate_combined(status, events, observations)
