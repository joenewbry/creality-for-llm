"""Final completion admission; sender/continuity facts are explicit unit models."""
from types import SimpleNamespace
import json
import pytest
from test_assigned_job import runtime,complete,request
from printer_control.assigned_job import AssignedJobEvidence
from printer_control.assigned_barrier import AssignedCompletionWindow
from printer_control.journal import RequestRejected


def fixture_completion(runtime,*,start_running=False):
    service,backend,provider,sender,now,state,cursor,actions,_=runtime
    complete(runtime,'job.stage')
    if start_running:
        start=service.submit(request(runtime,'job.start'))
        backend.context()  # Observe its printing transition before EOF.
    else:start=complete(runtime,'job.start')
    calls=[];writes=[];ready=[False];arrived=[None];window=[None]
    def dispatch(operation,phase,identity,enqueue,*,completion_window):
        assert operation==start['operation_id'] and phase=='completed'
        assert type(completion_window) is AssignedCompletionWindow
        calls.append((operation,phase));window[0]=completion_window
        enqueue('M400');enqueue('M118 E1 LLM_BARRIER_unit');return 'unit'
    def poll():
        if now[0]>=window[0].deadline:raise RequestRejected('completion_deadline_expired')
        if not ready[0]:return None
        if arrived[0] is None:arrived[0]=now[0]
        if now[0]-arrived[0]>1:raise RequestRejected('completion_marker_stale')
        return dict(operation_id=start['operation_id'],identity=backend.identity(),phase='completed',receipt={'observed_at':arrived[0]},
            completion_window=window[0].record(),resume_authorized=False,physical_readiness_qualified=False)
    backend.completion_barrier=SimpleNamespace(dispatch=dispatch,poll=poll)
    backend.machine_events=SimpleNamespace(capturing=True,journal=service.journal,snapshot=lambda:{'capturing':True},close=lambda:None)
    sender.sendCommand=lambda command,**kwargs:writes.append(command) or True
    provider.completed=lambda identity,operation:AssignedJobEvidence.from_observation(identity,operation,'completed',now[0],'unit_final_trace','unit_fixture_only')
    cursor[0]['pos']=len(backend.path.read_bytes())
    def eof():sender._callback.on_comm_print_job_done();state[0]=sender.STATE_OPERATIONAL
    return start,calls,writes,ready,eof


def test_completed_start_deadline_does_not_become_a_print_lifetime_limit(runtime):
    start,calls,writes,ready,eof=fixture_completion(runtime)
    service,backend,_,_,now,*_=runtime
    original=service.journal.get(start['operation_id'])
    now[0]=20.;eof();now[0]=25.
    assert backend.inspect()['job_state']=='completing' and len(calls)==1
    ready[0]=True;result=backend.inspect()
    assert result['job_state']=='completed' and result['completion_evidence']['completion_window']['observed_at']==20.
    assert result['completion_evidence']['completion_window']['deadline']==50.
    assert service.journal.get(start['operation_id'])==original
    assert writes==['M400','M118 E1 LLM_BARRIER_unit']
    with service.journal._db() as db:
        rows=db.execute("SELECT snapshot FROM observations WHERE source='job.inspect'").fetchall()
    assert any(json.loads(r[0]).get('event')=='completion_evidence_observed' for r in rows)


def test_delayed_first_poll_cannot_refresh_expired_eof_window(runtime):
    _,calls,writes,ready,eof=fixture_completion(runtime)
    backend=runtime[1];eof();runtime[4][0]=41.
    assert backend.inspect()['job_state']=='outcome_unknown' and calls==[] and writes==[]
    assert backend._fault['detail']=='completion_lifecycle_window_expired'


def test_finishing_state_still_consumes_original_eof_window(runtime):
    _,calls,writes,ready,eof=fixture_completion(runtime)
    backend=runtime[1];backend._completion_window_seconds=5.;eof();runtime[5][0]=runtime[3].STATE_FINISHING
    runtime[4][0]=16.
    assert backend.inspect()['job_state']=='outcome_unknown' and not writes


def test_marker_does_not_replace_external_completion_proof(runtime):
    _,calls,writes,ready,eof=fixture_completion(runtime)
    backend=runtime[1];eof();ready[0]=True;runtime[2].completed=lambda *a:True
    assert backend.inspect()['job_state']=='completing' and backend._completion_result is None


@pytest.mark.parametrize('change',['foreign_cursor','changed_file','duplicate_eof'])
def test_completion_identity_loss_refuses_marker(runtime,change):
    _,calls,writes,ready,eof=fixture_completion(runtime);backend=runtime[1];eof()
    if change=='foreign_cursor':runtime[6][0]['filename']='other.gcode'
    if change=='changed_file':backend.path.chmod(0o600);backend.path.write_bytes(b'changed')
    if change=='duplicate_eof':eof()
    assert backend.inspect()['job_state']=='outcome_unknown' and not writes


def test_completion_waits_for_start_receipt_in_a_fast_job(runtime):
    start,calls,writes,ready,eof=fixture_completion(runtime,start_running=True)
    backend=runtime[1];eof();ready[0]=True
    assert backend.inspect()['job_state']=='completing' and not writes
    assert runtime[0].poll(start['operation_id'])['status']=='completed'
    assert backend.inspect()['job_state']=='completed'


def test_slow_completion_provider_cannot_renew_marker_arrival(runtime):
    _,calls,writes,ready,eof=fixture_completion(runtime);backend=runtime[1];eof();ready[0]=True
    def delayed(identity,operation):
        runtime[4][0]+=1.1
        return AssignedJobEvidence.from_observation(identity,operation,'completed',runtime[4][0],'unit_final_trace','unit_fixture_only')
    runtime[2].completed=delayed
    assert backend.inspect()['job_state']=='outcome_unknown' and backend._completion_result is None
    assert backend._fault['detail']=='completion_marker_stale'


@pytest.mark.parametrize('seconds',[0,-1,True,float('inf'),float('nan'),121])
def test_completion_window_is_explicitly_bounded(seconds):
    with pytest.raises(ValueError):AssignedCompletionWindow(10.,seconds)



def test_provider_cannot_switch_the_sender_file_during_final_admission(runtime):
    _,calls,writes,ready,eof=fixture_completion(runtime);backend=runtime[1];eof();ready[0]=True
    def switched(identity,operation):
        runtime[6][0]['filename']='other.gcode'
        return AssignedJobEvidence.from_observation(identity,operation,'completed',runtime[4][0],'unit_final_trace','unit_fixture_only')
    runtime[2].completed=switched
    assert backend.inspect()['job_state']=='outcome_unknown' and backend._completion_result is None
    assert backend._fault['detail']=='completion_file_identity_changed'
