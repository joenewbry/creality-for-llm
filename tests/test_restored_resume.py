"""Service handoff with stub job execution; no streamed hot-printer claim."""
from dataclasses import replace

import pytest

from printer_control.checkpoint import PauseCheckpoint
from printer_control.pause_effects import DurablePauseEffects
from printer_control.pause_restore import InPlaceRestorePlan
from printer_control.restore_effects import DurableRestore
from printer_control.service import Outcome
from test_restore_effects import setup, emit, finish
from test_service import REQUEST
from test_thermal_pause import fixture, observation


def ready(path, *, hot=False):
    service,plan,epoch=setup(path)
    if hot:
        policy=fixture()
        thermal=DurablePauseEffects(service,policy,lambda action:None)
        thermal.advance(observation(policy,1),now=1,request_resume=True)
        effect=policy.pending['effect_id']
        thermal.advance(observation(policy,2,effect_id=effect),now=2)
        for at in (3,4,5):thermal.advance(observation(policy,at),now=at)
        thermal.consume_resume(observation(policy,6),now=6)
        saved=PauseCheckpoint(policy.saved)
        current=saved.to_dict();current['created_at']=20
        plan=InPlaceRestorePlan(saved,PauseCheckpoint(current),now=20,max_observation_age=1)
    restore=DurableRestore(service,plan,lambda p,e:emit(service,p,e),expected_epoch=epoch)
    restore.dispatch(now=20);finish(restore)
    service.clock=lambda:23
    service.backend.snapshot=replace(service.backend.snapshot,observed_at=23)
    calls=[]
    def acquire(plan):
        calls.append('observe')
        current=plan.saved.to_dict();current['created_at']=23
        monotonic_now=6+len(calls)*.1
        return {'checkpoint':PauseCheckpoint(current),
            'thermal':observation(service._thermal_pause.policy,monotonic_now) if hot else None,
            'monotonic_now':monotonic_now}
    def dispatch(operation_id,plan,*,expected_epoch,deadline):
        assert restore.receipt()['status']=='resume_dispatching'
        assert restore.receipt()['result']['resume_operation_id']==operation_id
        assert service.backend.witness.arm_resume(expected_epoch)
        calls.append('resume')
        service.backend.outcomes[operation_id]=Outcome('completed',{'evidence_type':'stub_printing_transition'})
    service.backend.restored_resume_evidence=acquire
    service.backend.dispatch_restored_resume=dispatch
    request={**REQUEST,'request_id':'resume','printer_id':'sim','operation':'job.resume',
             'arguments':{'job_id':'j1'},'deadline':30}
    return service,restore,request,calls


@pytest.mark.parametrize('hot',[False,True])
def test_resume_handoff_is_durable_rechecked_and_one_use(tmp_path,hot):
    service,restore,request,calls=ready(tmp_path/'journal',hot=hot)
    with service:
        receipt=service.submit(request)
        assert receipt['status']=='running'
        assert calls==['observe','observe','resume']
        assert service.submit(request)==receipt
        assert service.poll(receipt['operation_id'])['status']=='completed'
        assert restore.status=='resumed' and service._restore is None
        assert not service.journal.has_unreconciled_restore()
        assert service.submit(request)['status']=='completed'
        assert calls.count('resume')==1
        assert restore.receipt()['result']['resume_result']['evidence_type']=='stub_printing_transition'


def test_temperature_loss_between_validation_and_dispatch_prevents_resume(tmp_path):
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        acquire=service.backend.restored_resume_evidence
        def drift(plan):
            result=acquire(plan)
            if len(calls)==2:result['thermal']['nozzle_actual']=180
            return result
        service.backend.restored_resume_evidence=drift
        receipt=service.submit(request)
        assert receipt['status']=='outcome_unknown'  # claimed but not dispatched
        assert 'resume' not in calls
        assert restore.invalidated and service._thermal_pause.policy.state=='invalid'
        assert service.backend.witness.allow_job_commands is False


def test_partial_resume_is_never_replayed_and_restart_requires_recovery(tmp_path):
    path=tmp_path/'journal'
    service,restore,request,calls=ready(path)
    original=service.backend.dispatch_restored_resume
    def partial(*args,**kw):
        original(*args,**kw)
        raise OSError('after dispatch')
    service.backend.dispatch_restored_resume=partial
    with service:
        receipt=service.submit(request)
        assert receipt['status']=='outcome_unknown'
        assert service.submit(request)==receipt
        assert calls.count('resume')==1
        assert restore.status=='outcome_unknown'
        assert not service.backend.witness.allow_job_commands
    restarted,_,_=setup(path)
    with restarted:
        assert restarted._restore_recovery_required
        assert calls.count('resume')==1


def test_resume_completion_storage_failure_keeps_gate_closed(tmp_path,monkeypatch):
    service,restore,request,calls=ready(tmp_path/'journal')
    with service:
        receipt=service.submit(request)
        def fail(*args,**kw):raise OSError('storage unavailable')
        monkeypatch.setattr(restore,'_write_receipt',fail)
        with pytest.raises(OSError):service.poll(receipt['operation_id'])
        assert service._restore is restore and restore.invalidated
        assert service.submit({**request,'request_id':'another'})['status']=='rejected'
        assert calls.count('resume')==1


@pytest.mark.parametrize('changes',[{'nozzle_actual':180},{'motor_hold_until':6.1},
    {'sensors_valid':False},{'nozzle_target':100},{'bed_actual':40}])
def test_consumed_thermal_policy_still_requires_live_continuity(tmp_path,changes):
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        acquire=service.backend.restored_resume_evidence
        def lost(plan):
            result=acquire(plan);result['thermal'].update(changes);return result
        service.backend.restored_resume_evidence=lost
        assert service.submit(request)['status'] in ('failed','rejected')
        assert 'resume' not in calls
        assert service._thermal_pause.policy.state=='invalid'


def test_consumed_policy_cannot_be_replaced_before_verified_resume(tmp_path):
    from printer_control.journal import RequestRejected
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        previous=service._thermal_pause
        with pytest.raises(RequestRejected,match='prior_thermal_session_not_retired'):
            DurablePauseEffects(service,fixture(),lambda action:None)
        assert service._thermal_pause is previous and not previous.retired
        receipt=service.submit(request)
        assert service.poll(receipt['operation_id'])['status']=='completed'
        epoch=service.backend.witness.snapshot()[2]
        with pytest.raises(RequestRejected,match='retired'):
            previous.advance(observation(previous.policy,7),now=7)
        previous.interrupt('late_stale_callback')
        assert previous.policy.state=='consumed'
        assert service.backend.witness.snapshot()[2]==epoch
        assert previous.retired and service._thermal_pause is None


def test_unclosed_monitor_prevents_resume_dispatch(tmp_path):
    from types import SimpleNamespace
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        service._thermal_monitor=SimpleNamespace(closed=False,thread=None,close=lambda:None)
        receipt=service.submit(request)
        assert receipt['status']=='rejected'
        assert receipt['result']['reason']=='pause_monitor_not_closed_for_resume'
        assert 'resume' not in calls


def test_retirement_failure_keeps_resume_authority_invalid(tmp_path):
    service,restore,request,calls=ready(tmp_path/'journal',hot=True)
    with service:
        previous=service._thermal_pause
        receipt=service.submit(request)
        def fail(checkpoint_id):raise OSError('retirement failed')
        service.backend.retire_restored_pause=fail
        with pytest.raises(OSError):service.poll(receipt['operation_id'])
        assert restore.invalidated and not previous.retired
        assert service._thermal_pause is previous
        assert not service.backend.witness.allow_job_commands
        assert calls.count('resume')==1
