"""Compound source reports and synthetic physical provider through real core."""
import threading
import pytest
from test_assigned_preflight import cold_context
from test_checkpoint_inspection import prepare,submit,inspection,state_is_paused
from test_configuration_report import REPORT,emit
from test_assigned_observations import wait_for

pytestmark=pytest.mark.parametrize('cold_context',['checkpoint-configuration'],indirect=True)


def setup(context,numbered=False):
    b,s,seen,state=prepare(context,numbered);configs=[]
    def checkpoint(identity,report,configuration):
        configs.append(configuration)
        return b.evidence.checkpoint_from_controller(identity,report)
    b.evidence.checkpoint_from_sources=checkpoint
    return b,s,seen,state,configs


def group(context,parent,body=REPORT):
    b=context[0]
    assert b.poll(parent) is None
    token=b.completion_barrier._active['token']
    emit(context,'echo:LLM_BARRIER_'+token+'\n'+body+'echo:LLM_CONFIG_END_'+token+'\n')
    assert b.poll(parent) is None
    return b.checkpoint_inspection.operations[parent]['child']


def paused(context,numbered=False):
    b,s,seen,state,configs=setup(context,numbered)
    op=submit(s,'job.pause','pause')['operation_id'];child=group(context,op)
    assert not seen and not configs and state_is_paused(b)
    inspection(context,child,2);done=s.poll(op)
    assert done['status']=='completed',done
    return b,s,seen,state,configs,done


@pytest.mark.parametrize('numbered',[False,True])
def test_pause_and_resume_pass_distinct_fresh_settings_and_state(cold_context,numbered):
    b,s,seen,state,configs,pause=paused(cold_context,numbered)
    cfg=pause['result']['completion_barrier']['configuration']
    assert cfg['operation_id']==pause['operation_id'] and cfg['identity']==b.identity()
    assert len(pause['result']['completion_barrier']['writes'])==5
    assert not cfg['configuration_identity_complete'] and not cfg['physical_readiness_qualified']
    cold_context[3][3][0]=12.
    op=submit(s,'job.resume','resume')['operation_id'];child=group(cold_context,op)
    assert state_is_paused(b)
    inspection(cold_context,child,3);done=s.poll(op)
    if done['status']=='running':done=s.poll(op)
    assert done['status']=='completed',done
    new=done['result']['admission_barrier']['configuration']
    assert new['operation_id']==op and new['barrier_token']!=cfg['barrier_token']
    assert new['reported_settings_sha256']==cfg['reported_settings_sha256']
    assert new['source_observed_at']==12. and cfg['source_observed_at']==10.1
    assert state[0]==b.sender.STATE_PRINTING and len(configs)>=2
    assert s.poll(op)==done


@pytest.mark.parametrize('change',['steps','firmware','caps','added_setting'])
def test_changed_report_keeps_resume_paused_even_if_provider_reuses_config_hash(cold_context,change):
    b,s,seen,state,configs,pause=paused(cold_context,True)
    body={'steps':REPORT.replace('X80','X81',1),'firmware':REPORT.replace('SyntheticMarlin','DifferentMarlin'),
        'caps':REPORT.replace('EEPROM:1','EEPROM:0'),'added_setting':REPORT+'echo: M593 X F40 D0.1\n'}[change]
    before=len(configs)
    op=submit(s,'job.resume','resume')['operation_id'];child=group(cold_context,op,body)
    inspection(cold_context,child,3);result=s.poll(op)
    assert result['status']=='outcome_unknown' and state_is_paused(b),result
    assert result['result']['detail']=='reported_configuration_changed_since_pause'
    assert len(configs)==before
    assert b.checkpoint is None and not b.witness.allow_job_commands


def test_missing_report_prevents_state_query_or_checkpoint(cold_context):
    b,s,seen,state,configs=setup(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    assert b.poll(op) is None;before=len(cold_context[4]);token=b.completion_barrier._active['token']
    emit(cold_context,'echo:LLM_BARRIER_'+token+'\n'+'echo:LLM_CONFIG_END_'+token+'\n')
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and not configs and not seen
    assert len(cold_context[4])==before and b.checkpoint is None


def test_unexplained_line_increment_cannot_hide_in_five_read_frames(cold_context):
    b,s,seen,state,configs,_=paused(cold_context,True)
    op=submit(s,'job.resume','resume')['operation_id'];child=group(cold_context,op)
    inspection(cold_context,child,3);b.sender._current_line+=1
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and state_is_paused(b)
    assert 'transport_line_mismatch' in result['result']['detail']


def test_caller_cannot_mutate_cached_configuration(cold_context):
    b,s,seen,state,configs=setup(cold_context);original=b.evidence.checkpoint_from_sources
    def mutate(identity,report,configuration):
        result=original(identity,report,configuration);configuration['reported_settings'][0]='G20'
        configuration['reported_settings_sha256']='0'*64
        return result
    b.evidence.checkpoint_from_sources=mutate
    op=submit(s,'job.pause','pause')['operation_id'];child=group(cold_context,op);inspection(cold_context,child,2)
    done=s.poll(op);assert done['status']=='completed'
    saved=done['result']['completion_barrier']['configuration']
    assert saved['reported_settings'][0].startswith('G21') and saved['reported_settings_sha256']!='0'*64


def test_background_loop_captures_both_inputs_without_model_poll(cold_context):
    b,s,seen,state,configs=setup(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    child=group(cold_context,op)
    b._pump_thread=threading.Thread(target=b._pump_prompts);b._pump_thread.start()
    try:
        inspection(cold_context,child,2);wait_for(lambda:op in b._checkpoint_results)
        cold_context[3][3][0]=15.
        assert s.poll(op)['status']=='completed' and b.checkpoint is not None and configs
    finally:
        b._pump_stop.set();b._pump_thread.join(2)
        assert not b._pump_thread.is_alive()


def test_fresh_state_cannot_renew_old_settings_markers(cold_context):
    b,s,seen,state,configs,_=paused(cold_context)
    op=submit(s,'job.resume','resume')['operation_id'];child=group(cold_context,op)
    cold_context[3][3][0]+=1.1
    inspection(cold_context,child,3)
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and state_is_paused(b)
    assert b.checkpoint is None


def test_slow_configuration_persistence_prevents_the_state_query(cold_context,monkeypatch):
    b,s,seen,state,configs=setup(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    assert b.poll(op) is None;before=len(cold_context[4]);token=b.completion_barrier._active['token']
    emit(cold_context,'echo:LLM_BARRIER_'+token+'\n'+REPORT+'echo:LLM_CONFIG_END_'+token+'\n')
    original=b.machine_events.journal.record_observation
    def slow(*args,**kwargs):
        result=original(*args,**kwargs);cold_context[3][3][0]+=1.1;return result
    monkeypatch.setattr(b.machine_events.journal,'record_observation',slow)
    assert s.poll(op)['status']=='outcome_unknown' and not configs and not seen
    assert len(cold_context[4])==before


def test_slow_physical_provider_cannot_refresh_either_source(cold_context):
    b,s,seen,state,configs=setup(cold_context);original=b.evidence.checkpoint_from_sources
    def slow(*args):
        result=original(*args);cold_context[3][3][0]+=1.1;return result
    b.evidence.checkpoint_from_sources=slow
    op=submit(s,'job.pause','pause')['operation_id'];child=group(cold_context,op);inspection(cold_context,child,2)
    assert s.poll(op)['status']=='outcome_unknown' and b.checkpoint is None


def test_file_changed_after_settings_sample_cannot_create_checkpoint(cold_context):
    b,s,seen,state,configs=setup(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    child=group(cold_context,op);inspection(cold_context,child,2)
    b.path.chmod(0o600);b.path.write_bytes(b'changed')
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and b.checkpoint is None


def test_missing_end_marker_keeps_pause_unqualified(cold_context):
    b,s,seen,state,configs=setup(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    assert b.poll(op) is None;token=b.completion_barrier._active['token']
    emit(cold_context,'echo:LLM_BARRIER_'+token+'\n'+REPORT)
    assert s.poll(op)['status']=='running' and not configs and not seen
    cold_context[3][3][0]=20.
    assert s.poll(op)['status']=='outcome_unknown' and b.checkpoint is None


@pytest.mark.parametrize('case',['extra_write','repeated_end'])
def test_unplanned_write_or_marker_cannot_become_a_compound_checkpoint(cold_context,case):
    b,s,seen,state,configs=setup(cold_context);op=submit(s,'job.pause','pause')['operation_id']
    child=group(cold_context,op);inspection(cold_context,child,2)
    if case=='extra_write':cold_context[3][1].write(b'M105\n')
    else:emit(cold_context,'echo:LLM_CONFIG_END_'+b.completion_barrier._active['token']+'\n')
    assert s.poll(op)['status']=='outcome_unknown' and b.checkpoint is None


def test_two_numbered_cycles_rebind_both_reports_to_each_operation(cold_context):
    b,s,seen,state,configs,_=paused(cold_context,True)
    for cycle in range(2):
        resume=submit(s,'job.resume','resume'+str(cycle))['operation_id']
        child=group(cold_context,resume);inspection(cold_context,child,3+cycle*2)
        done=s.poll(resume)
        if done['status']=='running':done=s.poll(resume)
        assert done['status']=='completed' and done['result']['admission_barrier']['configuration']['operation_id']==resume
        if cycle==0:
            pause=submit(s,'job.pause','pause2')['operation_id'];child=group(cold_context,pause)
            inspection(cold_context,child,4);done=s.poll(pause)
            assert done['status']=='completed' and b._pause_configuration['operation_id']==pause


def test_compound_mode_requires_a_provider_for_both_sources(cold_context):
    b=cold_context[0]
    with pytest.raises(TypeError,match='require controller inspection'):
        b.attach_completion_barrier(checkpoint_configuration=True)
    del b.evidence.checkpoint_from_sources
    with pytest.raises(TypeError,match='state/configuration checkpoint provider'):
        b.attach_completion_barrier(checkpoint_inspection=True,checkpoint_configuration=True)


def test_compound_provider_does_not_fall_back_to_legacy_controller_callback(cold_context):
    b,s,seen,state,configs=setup(cold_context);legacy=b.evidence.checkpoint_from_controller
    b.evidence.checkpoint_from_sources=lambda identity,report,cfg:legacy(identity,report)
    del b.evidence.checkpoint_from_controller
    op=submit(s,'job.pause','pause')['operation_id'];child=group(cold_context,op);inspection(cold_context,child,2)
    assert s.poll(op)['status']=='completed'


def test_official_tool_client_drives_compound_pause_and_resume(cold_context):
    import asyncio,anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from printer_control.mcp_adapter import make_server
    from printer_control.tools import ToolRouter
    b,s,seen,state,configs=setup(cold_context,True);server=make_server(ToolRouter(s))
    def arguments(name):
        c=b.context()
        return dict(request_id=name,printer_id=c.printer_id,connection_generation=c.generation,
            expected_state_version=c.state_version,deadline=20.,arguments={'job_id':'job'})
    async def run():
        async with create_client_server_memory_streams() as (client_streams,server_streams):
            async with anyio.create_task_group() as tasks:
                async def serve():await server.run(*server_streams,server.create_initialization_options())
                tasks.start_soon(serve)
                async with ClientSession(*client_streams) as client:
                    await client.initialize()
                    for name,sequence in [('job.pause',2),('job.resume',3)]:
                        accepted=await client.call_tool(name,arguments(name))
                        assert not accepted.is_error and accepted.structured_content['status']=='running'
                        op=accepted.structured_content['operation_id']
                        pending=await client.call_tool('operation.get',{'operation_id':op})
                        assert pending.structured_content['status']=='running'
                        token=b.completion_barrier._active['token']
                        emit(cold_context,'echo:LLM_BARRIER_'+token+'\n'+REPORT+'echo:LLM_CONFIG_END_'+token+'\n')
                        pending=await client.call_tool('operation.get',{'operation_id':op})
                        assert pending.structured_content['status']=='running'
                        child=b.checkpoint_inspection.operations[op]['child'];inspection(cold_context,child,sequence)
                        done=await client.call_tool('operation.get',{'operation_id':op})
                        if done.structured_content['status']=='running':done=await client.call_tool('operation.get',{'operation_id':op})
                        assert not done.is_error and done.structured_content['status']=='completed'
                        assert not done.structured_content['result']['physical_control_qualified']
                tasks.cancel_scope.cancel()
    asyncio.run(run());assert state[0]==b.sender.STATE_PRINTING and configs
