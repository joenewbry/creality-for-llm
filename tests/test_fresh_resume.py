"""Fresh resume admission, with all other checkpoint facts explicitly modeled."""
from types import SimpleNamespace
import pytest
from test_assigned_job import runtime,complete,request
from test_checkpoint import data
from printer_control.checkpoint import PauseCheckpoint
from printer_control.journal import RequestRejected


def paused(runtime):
    service,backend,provider,sender,now,_,cursor,actions,_=runtime
    complete(runtime,'job.stage');complete(runtime,'job.start')
    for command in ('G21','G90','M82','T0','M106 S0','M104 S0','M140 S0','G1 F1200'):
        backend.witness.tracker.observe(command)
    sender._current_line=7
    def sample():
        value=data();value.update(backend.identity());value.update(created_at=now[0],cursor=cursor[0]['pos'],
            next_line_number=sender._current_line,position=dict(x=0.,y=0.,z=0.,e=0.))
        value['modal'].update(e_relative=False,fan_pwm=0)
        return PauseCheckpoint(value)
    provider.checkpoint=lambda *a:sample()
    complete(runtime,'job.pause')
    assert backend.checkpoint and backend.witness.frozen and actions.count('resume')==0
    return sample


def gate(runtime,*,numbered=False,bad_numbers=False):
    service,backend,provider,sender,now,*_=runtime
    calls=[];ready=[False];proof={};writes=[]
    def dispatch(operation,phase,identity,enqueue):
        calls.append((operation,phase));start=sender._current_line
        numbers=[start,start+1] if numbered else [None,None]
        if bad_numbers:numbers=[0,1]
        proof.update(operation_id=operation,phase=phase,identity=identity,token='unit-token',
            writes=[{'line_number':n,'confirmed':True} for n in numbers],resume_authorized=False,
            physical_readiness_qualified=False)
        enqueue('M400');enqueue('M118 E1 LLM_BARRIER_unit-token')
        if numbered:sender._current_line+=2
        return 'unit-token'
    def poll():
        if now[0]>11.:raise RequestRejected('barrier_marker_stale')
        return dict(proof) if ready[0] else None
    sender.sendCommand=lambda command,**kwargs:writes.append(command) or True
    backend.completion_barrier=SimpleNamespace(dispatch=dispatch,poll=poll)
    return calls,ready,proof,writes


@pytest.mark.parametrize('numbered',[False,True])
def test_resume_waits_for_new_marker_then_dispatches_once(runtime,numbered):
    paused(runtime);calls,ready,proof,writes=gate(runtime,numbered=numbered)
    service,backend,*_=runtime
    req=request(runtime,'job.resume');receipt=service.submit(req)
    for _ in range(3):receipt=service.poll(receipt['operation_id'])
    assert receipt['status']=='running' and runtime[7].count('resume')==0
    assert calls==[(receipt['operation_id'],'resuming')]
    assert writes==['M400','M118 E1 LLM_BARRIER_unit-token']
    ready[0]=True
    for _ in range(3):receipt=service.poll(receipt['operation_id'])
    assert receipt['status']=='completed' and runtime[7].count('resume')==1
    assert receipt['result']['admission_barrier']['phase']=='resuming'
    assert service.submit(req)==receipt and len(calls)==1


@pytest.mark.parametrize('loss',['motor_proof','config','cursor','bad_numbers','file_after_callback','stale_after_callback'])
def test_failed_fresh_admission_never_dispatches_resume(runtime,loss):
    sample=paused(runtime);calls,ready,proof,writes=gate(runtime,numbered=loss=='bad_numbers',bad_numbers=loss=='bad_numbers')
    service,backend,provider,sender,now,*_=runtime
    req=request(runtime,'job.resume');receipt=service.submit(req);receipt=service.poll(receipt['operation_id'])
    assert receipt['status']=='running' and runtime[7].count('resume')==0
    def changed(*args):
        value=sample().to_dict()
        if loss=='motor_proof':value['evidence']['motors_held']=False
        if loss=='config':value['config_sha256']='c'*64
        if loss=='cursor':value['cursor']+=1
        if loss=='file_after_callback':
            backend.path.chmod(0o600);backend.path.write_bytes(b'changed staged bytes during provider')
        if loss=='stale_after_callback':
            now[0]=11.1;value['created_at']=now[0]
        return PauseCheckpoint(value)
    provider.checkpoint=changed;ready[0]=True
    receipt=service.poll(receipt['operation_id'])
    assert receipt['status']=='outcome_unknown' and runtime[7].count('resume')==0
    assert backend.checkpoint is None and backend.witness.frozen and not backend.witness.allow_job_commands
    assert service.submit(req)==receipt and len(calls)==1
