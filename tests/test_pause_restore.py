from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from printer_control.checkpoint import PauseCheckpoint, InvalidCheckpoint
from printer_control.modal import ModalTracker
from printer_control.pause_restore import InPlaceRestorePlan
from test_checkpoint import data


def checkpoint(at=20, **modal):
    value = data()
    value['created_at'] = at
    value['modal'].update(modal)
    return PauseCheckpoint(value)


def plan(saved=None, current=None, **kw):
    return InPlaceRestorePlan(saved or checkpoint(10), current or checkpoint(),
                              now=20, max_observation_age=1, **kw)


@pytest.mark.parametrize('xyz,e', list(product((False, True), repeat=2)))
def test_restore_orders_axis_before_extrusion_without_movement_or_heating(xyz, e):
    result = plan(checkpoint(10, xyz_relative=xyz, e_relative=e),
                  checkpoint(xyz_relative=not xyz, e_relative=not e,
                             fan_pwm=0, feedrate=300, flow_percent=80, feedrate_percent=90))
    assert result.commands == ('G91' if xyz else 'G90', 'M83' if e else 'M82',
                              'M106 P0 S128', 'M220 S100', 'M221 T0 S100', 'G1 F1200')
    tracker = ModalTracker()
    for command in result.commands[:2]:
        tracker.observe(command)
    assert (tracker.xyz_relative, tracker.e_relative) == (xyz, e)
    assert result.verification_blockers(checkpoint(22, xyz_relative=xyz, e_relative=e),
                                        now=22, barrier_completed_at=21) == []


@pytest.mark.parametrize('field,value', [
    ('printer_id', 'other'), ('generation', 'reset'), ('host_session', 'restart'),
    ('job_id', 'other'), ('file_sha256', 'c' * 64), ('config_sha256', 'c' * 64),
    ('firmware_id', 'other'), ('cursor', 124), ('next_line_number', 8),
    ('position', dict(x=11, y=20, z=.2, e=3)),
    ('offsets', dict(x=1, y=0, z=0, e=0)), ('leveling_active', True),
])
def test_nonmodal_changes_cannot_be_restored(field, value):
    live = checkpoint().to_dict()
    live[field] = value
    with pytest.raises(InvalidCheckpoint, match=field + '_changed'):
        plan(current=PauseCheckpoint(live))


@pytest.mark.parametrize('proof', sorted(PauseCheckpoint.PROOFS))
def test_missing_continuity_proof_rejected_before_and_after(proof):
    live = checkpoint().to_dict()
    live['evidence'][proof] = False
    with pytest.raises(InvalidCheckpoint, match=proof + '_unconfirmed'):
        plan(current=PauseCheckpoint(live))
    live['created_at'] = 22
    assert proof + '_unconfirmed' in plan().verification_blockers(
        PauseCheckpoint(live), now=22, barrier_completed_at=21)


@pytest.mark.parametrize('modal', [dict(units='inch'), dict(nozzle_target=200), dict(bed_target=60)])
def test_targets_and_units_are_not_restoration_actions(modal):
    with pytest.raises(InvalidCheckpoint):
        plan(current=checkpoint(**modal))
    with pytest.raises(InvalidCheckpoint):
        plan(checkpoint(10, units='inch'), checkpoint(units='inch'))


@pytest.mark.parametrize('modal', [dict(fan_pwm=127.5), dict(flow_percent=0),
    dict(feedrate_percent=301), dict(flow_percent=99.5), dict(feedrate=60001),
    dict(feedrate=1e-100)])
def test_no_silent_rounding_overflow_or_unsupported_ranges(modal):
    with pytest.raises(InvalidCheckpoint, match='unsupported_restore'):
        plan(checkpoint(10, **modal))


def test_saved_or_stale_evidence_cannot_be_used_as_current():
    with pytest.raises(InvalidCheckpoint, match='independent_current'):
        InPlaceRestorePlan(checkpoint(10), checkpoint(10), now=10, max_observation_age=1)
    with pytest.raises(InvalidCheckpoint, match='stale'):
        plan(current=checkpoint(18))
    with pytest.raises(InvalidCheckpoint, match='expired'):
        InPlaceRestorePlan(checkpoint(10), checkpoint(69), now=70, max_observation_age=1)


def test_verification_needs_fresh_post_barrier_exact_state():
    result = plan()
    assert 'post_barrier_observation_required' in result.verification_blockers(
        checkpoint(), now=21, barrier_completed_at=21)
    assert 'modal_changed' in result.verification_blockers(
        checkpoint(22, fan_pwm=0), now=22, barrier_completed_at=21)
    assert 'invalid_restore_barrier_time' in result.verification_blockers(
        checkpoint(22), now=22, barrier_completed_at=19)
    assert 'invalid_restore_barrier_time' in result.verification_blockers(
        checkpoint(22), now=22, barrier_completed_at=23)
    assert 'current_observation_stale' in result.verification_blockers(
        checkpoint(22), now=24, barrier_completed_at=21)
    assert 'pause_interval_expired_or_clock_reversed' in result.verification_blockers(
        checkpoint(69), now=70, barrier_completed_at=21)


def test_hash_binds_evidence_and_commands_and_plan_is_immutable():
    result = plan()
    assert result.id == plan().id
    assert result.id != plan(current=checkpoint(fan_pwm=0)).id
    assert result.id != plan(checkpoint(10, feedrate=1200.25)).id
    assert plan(checkpoint(10, feedrate=1200.25)).commands[-1] == 'G1 F1200.25'
    with pytest.raises(FrozenInstanceError):
        result.commands = ('M112',)


def test_restoration_marker_fits_64_byte_serial_buffer_with_number_and_checksum():
    result = plan()
    command = 'M118 E1 ' + result.marker
    # Decimal uint32 line number, checksum 0..255 and newline included.
    assert len(('N4294967295 ' + command + '*255\n').encode('ascii')) <= 64
    assert result.marker == 'LLM_RS_' + result.id[:32]
    assert len(result.id) == 64
