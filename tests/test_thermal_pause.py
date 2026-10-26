"""Synthetic thermal decisions only; no device or physical temperature policy."""
from dataclasses import replace

import pytest

from printer_control.checkpoint import PauseCheckpoint
from printer_control.thermal_pause import ThermalPauseLimits, ThermalPausePolicy
from test_checkpoint import data


def fixture():
    checkpoint = data()
    checkpoint['modal'].update(nozzle_target=200., bed_target=60.)
    limits = ThermalPauseLimits(short_seconds=3., max_pause_seconds=30., standby_nozzle=100.,
        max_nozzle=250., max_bed=90., effect_timeout_seconds=3., reheat_timeout_seconds=10.,
        stable_seconds=2., tolerance_celsius=2., max_observation_gap_seconds=2.)
    policy = ThermalPausePolicy(PauseCheckpoint(checkpoint), limits, started_at=0.)
    return policy


def observation(policy, at, **changes):
    return {**{k:policy.saved[k] for k in policy.IDENTITY},
        'checkpoint_id':policy.checkpoint_id, 'sample_at':at,
        **{k:True for k in policy.PROOFS}, 'motor_hold_until':31.,
        'nozzle_actual':200., 'bed_actual':60., 'nozzle_target':policy.expected_nozzle,
        'bed_target':60., 'effect_id':None, **changes}


def tick(policy, at, resume=False, **changes):
    return policy.step(observation(policy,at,**changes), now=at, request_resume=resume)


def enter_standby(policy):
    assert tick(policy,1)['action'] is None
    assert tick(policy,2)['action'] is None
    result=tick(policy,3)
    assert result['action']['nozzle_target']==100.
    assert result['action']['bed_target']==60.
    effect=result['action']['effect_id']
    assert tick(policy,4,effect_id=effect,nozzle_target=100.)['state']=='standby'
    return effect


def test_extended_pause_reheat_stability_and_one_use_permission():
    policy=fixture()
    enter_standby(policy)
    assert tick(policy,5,nozzle_actual=100.)['state']=='standby'
    result=tick(policy,6,resume=True,nozzle_actual=100.)
    assert result['state']=='requesting_reheat' and not result['resume_ready']
    effect=result['action']['effect_id']
    assert result['action']['nozzle_target']==200.
    # Target receipt alone does not establish a hot nozzle.
    assert tick(policy,7,nozzle_actual=100.,nozzle_target=200.,effect_id=effect)['state']=='reheating'
    assert not tick(policy,8)['resume_ready']
    assert not tick(policy,9)['resume_ready']
    assert tick(policy,10)['resume_ready']
    receipt=policy.consume_resume(observation(policy,11), now=11)
    assert receipt['restoration_still_required'] and not receipt['physical_pause_qualified']
    with pytest.raises(ValueError,match='not_ready'):
        policy.consume_resume(observation(policy,12),now=12)


def test_temperature_dip_resets_stable_interval():
    policy=fixture()
    effect=tick(policy,1,resume=True)['action']['effect_id']
    tick(policy,2,effect_id=effect)
    tick(policy,3)
    assert not tick(policy,4,nozzle_actual=190.)['resume_ready']
    assert not tick(policy,5)['resume_ready']
    assert not tick(policy,6)['resume_ready']
    assert tick(policy,7)['resume_ready']


def test_target_change_without_correlated_receipt_is_never_completion_or_retry():
    policy=fixture()
    tick(policy,1);tick(policy,2)
    action=tick(policy,3)['action']
    result=tick(policy,4,nozzle_target=100.,effect_id='wrong')
    assert result['action'] is None and result['state']=='entering_standby'
    assert result['pending_effect']==action
    assert tick(policy,5,nozzle_target=100.)['action'] is None
    failed=tick(policy,6,nozzle_target=100.)
    assert failed['reason']=='target_effect_unconfirmed'
    assert tick(policy,7,effect_id=action['effect_id'],nozzle_target=100.)['state']=='invalid'


@pytest.mark.parametrize('change,reason', [
    ({'generation':'g2'},'pause_identity_changed'),
    ({'host_session':'new'},'pause_identity_changed'),
    ({'file_sha256':'c'*64},'pause_identity_changed'),
    ({'checkpoint_id':'c'*64},'pause_identity_changed'),
    ({'motors_held':False},'pause_continuity_unconfirmed'),
    ({'part_undisturbed':False},'pause_continuity_unconfirmed'),
    ({'fault_free':False},'pause_continuity_unconfirmed'),
    ({'sensors_valid':False},'pause_continuity_unconfirmed'),
    ({'motor_hold_until':1.},'motor_hold_expired'),
    ({'nozzle_actual':-15.},'temperature_outside_policy'),
    ({'nozzle_actual':float('nan')},'invalid_temperature_observation'),
    ({'bed_target':0.},'bed_hold_target_changed'),
    ({'bed_actual':40.},'bed_hold_temperature_lost'),
    ({'nozzle_target':230.},'unexpected_nozzle_target'),
])
def test_lost_evidence_invalidates_irreversibly(change,reason):
    policy=fixture()
    failed=tick(policy,1,**change)
    assert failed['reason']==reason and failed['requires_recovery']
    assert tick(policy,2)['state']=='invalid'


def test_gap_replayed_observation_and_clock_reversal_are_not_recoverable():
    for mode in ('gap','replay','clock'):
        policy=fixture()
        tick(policy,1)
        if mode=='gap': result=tick(policy,4)
        elif mode=='replay': result=policy.step(observation(policy,1),now=2)
        else: result=policy.step(observation(policy,2),now=.5)
        assert result['state']=='invalid'
        assert tick(policy,5)['state']=='invalid'


def test_reheat_timeout_and_total_pause_expiry():
    policy=fixture()
    enter_standby(policy)
    action=tick(policy,5,resume=True,nozzle_actual=100.)['action']
    tick(policy,6,effect_id=action['effect_id'],nozzle_actual=100.,nozzle_target=200.)
    for at in range(7,15): assert not tick(policy,at,nozzle_actual=100.)['resume_ready']
    assert tick(policy,15,nozzle_actual=100.)['reason']=='reheat_timeout'
    policy=fixture()
    enter_standby(policy)
    for at in range(5,30): assert tick(policy,at,nozzle_actual=100.)['state']=='standby'
    assert tick(policy,30,nozzle_actual=100.)['reason']=='pause_limit_expired'


def test_checkpoint_interval_cannot_be_extended_by_policy_configuration():
    policy=fixture()
    with pytest.raises(ValueError,match='checkpoint_interval'):
        ThermalPausePolicy(PauseCheckpoint(policy.saved),replace(policy.limits,max_pause_seconds=61.),started_at=0.)


@pytest.mark.parametrize('change', [{'short_seconds':0}, {'standby_nozzle':300},
    {'max_bed':0}, {'max_observation_gap_seconds':5}, {'stable_seconds':10},
    {'tolerance_celsius':float('nan')}, {'max_nozzle':True}])
def test_invalid_profile_limits_rejected(change):
    with pytest.raises(ValueError):
        replace(fixture().limits,**change)


def test_unheated_bed_does_not_require_zero_actual_temperature():
    original=fixture()
    saved=original.saved
    saved['modal']['bed_target']=0.
    policy=ThermalPausePolicy(PauseCheckpoint(saved),original.limits,started_at=0.)
    action=tick(policy,1,resume=True,bed_target=0.,bed_actual=20.)['action']
    tick(policy,2,effect_id=action['effect_id'],bed_target=0.,bed_actual=20.)
    for at in (3,4,5):result=tick(policy,at,bed_target=0.,bed_actual=20.)
    assert result['resume_ready']


def test_cold_checkpoint_cannot_be_reinterpreted_as_hot_pause():
    original=fixture()
    saved=original.saved
    saved['modal']['nozzle_target']=0.
    with pytest.raises(ValueError,match='targets_outside_policy'):
        ThermalPausePolicy(PauseCheckpoint(saved),replace(original.limits,standby_nozzle=0.),started_at=0.)
