"""Actual source capture/channel/barrier; synthetic external physical proofs."""
import json
import threading
from types import SimpleNamespace
import pytest
from printer_control.checkpoint import PauseCheckpoint
from printer_control.assigned_job import AssignedJobEvidence
from printer_control.stream_witness import StreamWitness
from printer_control.journal import RequestRejected
from test_assigned_preflight import cold_context,preview,cold_line,inactive
from test_assigned_inspection import service_for
from test_assigned_observations import deliver,wait_for
from test_machine_state import change

pytestmark=pytest.mark.parametrize('cold_context',['checkpoint'],indirect=True)


def prepare(context,numbered=False):
    b,_,_,capture,wire,_,provider=context
    preview(context)
    b.dispatch('stage','job.stage',{'job_id':'job','file_sha256':b.sha256},deadline=20.)
    b.witness=StreamWitness();b.witness.start_job()
    for cmd in ('G21','G90','M82','T0','M106 S0','M104 S0','M140 S0','G1 F600'):
        b.witness.tracker.observe(cmd)
    state=[b.sender.STATE_PRINTING];b._started=True
    b.sender.getState=lambda:state[0];b.sender.pause_position=SimpleNamespace(as_dict=lambda:dict(x=10.,y=10.,z=10.,e=0.))
    def pause(value):
        state[0]=b.sender.STATE_PAUSED if value else b.sender.STATE_PRINTING
        b.sender._callback.on_comm_state_change(state[0])
    b.sender.setPause=pause
    def send(command,**kw):
        raw=command.encode()
        if numbered:
            raw=f'N{b.sender._current_line} '.encode()+raw
            checksum=0
            for value in raw:checksum^=value
            raw+=f'*{checksum}'.encode();b.sender._current_line+=1
        return bool(capture[1].write(raw+b'\n'))
    b.sender.sendCommand=send
    seen=[]
    def checkpoint(identity,report):
        seen.append(report)
        return PauseCheckpoint(dict(schema_version=1,**identity,firmware_id='synthetic_only',config_sha256='a'*64,
            cursor=b.sender.getFilePosition()['pos'],next_line_number=b.sender._current_line,
            created_at=b.clock(),expires_at=b.clock()+30,position=dict(x=10.,y=10.,z=10.,e=0.),
            modal=dict(units='mm',xyz_relative=False,e_relative=False,feedrate=600.,tool=0,fan_pwm=0,
                nozzle_target=0,bed_target=0,feedrate_percent=100,flow_percent=100),
            offsets=dict(x=0.,y=0.,z=0.,e=0.),leveling_active=False,
            evidence={k:True for k in PauseCheckpoint.PROOFS}))
    provider.checkpoint_from_controller=checkpoint
    provider.resumed=lambda identity,op:AssignedJobEvidence.from_observation(identity,op,'resumed',b.clock(),'synthetic_only',provider.kind)
    return b,service_for(context),seen,state


def submit(s,operation,request_id):
    b=s.backend;c=b.context()
    return s.submit(dict(request_id=request_id,printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation=operation,arguments={'job_id':'job'}))


def marker(context,parent):
    b=context[0];s=b.machine_events
    # First poll queues the pair; the fake sender writes the actual shared boundary.
    assert b.poll(parent) is None
    token=b.completion_barrier._active['token']
    deliver(context[3],('echo:LLM_BARRIER_'+token+'\n').encode())
    wait_for(lambda:any(token in x['event']['data'].get('text','') for x in s.read()['events']))
    assert b.poll(parent) is None
    return b.checkpoint_inspection.operations[parent]['child']


def inspection(context,child,sequence):
    b=context[0];capture=context[3];wire=b.prompt._operations[child][1]
    b.prompt.channel.feed(inactive(wire,1),now=b.clock())
    deliver(capture,(change('Q',f'{wire:08x}',change('N',f'{sequence:08x}',cold_line()))+'\n').encode())
    wait_for(lambda:b.machine_events.snapshot()['reports']['machine_state']['data']['request']==wire)


@pytest.mark.parametrize('numbered',[False,True])
def test_pause_and_resume_use_distinct_post_drain_reports(cold_context,numbered):
    b,s,seen,state=prepare(cold_context,numbered)
    pause=submit(s,'job.pause','pause');op=pause['operation_id']
    child=marker(cold_context,op)
    assert not seen and not b.checkpoint
    inspection(cold_context,child,2)
    paused=s.poll(op)
    assert paused['result'].get('resume_blockers') is None,paused['result'].get('resume_blockers')
    assert paused['status']=='completed',paused
    proof=paused['result']['completion_barrier']
    assert proof['inspection']['operation_id']==child and len(proof['inspection']['writes'])==1
    assert proof['controller_state']['operation_id']==child and seen and b.checkpoint is not None
    assert not proof['resume_authorized'] and not proof['physical_readiness_qualified']
    b.inspect()  # Status must not invalidate a completed historical barrier.
    with b.machine_events.journal._db() as db:
        assert db.execute('SELECT status FROM assigned_barriers WHERE operation_id=?',(op,)).fetchone()[0]=='completed'
    cold_context[3][3][0]=12.  # Saved report is stale; resume must acquire another.
    resume=submit(s,'job.resume','resume');resume_op=resume['operation_id']
    assert resume['status']=='running',resume
    resume_child=marker(cold_context,resume_op)
    assert resume_child!=child and state[0]==b.sender.STATE_PAUSED
    inspection(cold_context,resume_child,3)
    resumed=s.poll(resume_op)
    if resumed['status']=='running':resumed=s.poll(resume_op)
    assert resumed['status']=='completed',resumed
    assert state[0]==b.sender.STATE_PRINTING
    assert resumed['result']['admission_barrier']['controller_state']['operation_id']==resume_child
    assert s.poll(resume_op)==resumed


@pytest.mark.parametrize('case',['stale','other_frame','duplicate_query','missing_state','provider','slow_provider'])
def test_uncertain_inputs_never_grant_a_checkpoint(cold_context,case):
    b,s,seen,_=prepare(cold_context);pause=submit(s,'job.pause','pause');op=pause['operation_id']
    child=marker(cold_context,op)
    if case=='missing_state':
        b.prompt.channel.feed(inactive(b.prompt._operations[child][1],1),now=b.clock())
        assert s.poll(op)['status']=='running' and not seen
        cold_context[3][3][0]=20.
    else:
        inspection(cold_context,child,2)
        if case=='stale':cold_context[3][3][0]+=1.1
        elif case=='other_frame':cold_context[3][1].write(b'M105\n')
        elif case=='duplicate_query':
            command=b.completion_barrier._active['inspection']['command']
            b.prompt.frame_writer.write_prompt((command+'\n').encode())
        elif case=='provider':b.evidence.checkpoint_from_controller=lambda *args:None
        elif case=='slow_provider':
            original=b.evidence.checkpoint_from_controller
            def slow(*args):
                value=original(*args);cold_context[3][3][0]+=1.1;return value
            b.evidence.checkpoint_from_controller=slow
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and b.checkpoint is None and b._fault,result
    assert state_is_paused(b) and not b.capabilities()['physical_control_qualified']


def state_is_paused(b):return b.sender.getState()==b.sender.STATE_PAUSED


def test_controller_loop_captures_pause_without_another_model_call(cold_context):
    b,s,seen,_=prepare(cold_context)
    pause=submit(s,'job.pause','pause');op=pause['operation_id'];child=marker(cold_context,op)
    b._pump_thread=threading.Thread(target=b._pump_prompts)
    b._pump_thread.start()
    try:
        inspection(cold_context,child,2)
        wait_for(lambda:op in b._checkpoint_results)
        assert b.checkpoint is not None and seen and b._checkpoint_results[op].status=='completed'
        cold_context[3][3][0]=15.
        assert s.poll(op)['status']=='completed'
    finally:
        b._pump_stop.set();b._pump_thread.join(2)
        assert not b._pump_thread.is_alive()


def test_before_marker_cannot_authorize_a_query(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    assert b.poll(op) is None
    before=len(cold_context[4])
    with pytest.raises(RequestRejected,match='fresh_barrier'):
        b.completion_barrier.authorize_inspection(op,'not-journaled',4)
    assert len(cold_context[4])==before and not seen


def test_cleared_workflow_cannot_replay_a_claimed_inspection(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    marker(cold_context,op);before=len(cold_context[4])
    b.checkpoint_inspection.operations.clear()
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and not seen and b.checkpoint is None
    assert len(cold_context[4])==before


def test_provider_cannot_change_the_received_report(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    child=marker(cold_context,op);inspection(cold_context,child,2)
    original=b.evidence.checkpoint_from_controller
    def mutate(identity,report):
        result=original(identity,report);report['state']['position']['x']=999
        return result
    b.evidence.checkpoint_from_controller=mutate
    result=s.poll(op)
    assert result['status']=='completed'
    assert result['result']['completion_barrier']['controller_state']['state']['position']['x']==10.


def test_inspection_intent_is_durable_before_actual_query(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    assert b.poll(op) is None
    token=b.completion_barrier._active['token']
    deliver(cold_context[3],('echo:LLM_BARRIER_'+token+'\n').encode())
    wait_for(lambda:any(token in x['event']['data'].get('text','') for x in b.machine_events.read()['events']))
    writer=b.prompt.frame_writer;original=writer._writer;inspected=[]
    def confirm(data):
        if data.startswith(b'M876'):
            with b.machine_events.journal._db() as db:
                intent=json.loads(db.execute('SELECT intent FROM assigned_barriers WHERE token=?',(token,)).fetchone()[0])
                child=intent['inspection']['operation_id']
                assert b.machine_events.journal.get(child)['status']=='running'
                inspected.append(child)
        return original(data)
    writer._writer=confirm
    assert b.poll(op) is None and len(inspected)==1 and not seen


def test_partial_inspection_frame_never_grants_checkpoint(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    assert b.poll(op) is None
    token=b.completion_barrier._active['token']
    deliver(cold_context[3],('echo:LLM_BARRIER_'+token+'\n').encode())
    wait_for(lambda:any(token in x['event']['data'].get('text','') for x in b.machine_events.read()['events']))
    original=b.prompt.frame_writer._writer
    b.prompt.frame_writer._writer=lambda data:2 if data.startswith(b'M876') else original(data)
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and not seen and b.checkpoint is None
    assert b.prompt.frame_writer.fault


@pytest.mark.parametrize('case',['unqualified_provider','extra_line_number'])
def test_resume_stays_paused_when_new_checkpoint_cannot_be_qualified(cold_context,case):
    b,s,seen,state=prepare(cold_context,numbered=True)
    pause=submit(s,'job.pause','pause')['operation_id'];child=marker(cold_context,pause)
    inspection(cold_context,child,2);assert s.poll(pause)['status']=='completed'
    resume=submit(s,'job.resume','resume')['operation_id'];child=marker(cold_context,resume)
    inspection(cold_context,child,3)
    if case=='unqualified_provider':b.evidence.checkpoint_from_controller=lambda *a:None
    else:b.sender._current_line+=1
    result=s.poll(resume)
    assert result['status']=='outcome_unknown' and state[0]==b.sender.STATE_PAUSED
    assert b.checkpoint is None and not b.witness.allow_job_commands


def test_new_barrier_cannot_abandon_an_unfinished_inspection(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    marker(cold_context,op);before=len(cold_context[4])
    with pytest.raises(RequestRejected,match='barrier_already_pending'):
        b.completion_barrier.dispatch(op,'paused',b.identity(),lambda command:None)
    assert len(cold_context[4])==before and not seen


def test_external_provider_is_required_when_attaching_checkpoint_inspection(cold_context):
    b=cold_context[0];del b.evidence.checkpoint_from_controller
    with pytest.raises(TypeError,match='external controller-backed'):
        b.attach_completion_barrier(checkpoint_inspection=True)


def test_supported_prompt_inspection_can_overlap_before_drain_starts(cold_context):
    b,s,seen,state=prepare(cold_context)
    b.sender.setPause=lambda value:None  # Pause acknowledgement has not arrived.
    pause=submit(s,'job.pause','pause')
    assert pause['status']=='running' and not b._barrier_tokens
    c=b.context()
    inspect=s.submit(dict(request_id='observe-wait',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='prompt.inspect',arguments={}))
    assert inspect['status']=='running' and state[0]==b.sender.STATE_PRINTING
    assert not b.checkpoint_inspection.operations and not seen


def test_other_prompt_cannot_replace_inspection_after_drain_starts(cold_context):
    b,s,seen,_=prepare(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    marker(cold_context,op);before=len(cold_context[4]);c=b.context()
    inspect=s.submit(dict(request_id='competing-inspect',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='prompt.inspect',arguments={}))
    assert inspect['status']=='rejected' and inspect['result']['reason']=='operation_in_progress'
    assert len(cold_context[4])==before and not seen


def test_predrain_prompt_receipt_finishes_before_checkpoint_inspection(cold_context):
    b,s,seen,state=prepare(cold_context)
    pause_method=b.sender.setPause;b.sender.setPause=lambda value:None
    op=submit(s,'job.pause','pause')['operation_id'];c=b.context()
    prior=s.submit(dict(request_id='observe-wait',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='prompt.inspect',arguments={}))
    pause_method(True)
    assert b.poll(op) is None
    token=b.completion_barrier._active['token']
    deliver(cold_context[3],('echo:LLM_BARRIER_'+token+'\n').encode())
    wait_for(lambda:any(token in x['event']['data'].get('text','') for x in b.machine_events.read()['events']))
    assert b.poll(op) is None and not b.checkpoint_inspection.operations
    wire=b.prompt._operations[prior['operation_id']][1]
    b.prompt.channel.feed(inactive(wire,1),now=b.clock())
    assert s.poll(prior['operation_id'])['status']=='completed'
    assert b.poll(op) is None
    child=b.checkpoint_inspection.operations[op]['child']
    inspection(cold_context,child,2)
    assert s.poll(op)['status']=='completed' and seen
