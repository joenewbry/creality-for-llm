from dataclasses import replace

import pytest

from printer_control.checkpoint import PauseCheckpoint
from printer_control.journal import RequestRejected
from printer_control.restore_effects import DurableRestore
from printer_control.service import OperationService
from test_service import FakeBackend, REQUEST
from test_stream_witness import restore_fixture, hook


def setup(path):
    witness, epoch, commands, tag, plan = restore_fixture()
    # Recreate a frozen witness without an already-issued grant.
    witness.revoke_pause()
    backend = FakeBackend()
    backend.snapshot = replace(backend.snapshot,printer_id='sim')
    backend.host_session = 's1'
    backend.witness = witness
    service = OperationService(path,backend,clock=lambda:10)
    return service, plan, witness.snapshot()[2]


def emit(service, plan, epoch):
    witness = service.backend.witness
    tag = witness.authorize_restore(plan,epoch,now=20)
    for phase in ('queuing','queued','sending','sent'):
        for command in plan.commands + ('M400','M118 E1 ' + plan.marker):
            assert hook(witness,phase,command,[tag]) is None


def finish(driver, **overrides):
    value = driver.plan.saved.to_dict()
    value['created_at'] = 22
    args=dict(current=PauseCheckpoint(value),marker=driver.plan.marker,barrier_completed_at=21,now=22)
    args.update(overrides)
    return driver.complete(**args)


def test_durable_intent_precedes_dispatch_and_completion_never_grants_resume(tmp_path):
    service, plan, epoch = setup(tmp_path/'journal')
    with service:
        def send(plan,epoch):
            assert driver.receipt()['status'] == 'dispatching'
            emit(service,plan,epoch)
        driver=DurableRestore(service,plan,send,expected_epoch=epoch)
        assert driver.dispatch(now=20)['status']=='dispatching'
        assert finish(driver)['status']=='completed'
        assert not driver.receipt()['resume_authorized']
        with pytest.raises(RequestRejected):driver.dispatch(now=20)
        assert service.submit({**REQUEST,'printer_id':'sim'})['status']=='rejected'


@pytest.mark.parametrize('stage',['prepared','dispatching','completed'])
def test_restart_preserves_receipt_invalidates_authority_and_never_replays(tmp_path,stage):
    path=tmp_path/'journal'
    service,plan,epoch=setup(path)
    sent=[]
    driver=DurableRestore(service,plan,lambda p,e:(sent.append(p.id),emit(service,p,e)),expected_epoch=epoch)
    if stage!='prepared':driver.dispatch(now=20)
    if stage=='completed':finish(driver)
    # Release transport/lease without graceful restoration interruption to
    # model restart against committed state, not process-kill evidence.
    service.backend.close();service.lease.close()
    restarted,_,_=setup(path)
    with restarted:
        with restarted.journal._db() as db:row=dict(db.execute('SELECT * FROM restore_effects').fetchone())
        assert row['status']==('outcome_unknown' if stage=='dispatching' else stage)
        assert row['invalidated']=='host_restarted'
        assert len(sent)==(stage!='prepared')
        assert restarted.submit({**REQUEST,'printer_id':'sim'})['status']=='rejected'
        if stage=='completed':assert 'observation' in row['result']


def test_partial_dispatch_revokes_remaining_commands_and_never_retries(tmp_path):
    service,plan,epoch=setup(tmp_path/'journal')
    with service:
        tags=[]
        def send(p,e):
            tag=service.backend.witness.authorize_restore(p,e,now=20)
            tags.append(tag)
            hook(service.backend.witness,'queuing',p.commands[0],[tag])
            raise OSError('partial write')
        driver=DurableRestore(service,plan,send,expected_epoch=epoch)
        with pytest.raises(OSError):driver.dispatch(now=20)
        assert driver.receipt()['status']=='outcome_unknown'
        assert hook(service.backend.witness,'queued',plan.commands[0],tags)==(None,)
        with pytest.raises(RequestRejected):driver.dispatch(now=20)
        assert len(tags)==1


@pytest.mark.parametrize('stage',['dispatch','complete'])
def test_storage_failure_never_exposes_success(tmp_path,monkeypatch,stage):
    service,plan,epoch=setup(tmp_path/'journal')
    with service:
        sent=[]
        driver=DurableRestore(service,plan,lambda p,e:(sent.append(p.id),emit(service,p,e)),expected_epoch=epoch)
        if stage=='complete':driver.dispatch(now=20)
        def fail(*a,**k):raise OSError('disk unavailable')
        monkeypatch.setattr(driver,'_write_receipt',fail)
        with pytest.raises(OSError):
            if stage=='dispatch':driver.dispatch(now=20)
            else:finish(driver)
        assert driver.receipt_failure and driver.invalidated
        assert len(sent)==(stage=='complete')
        with pytest.raises(RequestRejected):driver.dispatch(now=20)


@pytest.mark.parametrize('failure',['marker','position','epoch','stale','host_session'])
def test_completion_requires_exact_fresh_correlated_observation(tmp_path,failure):
    service,plan,epoch=setup(tmp_path/'journal')
    with service:
        driver=DurableRestore(service,plan,lambda p,e:emit(service,p,e),expected_epoch=epoch)
        driver.dispatch(now=20)
        args={}
        if failure=='marker':args['marker']='stale'
        if failure=='position':
            value=plan.saved.to_dict();value['created_at']=22;value['position']['x']=11
            args['current']=PauseCheckpoint(value)
        if failure=='epoch':hook(service.backend.witness,'queuing','G91')
        if failure=='stale':args['now']=24
        if failure=='host_session':service.backend.host_session='restarted'
        with pytest.raises(RequestRejected):finish(driver,**args)
        assert driver.status=='outcome_unknown' and driver.invalidated


@pytest.mark.parametrize('operation',['job.cancel','printer.stop'])
@pytest.mark.parametrize('storage_failure',[False,True])
def test_interruption_revokes_before_dispatch_even_when_receipt_write_fails(tmp_path,monkeypatch,operation,storage_failure):
    service,plan,epoch=setup(tmp_path/'journal')
    with service:
        witness=service.backend.witness
        tags=[]
        def send(p,e):
            tag=witness.authorize_restore(p,e,now=20);tags.append(tag)
            for phase in ('queuing','queued'):
                for command in p.commands+('M400','M118 E1 '+p.marker):hook(witness,phase,command,[tag])
        driver=DurableRestore(service,plan,send,expected_epoch=epoch)
        driver.dispatch(now=20)
        if storage_failure:
            def fail(*a,**k):raise OSError('disk unavailable')
            monkeypatch.setattr(driver,'_write_receipt',fail)
        service.backend.validate=lambda *a:None
        def dispatch(*a,**k):assert hook(witness,'sending',plan.commands[0],tags)==(None,)
        service.backend.dispatch=dispatch
        request={**REQUEST,'printer_id':'sim','operation':operation,'arguments':{}}
        assert service.submit(request)['status']=='running'
        assert driver.invalidated=='interrupted_by_'+operation
        assert driver.receipt_failure is storage_failure
