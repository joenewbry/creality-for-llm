"""Real owned evidence admission, synthetic qualification and physical producer.

No fixture virtual-peer state is imported into the provider implementation.
"""
import copy
from dataclasses import FrozenInstanceError
import json
import os
import struct
from types import SimpleNamespace
import pytest
from printer_control.cold_provider import ColdMachineQualification,ColdPhysicalObservation,ColdAssignedEvidence,digest
from printer_control.checkpoint import PauseCheckpoint
from printer_control.journal import RequestRejected
from test_assigned_preflight import cold_context,preview,cold_line,inactive
from test_cold_preflight import policy
from test_configuration_report import setup as config_setup,request as config_request,response as config_response,REPORT,emit
from test_checkpoint_inspection import prepare,submit
from test_checkpoint_configuration import group
from test_assigned_observations import deliver,wait_for
from test_machine_state import change

pytestmark=pytest.mark.parametrize('cold_context',['physical-cold'],indirect=True)


def qualification(settings):
    p=policy()
    return ColdMachineQualification(dict(schema_version=1,qualification_id='synthetic-unit-not-deployable',printer_id='fixture',
        board='synthetic-board',processor='synthetic-processor',bootloader='synthetic-layout',screen='synthetic-screen',
        firmware_sha256='f'*64,configuration_sha256='c'*64,reported_settings_sha256=settings,coverage_sha256='d'*64,
        assignment_sha256='a'*64,protected_exclusion_sha256='b'*64,driver_sha256='e'*64,recovery_sha256='9'*64,
        coordinate_frame='native_zero_offsets_no_leveling',timer_policy='inactivity_shutdown_disabled',
        position_tolerance_mm=.001,max_pause_seconds=30,cold_policy_sha256=p.id),policy=p)


def physical(request,now):
    return dict(identity=request['identity'],qualification_sha256=request['qualification_sha256'],
        operation_id=request['operation_id'],phase=request['phase'],source_sha256=request['source_sha256'],
        request_nonce=request['request_nonce'],evidence_id='synthetic-physical-source',observed_at=now,expires_at=now+.5,
        continuous_since=0.,position=dict(x=10.,y=10.,z=10.),temperatures={x:dict(actual=25.,target=0.) for x in ('T','B')},
        motor_axes=dict(x=True,y=True,z=True),**{k:(request['phase'] in ('started','resumed','completed','cancelled') if k=='transition_verified' else True) for k in ColdPhysicalObservation.BOOLS})


def install(context):
    b,s,writes=config_setup(context);op=s.submit(config_request(s))['operation_id'];config_response(context)
    done=s.poll(op);assert done['status']=='completed',done
    q=qualification(done['result']['reported_settings_sha256']);calls=[];hook=[None]
    def observe(request):
        calls.append(copy.deepcopy(request));data=physical(request,b.clock())
        if hook[0]:hook[0](data,request)
        return ColdPhysicalObservation(data)
    provider=ColdAssignedEvidence(q,observe=observe);b.evidence=provider;b.evidence_kind=provider.kind;provider.bind(b)
    return b,s,provider,calls,hook


def inspect_reply(context,child,sequence,*,transform=lambda s:s):
    b=context[0];wire=b.prompt._operations[child][1];b.prompt.channel.feed(inactive(wire,1),now=b.clock())
    line=cold_line().replace('llmstate:v1','llmstate:v2')+' I000004b00000000000000000 C07 J01'
    line=transform(change('Q',f'{wire:08x}',change('N',f'{sequence:08x}',line)))
    deliver(context[3],(line+'\n').encode())
    wait_for(lambda:b.machine_events.snapshot()['reports']['machine_state']['data']['request']==wire)


def paused(context,numbered=False):
    b,s,provider,calls,hook=install(context)
    # Real provider preflight now replaces the permissive fixture callback.
    preview(context)
    assert provider.preflight(b._read_bounded(b.path),b.identity())
    staged=s.submit(dict(request_id='physical-stage',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256}))
    assert s.poll(staged['operation_id'])['status']=='completed'
    b,s,_,state=prepare(context,numbered,fixture_provider=False,stage_job=False)
    op=submit(s,'job.pause','pause')['operation_id'];child=group(context,op)
    inspect_reply(context,child,2);done=s.poll(op)
    assert done['status']=='completed',done
    return b,s,provider,calls,hook,state,done


def test_preflight_uses_owned_baseline_state_and_current_physical_observation(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    assert p.preflight(b._read_bounded(b.path),b.identity()) is True
    assert p.start_blockers(b.identity())==[]
    assert [c['phase'] for c in calls]==['preflight','start']
    assert all(c['sources']['controller']['state']['inactivity']['stepper_interval_ms']==0 for c in calls)
    assert all(c['sources']['configuration']['configuration_identity_complete'] is False for c in calls)
    assert calls[0]['request_nonce']!=calls[1]['request_nonce']
    data=p.qualification.to_dict();data['processor']='mutated'
    assert p.qualification.to_dict()['processor']=='synthetic-processor'
    with pytest.raises(FrozenInstanceError):p.qualification.id='changed'
    with pytest.raises(ValueError):p.bind(b)


@pytest.mark.parametrize('numbered',[False,True])
def test_real_provider_builds_pause_checkpoint_and_rechecks_resume_sources(cold_context,numbered):
    b,s,p,calls,hook,state,pause=paused(cold_context,numbered)
    checkpoint=PauseCheckpoint.from_envelope({k:pause['result'][k] for k in ('checkpoint_id','checkpoint')}).to_dict()
    assert checkpoint['firmware_id']=='f'*64 and checkpoint['config_sha256']=='c'*64
    assert checkpoint['position']==dict(x=10.,y=10.,z=10.,e=0.) and all(checkpoint['evidence'].values())
    assert checkpoint['created_at']==10.1 and checkpoint['expires_at']==40.1
    assert not pause['result']['physical_control_qualified']
    cold_context[3][3][0]=12.
    op=submit(s,'job.resume','resume')['operation_id'];child=group(cold_context,op);inspect_reply(cold_context,child,3)
    done=s.poll(op)
    if done['status']=='running':done=s.poll(op)
    assert done['status']=='completed',done
    assert state[0]==b.sender.STATE_PRINTING
    assert calls[-1]['phase']=='resumed' and calls[-2]['phase']=='resuming'
    assert calls[-2]['continuous_since_required']==10.1
    assert s.poll(op)==done


@pytest.mark.parametrize('field,value',[(k,False) for k in sorted(ColdPhysicalObservation.BOOLS-{'transition_verified'})]+
    [('motor_axes',dict(x=True,y=False,z=True)),('position',dict(x=11.,y=10.,z=10.)),
     ('temperatures',{'T':dict(actual=-15.,target=0.),'B':dict(actual=25.,target=0.)}),
     ('temperatures',{'T':dict(actual=25.,target=1.),'B':dict(actual=25.,target=0.)})])
def test_missing_or_contradictory_physical_fact_never_grants_preflight(cold_context,field,value):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    hook[0]=lambda data,request:data.update({field:value})
    with pytest.raises(RequestRejected):p.preflight(b._read_bounded(b.path),b.identity())
    assert p.start_blockers(b.identity())


@pytest.mark.parametrize('field,value',[('phase','paused'),('operation_id','wrong'),('source_sha256','0'*64),
    ('qualification_sha256','0'*64),('request_nonce','0'*32),('observed_at',10.),('continuous_since',10.2),
    ('identity',dict(printer_id='other',generation='g1',host_session='old',job_id='job',file_sha256='a'*64))])
def test_observation_cannot_be_rebound_or_backdated(cold_context,field,value):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    hook[0]=lambda data,request:data.update({field:value})
    with pytest.raises((RequestRejected,ValueError)):p.preflight(b._read_bounded(b.path),b.identity())


def test_slow_physical_callback_does_not_refresh_either_source(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    hook[0]=lambda data,request:cold_context[3][3].__setitem__(0,11.2)
    with pytest.raises(RequestRejected):p.preflight(b._read_bounded(b.path),b.identity())


def test_old_observation_cannot_be_replayed_for_same_scope(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context);saved=[]
    hook[0]=lambda data,request:saved.append(copy.deepcopy(data))
    assert p.preflight(b._read_bounded(b.path),b.identity())
    p.observe=lambda request:ColdPhysicalObservation(saved[0])
    with pytest.raises(RequestRejected):p.preflight(b._read_bounded(b.path),b.identity())


def test_source_mutation_during_physical_callback_invalidates_preflight(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    hook[0]=lambda data,request:b.source_path.write_bytes(b'changed')
    with pytest.raises(RequestRejected):p.preflight(b._read_bounded(b.path),b.identity())


def test_settings_change_or_missing_v2_policy_blocks_before_physical_producer(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    q=p.qualification.to_dict();q['reported_settings_sha256']='0'*64
    p.qualification=ColdMachineQualification(q,policy=p.qualification.policy)
    with pytest.raises(RequestRejected,match='identity_or_source'):p.preflight(b._read_bounded(b.path),b.identity())
    assert not calls


def test_resume_requires_continuity_over_original_pause_interval(cold_context):
    b,s,p,calls,hook,state,pause=paused(cold_context)
    cold_context[3][3][0]=12.;hook[0]=lambda data,request:data.update(continuous_since=11.)
    op=submit(s,'job.resume','resume')['operation_id'];child=group(cold_context,op);inspect_reply(cold_context,child,3)
    done=s.poll(op)
    assert done['status']=='outcome_unknown' and state[0]==b.sender.STATE_PAUSED


def test_provider_never_falls_back_to_legacy_checkpoint_or_another_process(cold_context,monkeypatch):
    b,s,p,calls,hook=install(cold_context)
    with pytest.raises(RequestRejected,match='compound'):p.checkpoint(b.identity())
    monkeypatch.setattr(os,'getpid',lambda:p.pid+1)
    with pytest.raises(RequestRejected):p._check(b.identity())


def begin_started(context,*,verify=True):
    from printer_control.stream_witness import StreamWitness
    b,s,p,calls,hook=install(context);preview(context)
    if not verify:hook[0]=lambda data,request:data.update(transition_verified=False)
    b.witness=StreamWitness();state=[b.sender.STATE_OPERATIONAL]
    b.sender.getState=lambda:state[0]
    def started():
        state[0]=b.sender.STATE_PRINTING;b.sender._callback.on_comm_state_change(state[0])
    b.sender.startPrint=started
    stage=s.submit(dict(request_id='real-provider-stage',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256}))
    assert s.poll(stage['operation_id'])['status']=='completed'
    op=submit(s,'job.start','real-provider-start')['operation_id'];done=s.poll(op)
    assert done['status']==('completed' if verify else 'outcome_unknown'),done
    assert calls[-1]['phase']=='started'
    if verify:assert done['result']['evidence_scope']==p.kind
    return b,s,p,calls,hook,state,op


def test_actual_provider_start_and_final_completion_use_real_journal_and_drain(cold_context):
    b,s,p,calls,hook,state,op=begin_started(cold_context)
    state[0]=b.sender.STATE_OPERATIONAL;b.sender.getFilePosition()['pos']=len(b._read_bounded(b.path))
    b.sender._callback.on_comm_print_job_done()
    b.context();token=b.completion_barrier._active['token']
    assert b._phase=='completing' and calls[-1]['phase']=='started'
    emit(cold_context,'echo:LLM_BARRIER_'+token+'\n');b.context()
    assert b._phase=='completed' and calls[-1]['phase']=='completed'
    assert b._completion_result['external_evidence']['scope']==p.kind
    assert not b._completion_result['physical_control_qualified']


def test_actual_provider_cancel_waits_for_fresh_drain_and_observed_transition(cold_context):
    b,s,p,calls,hook,state,_=begin_started(cold_context)
    def cancelled():
        state[0]=b.sender.STATE_OPERATIONAL;b.sender._callback.on_comm_print_job_cancelled()
    b.sender.cancelPrint=cancelled
    op=submit(s,'job.cancel','real-provider-cancel')['operation_id']
    assert s.poll(op)['status']=='running';token=b.completion_barrier._active['token']
    emit(cold_context,'echo:LLM_BARRIER_'+token+'\n');done=s.poll(op)
    assert done['status']=='completed' and calls[-1]['phase']=='cancelled'


def test_physical_transition_cannot_be_inferred_from_sender_alone(cold_context):
    b,s,p,calls,hook,state,op=begin_started(cold_context,verify=False)
    assert state[0]==b.sender.STATE_PRINTING and s.poll(op)['status']=='outcome_unknown'
    assert calls[-1]['phase']=='started'


@pytest.mark.parametrize('kind',['v1','stepper_timeout','max_timeout','position'])
def test_unsupported_state_cannot_reach_physical_producer(cold_context,kind):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    # Obtain another real journaled inspection, then emit the requested source.
    c=b.context();req=dict(request_id='changed-controller',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=c.state_version,deadline=20.,operation='prompt.inspect',arguments={})
    op=s.submit(req)['operation_id'];wire=b.prompt._operations[op][1]
    b.prompt.channel.feed(inactive(wire,1),now=b.clock())
    line=change('Q',f'{wire:08x}',change('N','00000002',cold_line()))
    if kind!='v1':
        line=line.replace('llmstate:v1','llmstate:v2')+' I000004b00000000000000000 C07 J01'
    if kind=='stepper_timeout':line=change('I','000004b00000ea6000000000',change('J','00',line))
    if kind=='max_timeout':line=change('I','000004b00000000000000001',change('J','03',line))
    if kind=='position':line=change('P',struct.pack('>4f',10000.,10.,10.,0.).hex(),line)
    deliver(cold_context[3],(line+'\n').encode());wait_for(lambda:b.machine_events.snapshot()['reports']['machine_state']['data']['request']==wire)
    done=s.poll(op);assert done['status']=='completed'
    report=b.state_reader.read(op)
    with pytest.raises(RequestRejected):p._state(b.identity(),report)
    assert not calls



def test_advancing_clock_changes_age_without_changing_source_identity(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    hook[0]=lambda data,request:cold_context[3][3].__setitem__(0,b.clock()+.01)
    assert p.preflight(b._read_bounded(b.path),b.identity())
    assert p.start_blockers(b.identity())==[]


def test_observation_expiry_during_journal_commit_blocks_admission(cold_context,monkeypatch):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    original=b.machine_events.journal.record_observation
    def slow(value,**kw):
        result=original(value,**kw)
        if value.get('kind')=='cold_physical_observation_accepted':cold_context[3][3][0]+=.6
        return result
    monkeypatch.setattr(b.machine_events.journal,'record_observation',slow)
    with pytest.raises(RequestRejected,match='expired'):p.preflight(b._read_bounded(b.path),b.identity())


def test_effect_rechecks_physical_expiry_after_its_own_durable_binding(cold_context,monkeypatch):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    original=b.machine_events.journal.record_observation
    def slow(value,**kw):
        result=original(value,**kw)
        if value.get('kind')=='cold_effect_admission':cold_context[3][3][0]+=.6
        return result
    monkeypatch.setattr(b.machine_events.journal,'record_observation',slow)
    req=dict(request_id='expired-effect',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256})
    result=s.submit(req)
    assert result['status'] in ('failed','outcome_unknown') and not cold_context[5]


def test_failed_preflight_does_not_leave_a_previous_grant_usable(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    assert p.preflight(b._read_bounded(b.path),b.identity()) and p._stage_admission is not None
    hook[0]=lambda data,request:data.update(sensors_verified=False)
    with pytest.raises(RequestRejected):p.preflight(b._read_bounded(b.path),b.identity())
    assert p._stage_admission is None


def test_started_confirmation_can_observe_movement_after_the_admission_snapshot(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    # Use the same actual provider, rather than FixtureEvidence's transition.
    original=p.observe
    def moved(request):
        data=original(request).to_dict()
        if request['phase']=='started':data['position']=dict(x=20.,y=20.,z=10.)
        return ColdPhysicalObservation(data)
    p.observe=moved
    from printer_control.stream_witness import StreamWitness
    b.witness=StreamWitness();state=[b.sender.STATE_OPERATIONAL];b.sender.getState=lambda:state[0]
    def start():state[0]=b.sender.STATE_PRINTING;b.sender._callback.on_comm_state_change(state[0])
    b.sender.startPrint=start
    staged=s.submit(dict(request_id='stage-moving',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256}))
    assert s.poll(staged['operation_id'])['status']=='completed'
    op=submit(s,'job.start','start-moving')['operation_id']
    assert s.poll(op)['status']=='completed' and calls[-1]['phase']=='started'


def test_cancellation_confirmation_does_not_depend_on_unchanged_job_file(cold_context):
    b,s,p,calls,hook,state,_=begin_started(cold_context)
    b.path.chmod(0o600);b.path.write_bytes(b'changed after print start')
    def cancel():state[0]=b.sender.STATE_OPERATIONAL;b.sender._callback.on_comm_print_job_cancelled()
    b.sender.cancelPrint=cancel
    op=submit(s,'job.cancel','cancel-changed-file')['operation_id'];assert s.poll(op)['status']=='running'
    token=b.completion_barrier._active['token'];emit(cold_context,'echo:LLM_BARRIER_'+token+'\n')
    done=s.poll(op);assert done['status']=='completed',done
    assert calls[-1]['phase']=='cancelled'


def test_pause_accepts_advancing_observation_age_without_refreshing_original_source(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    staged=s.submit(dict(request_id='stage-time',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256}))
    assert s.poll(staged['operation_id'])['status']=='completed'
    b,s,_,state=prepare(cold_context,fixture_provider=False,stage_job=False)
    hook[0]=lambda data,request:cold_context[3][3].__setitem__(0,b.clock()+.01)
    op=submit(s,'job.pause','pause-time')['operation_id'];child=group(cold_context,op);inspect_reply(cold_context,child,2)
    done=s.poll(op);assert done['status']=='completed',done
    assert done['result']['checkpoint']['created_at']==10.1 and b.clock()>10.1


def test_start_effect_checks_physical_expiry_after_journal_without_starting_sender(cold_context,monkeypatch):
    from printer_control.stream_witness import StreamWitness
    b,s,p,calls,hook=install(cold_context);preview(cold_context);b.witness=StreamWitness()
    b.sender.startPrint=lambda:pytest.fail('expired physical admission started sender')
    stage=s.submit(dict(request_id='stage-before-expiry',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256}))
    assert s.poll(stage['operation_id'])['status']=='completed'
    original=b.machine_events.journal.record_observation
    def slow(value,**kw):
        result=original(value,**kw)
        if value.get('kind')=='cold_effect_admission' and value['operation']=='job.start':cold_context[3][3][0]+=.6
        return result
    monkeypatch.setattr(b.machine_events.journal,'record_observation',slow)
    op=submit(s,'job.start','expired-start')['operation_id'];done=s.poll(op)
    assert done['status'] in ('failed','outcome_unknown') and not b._started


@pytest.mark.parametrize('field,value',[('coordinate_frame','unqualified_offset_frame'),('timer_policy','unknown'),
    ('position_tolerance_mm',1.),('max_pause_seconds',float('nan')),('driver_sha256','missing'),
    ('cold_policy_sha256','0'*64),('schema_version',True)])
def test_qualification_has_no_defaults_for_unsupported_frame_or_policy(cold_context,field,value):
    original=qualification('a'*64);data=original.to_dict();data[field]=value
    with pytest.raises(ValueError):ColdMachineQualification(data,policy=original.policy)


def test_mode_mismatch_and_live_configuration_change_cannot_produce_checkpoint(cold_context):
    b,s,p,calls,hook=install(cold_context);preview(cold_context)
    staged=s.submit(dict(request_id='stage-mode',printer_id=b.printer_id,connection_generation=b.generation,
        expected_state_version=b.context().state_version,deadline=20.,operation='job.stage',arguments={'job_id':'job','file_sha256':b.sha256}))
    assert s.poll(staged['operation_id'])['status']=='completed'
    b,s,_,state=prepare(cold_context,fixture_provider=False,stage_job=False)
    op=submit(s,'job.pause','pause-mode')['operation_id'];child=group(cold_context,op)
    inspect_reply(cold_context,child,2,transform=lambda line:change('V',struct.pack('>9f',1.,11.,100.,100.,0.,25.,0.,25.,0.).hex(),line))
    done=s.poll(op);assert done['status']=='outcome_unknown' and b.checkpoint is None


def test_uncommitted_transition_never_calls_physical_producer(cold_context):
    b,s,p,calls,hook=install(cold_context)
    with pytest.raises(RequestRejected,match='committed'):p.started(b.identity(),'unknown-operation')
    assert not calls
