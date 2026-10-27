"""Controller-managed source inspection, with no device or physical proof."""
import json
import pytest
from printer_control.service import OperationService
from printer_control.journal import RequestRejected
from test_assigned_preflight import cold_context,cold_line,inactive
from test_assigned_observations import deliver,wait_for
from test_machine_state import change

pytestmark=pytest.mark.parametrize('cold_context',[True],indirect=True)


def service_for(context):
    b=context[0];s=OperationService.__new__(OperationService)
    s.backend=b;s.journal=b.machine_events.journal;s.lease=b.machine_events.lease
    s.clock=b.clock;s.max_age=5.;s._pending={};s._restore=None;s._restore_recovery_required=False
    s._thermal_pause=None;s._thermal_recovery_required=False;s._resume_workflow=None
    return s


def stage(s,request_id='stage',deadline=20.):
    c=s.backend.context()
    request=dict(request_id=request_id,printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=deadline,operation='job.stage',
        arguments={'job_id':'job','file_sha256':s.backend.sha256})
    return s.submit(request),request


def reply(context,parent,*,state=True,sequence=2):
    b,_,_,capture,_,_,_=context
    item=b.cold_inspection.pending[parent];child=item['child']
    wire=b.prompt._operations[child][1]
    b.prompt.channel.feed(inactive(wire,1),now=capture[3][0])
    if state:
        line=change('Q',f'{wire:08x}',change('N',f'{sequence:08x}',cold_line()))
        deliver(capture,(line+'\n').encode())
        wait_for(lambda:b.machine_events.snapshot()['reports']['machine_state']['data']['request']==wire)
    return child


def test_stage_gets_fresh_inspection_without_an_llm_preview(cold_context):
    b,_,_,capture,wire,selected,_=cold_context;s=service_for(cold_context)
    capture[3][0]=12.  # Existing report is already stale.
    before=len(wire);receipt,request=stage(s);parent=receipt['operation_id']
    assert receipt['status']=='running' and len(wire)==before+1 and not selected
    child=b.cold_inspection.pending[parent]['child']
    assert s.journal.get(child)['status']=='running'
    assert s.submit(request)==receipt and len(wire)==before+1
    assert s.poll(parent)['status']=='running' and not selected
    reply(cold_context,parent)
    done=s.poll(parent)
    assert done['status']=='completed' and len(selected)==1
    assert done['result']['cold_preflight']['inspect_operation_id']==child
    assert s.journal.get(child)['status']=='completed'
    assert s.poll(parent)==done and len(selected)==1 and len(wire)==before+1
    assert not b.capabilities()['physical_control_qualified']


def test_receipt_without_state_cannot_select_file(cold_context):
    b,_,_,capture,_,selected,_=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent,state=False)
    assert s.poll(parent)['status']=='running' and not selected
    capture[3][0]=20.
    assert s.poll(parent)['status']=='failed' and not selected


@pytest.mark.parametrize('failure',['stale','external','file','generation','fault'])
def test_changed_inputs_cannot_gain_job_effect(cold_context,failure):
    b,_,_,capture,_,selected,provider=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent)
    if failure=='stale':capture[3][0]=11.2
    elif failure=='external':provider.preflight=lambda *args:False
    elif failure=='file':b.source_path.write_bytes(b'G28\n')
    elif failure=='generation':b.generation='different'
    else:b._fault={'reason':'injected_source_loss'}
    result=s.poll(parent)
    assert result['status'] in ('failed','outcome_unknown') and not selected


def test_other_prompt_request_cannot_replace_internal_inspection(cold_context):
    b,_,_,_,wire,selected,_=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);before=len(wire);c=b.context()
    other=s.submit(dict(request_id='other',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='prompt.inspect',arguments={}))
    assert other['status']=='rejected' and other['result']['reason']=='operation_in_progress'
    assert len(wire)==before and not selected


def test_unjournaled_parent_cannot_send_inspection(cold_context):
    b,_,_,_,wire,selected,_=cold_context;before=len(wire)
    with pytest.raises(RequestRejected,match='committed_cold_parent_required'):
        b.dispatch('invented','job.stage',{'job_id':'job','file_sha256':b.sha256},deadline=20.)
    assert len(wire)==before and not selected


def test_interrupted_file_effect_is_not_retried(cold_context):
    b,_,_,_,_,selected,_=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent)
    def fail(path,sd):selected.append(path);raise OSError('interrupted')
    b.sender.selectFile=fail
    first=s.poll(parent)
    assert first['status']=='outcome_unknown' and len(selected)==1
    assert s.poll(parent)==first and len(selected)==1


def test_start_collects_a_new_report_after_delayed_model_request(cold_context):
    b,_,_,capture,wire,selected,_=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent)
    assert s.poll(parent)['status']=='completed'
    capture[3][0]=12.;starts=[]
    b.witness.start_job=lambda:None
    def start_print():
        starts.append(True);b.sender.getState=lambda:b.sender.STATE_PRINTING
    b.sender.startPrint=start_print
    c=b.context();before=len(wire)
    start=s.submit(dict(request_id='start',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='job.start',arguments={'job_id':'job'}))
    assert start['status']=='running' and not starts and len(wire)==before+1
    reply(cold_context,start['operation_id'],sequence=3)
    assert s.poll(start['operation_id'])['status']=='running' and starts==[True]
    assert s.poll(start['operation_id'])['status']=='running' and starts==[True]


def test_slow_external_callback_cannot_extend_one_second_limit(cold_context):
    b,_,_,capture,_,selected,provider=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent)
    def slow(*args):capture[3][0]+=1.1;return True
    provider.preflight=slow
    assert s.poll(parent)['status']=='failed' and not selected


def test_same_parent_cannot_replay_a_claimed_child(cold_context):
    b,_,_,_,wire,selected,_=cold_context;s=service_for(cold_context)
    receipt,request=stage(s);before=len(wire)
    b.cold_inspection.pending.clear()  # Lost in-memory workflow is not replay authority.
    with pytest.raises(RequestRejected):
        b.cold_inspection.begin(receipt['operation_id'],'job.stage',request['arguments'],20.)
    assert len(wire)==before and not selected


def test_existing_controller_pump_advances_without_model_polling(cold_context):
    import threading
    b,_,_,capture,_,selected,_=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id']
    b._pump_thread=threading.Thread(target=b._pump_prompts)
    b._pump_thread.start()
    try:
        reply(cold_context,parent)
        wait_for(lambda:parent in b._cold_results)
        assert len(selected)==1 and b._cold_results[parent].status=='completed'
        capture[3][0]=15.  # A slow model can collect the completed receipt later.
        assert s.poll(parent)['status']=='completed' and len(selected)==1
    finally:
        b._pump_stop.set();b._pump_thread.join(2)
        assert not b._pump_thread.is_alive()


def test_provider_exception_fails_admission_before_job_effect(cold_context):
    b,_,_,_,_,selected,provider=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent)
    def unavailable(*args):raise OSError('qualification source unavailable')
    provider.preflight=unavailable
    result=s.poll(parent)
    assert result['status']=='failed' and result['result']['reason']=='cold_admission_unavailable'
    assert not selected and parent not in b.cold_inspection.pending


def test_slow_witness_setup_cannot_start_from_expired_source(cold_context):
    b,_,_,capture,_,_,_=cold_context;s=service_for(cold_context)
    receipt,_=stage(s);parent=receipt['operation_id'];reply(cold_context,parent)
    assert s.poll(parent)['status']=='completed'
    starts=[]
    def slow():capture[3][0]+=1.1
    b.witness.start_job=slow;b.sender.startPrint=lambda:starts.append(True)
    c=b.context()
    start=s.submit(dict(request_id='start',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='job.start',arguments={'job_id':'job'}))
    reply(cold_context,start['operation_id'],sequence=3)
    assert s.poll(start['operation_id'])['status']=='outcome_unknown' and not starts
