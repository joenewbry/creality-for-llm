from dataclasses import FrozenInstanceError
import pytest
from printer_control.checkpoint import PauseCheckpoint, InvalidCheckpoint
from printer_control.journal import Journal


def data():
    return dict(schema_version=1, printer_id='sim', generation='g1', host_session='s1',
                job_id='j1', file_sha256='a' * 64, firmware_id='virtual', config_sha256='b' * 64,
                cursor=123, next_line_number=7, created_at=10, expires_at=70,
                position=dict(x=10, y=20, z=.2, e=3), offsets=dict(x=0, y=0, z=0, e=0),
                leveling_active=False, evidence={k: True for k in PauseCheckpoint.PROOFS},
                modal=dict(units='mm', xyz_relative=False, e_relative=True, feedrate=1200,
                           tool=0, fan_pwm=128, nozzle_target=0, bed_target=0,
                           feedrate_percent=100, flow_percent=100))


def test_checkpoint_is_immutable_and_hash_verified():
    source = data()
    saved = PauseCheckpoint(source)
    source['position']['x'] = 99
    exported = saved.envelope()
    assert PauseCheckpoint.from_envelope(exported) == saved
    exported['checkpoint']['position']['x'] = 99
    assert saved.to_dict()['position']['x'] == 10
    with pytest.raises(InvalidCheckpoint, match='hash_mismatch'):
        PauseCheckpoint.from_envelope(exported)
    with pytest.raises(FrozenInstanceError):
        saved.id = 'forged'


@pytest.mark.parametrize('field,value', [('generation', 'new'), ('host_session', 'restart'),
    ('file_sha256', 'c' * 64), ('config_sha256', 'c' * 64), ('job_id', 'new'),
    ('cursor', 124), ('next_line_number', 8), ('leveling_active', True)])
def test_changed_identity_or_stream_never_qualifies_resume(field, value):
    saved = PauseCheckpoint(data())
    live = data()
    live[field] = value
    live['created_at'] = 20
    assert field + '_changed' in saved.in_place_resume_blockers(PauseCheckpoint(live), now=20, max_observation_age=1)


@pytest.mark.parametrize('proof', sorted(PauseCheckpoint.PROOFS))
def test_lost_physical_evidence_blocks_resume(proof):
    live = data()
    live['evidence'][proof] = False
    assert proof + '_unconfirmed' in PauseCheckpoint(data()).in_place_resume_blockers(PauseCheckpoint(live), now=10, max_observation_age=1)


def test_timeout_stale_and_changed_modes_are_distinct():
    saved = PauseCheckpoint(data())
    live = data()
    live['modal']['e_relative'] = False
    reasons = saved.in_place_resume_blockers(PauseCheckpoint(live), now=70, max_observation_age=1)
    assert set(reasons) == {'modal_changed', 'current_observation_stale', 'pause_interval_expired_or_clock_reversed'}
    assert saved.in_place_resume_blockers(saved, now=10, max_observation_age=1) == []


@pytest.mark.parametrize('mutation', [
    lambda d: d.pop('offsets'), lambda d: d.update(cursor=True),
    lambda d: d['modal'].update(e_relative=None), lambda d: d['position'].update(x=float('nan')),
    lambda d: d['evidence'].update(motors_held=1), lambda d: d.update(expires_at=10),
    lambda d: d['modal'].update(extra=1), lambda d: d.update(file_sha256='unknown'),
])
def test_incomplete_or_ambiguous_checkpoint_rejected(mutation):
    value = data()
    mutation(value)
    with pytest.raises(InvalidCheckpoint):
        PauseCheckpoint(value)


def test_checkpoint_and_pause_completion_persist_in_one_receipt(tmp_path):
    path = tmp_path / 'journal.sqlite'
    journal = Journal(path)
    request = dict(request_id='r1', printer_id='sim', connection_generation='g1',
                   expected_state_version=1, deadline=20, operation='job.pause', arguments={'job_id': 'j1'})
    accepted = journal.accept(request, now=10, printer_id='sim', generation='g1', state_version=1)
    op = accepted['operation_id']
    journal.claim(op, now=10)
    checkpoint = PauseCheckpoint(data())
    journal.finish(op, 'completed', checkpoint.envelope(), now=10)
    restored = PauseCheckpoint.from_envelope(Journal(path).get(op)['result'])
    assert restored == checkpoint
    live = data()
    live['host_session'] = 'new-host-session'
    assert 'host_session_changed' in restored.in_place_resume_blockers(PauseCheckpoint(live), now=10, max_observation_age=1)
