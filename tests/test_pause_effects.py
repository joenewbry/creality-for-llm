from dataclasses import replace

import pytest

from printer_control.journal import RequestRejected
from printer_control.pause_effects import DurablePauseEffects
from printer_control.service import OperationService
from test_service import FakeBackend, REQUEST
from test_thermal_pause import fixture as policy_fixture, observation


def owner(path):
    backend=FakeBackend()
    backend.snapshot=replace(backend.snapshot,printer_id='sim')
    return OperationService(path,backend,clock=lambda:10)


def advance(driver,at,resume=False,**changes):
    return driver.advance(observation(driver.policy,at,**changes),now=at,request_resume=resume)


def test_effect_commits_before_write_and_completion_before_next_action(tmp_path):
    with owner(tmp_path/'journal') as service:
        sent=[]
        def send(action):
            assert driver.effects()[-1]['status']=='dispatching'
            assert driver.effects()[-1]['action']==action
            sent.append(action)
        driver=DurablePauseEffects(service,policy_fixture(),send)
        advance(driver,1);advance(driver,2);advance(driver,3)
        assert len(sent)==1 and driver.effects()[0]['status']=='dispatching'
        advance(driver,4,effect_id=sent[-1]['effect_id'],nozzle_target=100.)
        assert driver.effects()[0]['status']=='completed'
        advance(driver,5,resume=True,nozzle_actual=100.)
        assert len(sent)==2
        advance(driver,6,effect_id=sent[-1]['effect_id'],nozzle_target=200.,nozzle_actual=100.)
        for at in (7,8,9):advance(driver,at)
        assert driver.policy.status()['resume_ready']
        receipt=driver.consume_resume(observation(driver.policy,10),now=10)
        assert receipt['restoration_still_required']
        assert all(x['status']=='completed' for x in driver.effects())
        assert not service.journal.has_unreconciled_thermal_pause()
        with pytest.raises(ValueError):driver.consume_resume(observation(driver.policy,11),now=11)


def test_storage_failure_before_dispatch_never_writes_or_revives(tmp_path,monkeypatch):
    with owner(tmp_path/'journal') as service:
        sent=[]
        driver=DurablePauseEffects(service,policy_fixture(),sent.append)
        advance(driver,1);advance(driver,2)
        original=driver._persist
        def fail(**kwargs):raise OSError('storage unavailable')
        monkeypatch.setattr(driver,'_persist',fail)
        with pytest.raises(OSError):advance(driver,3)
        assert sent==[] and driver.policy.state=='invalid'
        monkeypatch.setattr(driver,'_persist',original)
        advance(driver,4)
        assert sent==[] and driver.receipt_failure


def test_partial_effect_write_becomes_unknown_without_retry(tmp_path):
    with owner(tmp_path/'journal') as service:
        sent=[]
        def partial(action):
            sent.append(action)
            raise OSError('write uncertain')
        driver=DurablePauseEffects(service,policy_fixture(),partial)
        advance(driver,1,resume=True)
        assert driver.effects()[0]['status']=='outcome_unknown'
        advance(driver,2,effect_id=sent[0]['effect_id'])
        assert len(sent)==1 and driver.policy.state=='invalid'


def test_receipt_failure_after_ack_cannot_authorize_followup(tmp_path,monkeypatch):
    with owner(tmp_path/'journal') as service:
        sent=[]
        driver=DurablePauseEffects(service,policy_fixture(),sent.append)
        advance(driver,1);advance(driver,2);advance(driver,3)
        def fail(**kwargs):raise OSError('commit unavailable')
        monkeypatch.setattr(driver,'_persist',fail)
        with pytest.raises(OSError):advance(driver,4,effect_id=sent[0]['effect_id'],nozzle_target=100.)
        assert driver.policy.state=='invalid' and driver.effects()[0]['status']=='dispatching'
        with pytest.raises(RequestRejected):driver.consume_resume(observation(driver.policy,5),now=5)
        assert len(sent)==1


def test_restart_seals_old_session_and_does_not_replay(tmp_path):
    path=tmp_path/'journal'
    sent=[]
    with owner(path) as service:
        driver=DurablePauseEffects(service,policy_fixture(),sent.append)
        advance(driver,1,resume=True)
    with owner(path) as replacement:
        assert replacement._thermal_recovery_required
        with replacement.journal._db() as db:
            assert db.execute('SELECT status FROM thermal_effects').fetchone()[0]=='outcome_unknown'
        with pytest.raises(RequestRejected,match='prior_thermal'):
            DurablePauseEffects(replacement,policy_fixture(),sent.append)
        receipt=replacement.submit({**REQUEST,'printer_id':'sim'})
        assert receipt['status']=='rejected'
        assert receipt['result']['reason']=='thermal_pause_requires_completion_or_recovery'
        assert replacement.backend.dispatched==[] and len(sent)==1


def test_ordinary_commands_blocked_but_validated_cancel_interrupts_policy(tmp_path):
    with owner(tmp_path/'journal') as service:
        sent=[]
        driver=DurablePauseEffects(service,policy_fixture(),sent.append)
        advance(driver,1,resume=True)
        blocked=service.submit({**REQUEST,'printer_id':'sim'})
        assert blocked['status']=='rejected' and service.backend.dispatched==[]
        service.backend.validate=lambda *args:None
        cancel=service.submit({**REQUEST,'printer_id':'sim','request_id':'cancel','operation':'job.cancel'})
        assert cancel['status']=='running'
        assert driver.policy.state=='invalid' and driver.effects()[0]['status']=='outcome_unknown'
        assert len(service.backend.dispatched)==1 and len(sent)==1


def test_storage_failure_does_not_suppress_stop(tmp_path,monkeypatch):
    with owner(tmp_path/'journal') as service:
        driver=DurablePauseEffects(service,policy_fixture(),lambda action:None)
        def fail(**kwargs):raise OSError('disk unavailable')
        monkeypatch.setattr(driver,'_persist',fail)
        service.backend.validate=lambda *args:None
        stopped=service.submit({**REQUEST,'printer_id':'sim','operation':'printer.stop'})
        assert stopped['status']=='running'
        assert len(service.backend.dispatched)==1 and driver.receipt_failure
        assert driver.policy.state=='invalid'


def test_failed_consumption_commit_keeps_ordinary_operations_blocked(tmp_path,monkeypatch):
    with owner(tmp_path/'journal') as service:
        sent=[]
        driver=DurablePauseEffects(service,policy_fixture(),sent.append)
        advance(driver,1,resume=True)
        advance(driver,2,effect_id=sent[0]['effect_id'])
        for at in (3,4,5):advance(driver,at)
        assert driver.policy.state=='ready'
        def fail(**kwargs):raise OSError('commit unavailable')
        monkeypatch.setattr(driver,'_persist',fail)
        with pytest.raises(OSError):driver.consume_resume(observation(driver.policy,6),now=6)
        assert driver.policy.state=='invalid'
        receipt=service.submit({**REQUEST,'printer_id':'sim'})
        assert receipt['status']=='rejected' and service.backend.dispatched==[]
