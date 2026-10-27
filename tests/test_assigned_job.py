import hashlib
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import uuid
import json
import pytest
from printer_control.assigned_job import AssignedJobBackend,AssignedJobEvidence
from printer_control.assigned_sender import AssignedSender
from printer_control.journal import RequestRejected
from printer_control.service import OperationService
from printer_control.stream_witness import StreamWitness


@pytest.fixture
def runtime(tmp_path):
    now=[10.];state=[AssignedSender.STATE_OPERATIONAL];cursor=[None];actions=[]
    prompt=SimpleNamespace(channel=SimpleNamespace(generation='g1',state='ready'),printer_id='fixture',
        receive_hub=SimpleNamespace(fault=None),frame_writer=SimpleNamespace(fault=None),
        _version=0,context=lambda:None,inspect=lambda:{},close=lambda:None,supported_operations=('prompt.inspect','prompt.bind','prompt.respond'))
    sender=AssignedSender.__new__(AssignedSender)
    sender._assigned_endpoint=SimpleNamespace(backend=prompt)
    sender._callback=SimpleNamespace(**{name:(lambda *a,**k:None) for name in (
        'on_comm_state_change','on_comm_print_job_done','on_comm_print_job_cancelled','on_comm_print_job_failed','on_comm_error')})
    sender.getState=lambda:state[0];sender.getStateString=lambda:str(state[0]);sender._current_line=0
    sender.getFilePosition=lambda:dict(cursor[0]) if cursor[0] else None
    def select(path,sd):actions.append('stage');cursor[0]={'origin':'local','filename':path,'pos':0}
    def start():actions.append('start');state[0]=sender.STATE_PRINTING;sender._callback.on_comm_state_change(state[0])
    def pause(value):actions.append('pause' if value else 'resume');state[0]=sender.STATE_PAUSED if value else sender.STATE_PRINTING;sender._callback.on_comm_state_change(state[0])
    def cancel():actions.append('cancel');state[0]=sender.STATE_OPERATIONAL;sender._callback.on_comm_print_job_cancelled();sender._callback.on_comm_state_change(state[0])
    sender.selectFile=select;sender.startPrint=start;sender.setPause=pause;sender.cancelPrint=cancel;sender.close=lambda:None
    sender.pause_position=SimpleNamespace(as_dict=lambda:dict(x=0.,y=0.,z=0.,e=0.))
    def proof(identity,operation_id,phase):return AssignedJobEvidence.from_observation(identity,operation_id,phase,now[0],'unit_source','unit_fixture_only')
    provider=SimpleNamespace(started=lambda identity,key:proof(identity,key,'started'),resumed=lambda identity,key:proof(identity,key,'resumed'),kind='unit_fixture_only',preflight=lambda *a:True,start_blockers=lambda *a:[],
        checkpoint=lambda *a:None,completed=lambda *a:False,cancelled=lambda *a:False)
    path=tmp_path/'job.gcode';path.write_bytes(b'fixture content')
    backend=AssignedJobBackend(sender,job_path=path,job_id='job',witness=StreamWitness(),evidence=provider,clock=lambda:now[0])
    service=OperationService(tmp_path/'journal.sqlite',backend,clock=lambda:now[0])
    yield service,backend,provider,sender,now,state,cursor,actions,path
    service.close()


def request(runtime,operation,*,arguments=None):
    service,backend,*_=runtime;context=backend.context()
    args={'job_id':'job'} if arguments is None else arguments
    if operation=='job.stage':args={**args,'file_sha256':backend.sha256}
    return dict(request_id=uuid.uuid4().hex,printer_id=context.printer_id,connection_generation=context.generation,
                expected_state_version=context.state_version,deadline=runtime[4][0]+5,operation=operation,arguments=args)


def complete(runtime,operation):
    service=runtime[0];req=request(runtime,operation);result=service.submit(req)
    if result['status']=='running':result=service.poll(result['operation_id'])
    assert result['status']=='completed',result
    assert service.submit(req)==result
    return result


def test_stage_uses_immutable_bytes_and_replay_does_not_select_again(runtime):
    service,backend,_,_,_,_,_,actions,path=runtime
    before=backend.context().state_version;complete(runtime,'job.stage')
    assert backend.context().state_version>before and actions==['stage']
    assert backend.path!=path and backend.path.read_bytes()==b'fixture content'
    path.write_bytes(b'changed input')
    complete(runtime,'job.start')
    assert actions==['stage','start'] and backend.path.read_bytes()==b'fixture content'


@pytest.mark.parametrize('admission',[False,None,0,'yes'])
def test_unqualified_preflight_never_selects_file(runtime,admission):
    runtime[2].preflight=lambda *a:admission
    result=runtime[0].submit(request(runtime,'job.stage'))
    assert result['status']=='rejected' and not runtime[7]


def test_changed_source_between_validation_and_dispatch_never_selects(runtime):
    _,backend,_,_,_,_,_,actions,path=runtime
    args={'job_id':'job','file_sha256':backend.sha256};backend.validate('job.stage',args)
    path.write_bytes(b'changed after validation')
    with pytest.raises(RequestRejected):backend.dispatch('op','job.stage',args,deadline=15.)
    assert not actions and not backend._staged


def test_start_requires_fresh_external_admission_and_does_not_dispatch_when_denied(runtime):
    complete(runtime,'job.stage');runtime[2].start_blockers=lambda *a:['clearance_unqualified']
    result=runtime[0].submit(request(runtime,'job.start'))
    assert result['status']=='rejected' and runtime[7]==['stage']


def test_pause_without_checkpoint_does_not_claim_confirmed_pause_or_resume(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    result=runtime[0].submit(request(runtime,'job.pause'));result=runtime[0].poll(result['operation_id'])
    assert result['status']=='outcome_unknown' and not result['result']['resumable']
    assert runtime[0].submit(request(runtime,'job.resume'))['status']=='rejected'


@pytest.mark.parametrize('unqualified',[False,None,True])
def test_done_callback_and_operational_state_do_not_prove_completion(runtime,unqualified):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    runtime[2].completed=lambda *a:unqualified
    sender=runtime[3];sender._callback.on_comm_print_job_done();runtime[5][0]=sender.STATE_OPERATIONAL
    assert runtime[1].inspect()['job_state']=='completing'
    runtime[2].completed=lambda identity,key:AssignedJobEvidence.from_observation(identity,key,'completed',runtime[4][0],'unit_source','unit_fixture_only')
    assert runtime[1].inspect()['job_state']=='completed'


@pytest.mark.parametrize('unqualified',[False,None,True])
def test_cancellation_waits_for_external_completion_evidence(runtime,unqualified):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    runtime[2].cancelled=lambda *a:unqualified
    receipt=runtime[0].submit(request(runtime,'job.cancel'))
    assert runtime[0].poll(receipt['operation_id'])['status']=='running'
    assert runtime[1].inspect()['job_state']=='cancelling'
    runtime[2].cancelled=lambda identity,key:AssignedJobEvidence.from_observation(identity,key,'cancelled',runtime[4][0],'unit_source','unit_fixture_only')
    assert runtime[0].poll(receipt['operation_id'])['status']=='completed'


def test_deadline_expiry_stays_unknown_and_never_claims_physical_stop(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    receipt=runtime[0].submit(request(runtime,'job.cancel'));runtime[4][0]=16.
    result=runtime[0].poll(receipt['operation_id'])
    assert result['status']=='outcome_unknown' and result['result']['reason']=='operation_deadline_expired'
    assert result['result']['physical_stop_confirmed'] is False
    assert runtime[1].inspect()['job_state']=='outcome_unknown'


def test_completion_provider_exception_is_observable_unknown(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    runtime[3]._callback.on_comm_print_job_done();runtime[5][0]=runtime[3].STATE_OPERATIONAL
    def broken(*a):raise OSError('evidence unavailable')
    runtime[2].completed=broken
    assert runtime[1].inspect()['job_state']=='outcome_unknown'
    assert runtime[1].capabilities()['physical_control_qualified'] is False


@pytest.mark.parametrize('scenario',['resume','native_prompt','cancel','denied','resend','ack_timeout','sensor_fault','thermal_fault','idle_reports'])
def test_journaled_assigned_job_tools_over_owned_software_terminal(scenario):
    if sys.platform!='linux':pytest.skip('Linux owned-terminal integration')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([sys.executable,'tests/assigned_job_smoke.py','--scenario',scenario],cwd=root,capture_output=True,text=True,timeout=75)
    assert run.returncode==0,run.stderr[-5000:]
    result=json.loads(run.stdout);assert result['physical_serial_open_or_discovery_attempts']==0
    if scenario=='denied':assert result['rejected_before_start']
    elif scenario=='idle_reports':
        assert result['prompt_copy_bytes']>65536 and result['controller_pump_samples']>0 and result['no_tool_calls_during_capture']
    elif scenario in ('resend','ack_timeout','sensor_fault','thermal_fault'):
        assert result['job_state']=='outcome_unknown' and not result['physical_stop_confirmed'] and not result['automatic_emergency_frame_emitted']
        assert result['no_additional_wire_frames_after_retirement'] and result['no_original_move_replay'] and result['source_bound_serial_capture']
    else:
        assert result['source_changed_after_stage'] and result['physical_provider_implemented'] is False
        assert result['moves_completed']==80 if scenario!='cancel' else 0<result['moves_completed']<80
        assert result['native_prompt_frames']==(3 if scenario=='native_prompt' else 0)


@pytest.mark.parametrize('wrong',['boolean','identity','operation','phase','scope','before_dispatch','future'])
def test_sender_transition_cannot_use_unbound_or_mistimed_external_proof(runtime,wrong):
    from dataclasses import replace
    complete(runtime,'job.stage')
    req=request(runtime,'job.start');result=runtime[0].submit(req)
    def unqualified(identity,key):
        if wrong=='boolean':return True
        proof=AssignedJobEvidence.from_observation(identity,key,'started',runtime[4][0],'unit_source','unit_fixture_only')
        fields={'identity':{'identity_sha256':'0'*64},'operation':{'operation_id':'another-operation'},
                'phase':{'phase':'resumed'},'scope':{'scope':'another-provider'},
                'before_dispatch':{'observed_at':9.},'future':{'observed_at':11.}}
        return replace(proof,**fields[wrong])
    runtime[2].started=unqualified
    assert runtime[0].poll(result['operation_id'])['status']=='running'
    assert runtime[0].submit(req)['status']=='running' and runtime[7].count('start')==1
    runtime[4][0]=16.
    assert runtime[0].poll(result['operation_id'])['status']=='outcome_unknown'


def test_changed_private_staged_bytes_are_rejected_before_start(runtime):
    complete(runtime,'job.stage')
    runtime[1].path.chmod(0o600);runtime[1].path.write_bytes(b'changed private bytes')
    result=runtime[0].submit(request(runtime,'job.start'))
    assert result['status']=='rejected' and runtime[7]==['stage']


def test_stream_progress_does_not_make_an_otherwise_current_pause_request_stale(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    before=runtime[1].context().state_version
    runtime[1].witness.hook(None,'queued','G1 X1 F6000',None,'G1',tags={'source:file'})
    assert runtime[1].context().state_version==before


def test_stream_change_while_paused_still_invalidates_expected_state_version(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    runtime[3].setPause(True)
    before=runtime[1].context().state_version
    runtime[1].witness.hook(None,'queued','G1 X1 F6000',None,'G1',tags={'source:file'})
    assert runtime[1].context().state_version>before


def test_corrupt_staged_file_cannot_block_cancellation_of_the_owned_job(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    runtime[1].path.chmod(0o600);runtime[1].path.write_bytes(b'changed during job')
    receipt=runtime[0].submit(request(runtime,'job.cancel'))
    assert receipt['status']=='running' and runtime[7]==['stage','start','cancel']


def test_attached_capture_loss_invalidates_job_authority(runtime):
    complete(runtime,'job.stage');complete(runtime,'job.start')
    backend=runtime[1]
    backend.machine_events=SimpleNamespace(capturing=False,snapshot=lambda:{'capturing':False},close=lambda:None)
    assert backend.inspect()['job_state']=='outcome_unknown'
    assert backend.inspect()['blocking_fault']['reason']=='assigned_receive_capture_lost'
    assert runtime[0].submit(request(runtime,'job.pause'))['status']=='rejected'


def test_machine_capture_must_close_before_transport_and_lease(runtime):
    service,backend,_,sender,*_=runtime;order=[]
    sender.close=lambda:order.append('sender')
    backend.prompt.close=lambda:order.append('transport')
    def capture_close():
        service.lease.check();order.append('capture')
    backend.machine_events=SimpleNamespace(close=capture_close)
    service.close()
    assert order==['sender','capture','transport']
    backend.machine_events=None  # Fixture cleanup may safely repeat close.
    service.lease.acquire()


def test_capture_close_failure_retains_lease_and_transport(runtime):
    service,backend,_,_,_,_,_,actions,_=runtime
    def fail():raise TimeoutError('capture still writing')
    backend.machine_events=SimpleNamespace(close=fail)
    backend.prompt.close=lambda:actions.append('transport_closed')
    with pytest.raises(TimeoutError):service.close()
    service.lease.check()
    assert 'transport_closed' not in actions and not backend._closed
    backend.machine_events=None


def lifecycle(runtime):
    import threading
    from printer_control.assigned_observations import AssignedMachineEventRecorder
    service,backend,_,sender,_,_,_,actions,_=runtime
    capture=AssignedMachineEventRecorder.__new__(AssignedMachineEventRecorder)
    capture._condition=threading.Condition();capture._closing=capture._closed=False;capture._fault=None
    capture.endpoint=sender._assigned_endpoint;capture.close=lambda:None
    backend.machine_events=capture
    backend.prompt.controller_lease=service.lease
    sender._assigned_endpoint._claimed=False
    sender._assigned_endpoint.retire=lambda reason:setattr(backend.prompt.receive_hub,'fault',reason)
    sender.start=lambda:actions.append('sender.start')
    return backend,capture


def wait_lifecycle(predicate):
    import time
    until=time.monotonic()+1
    while not predicate():
        assert time.monotonic()<until
        time.sleep(.005)


def test_managed_start_refuses_missing_capture_before_sender_start(runtime):
    runtime[3].start=lambda:pytest.fail('unobserved sender started')
    with pytest.raises(ValueError,match='source capture'):runtime[1].start()


def test_managed_prompt_pump_runs_without_tools_or_external_provider_calls(runtime):
    backend,_=lifecycle(runtime)
    runtime[2].completed=lambda *a:pytest.fail('background physical evidence callback')
    backend.start();wait_lifecycle(lambda:backend._pump_samples>0)
    assert runtime[7]==['sender.start']
    with pytest.raises(ValueError,match='already started'):backend.start()
    with backend.guard():runtime[0].close()
    assert not backend._pump_thread.is_alive() and backend._closed
    runtime[0].lease.acquire()  # Existing fixture performs its own final close.


def test_close_can_join_prompt_consumer_waiting_for_service_guard(runtime):
    backend,_=lifecycle(runtime)
    with backend.guard():
        backend.start()
        runtime[0].close()
    assert not backend._pump_thread.is_alive()
    runtime[0].lease.acquire()


def test_stalled_prompt_consumer_retains_lease_and_transport_until_join(runtime):
    import threading
    backend,_=lifecycle(runtime);entered=threading.Event();release=threading.Event();closed=[]
    def blocked():entered.set();assert release.wait(5)
    backend.prompt.context=blocked
    backend.prompt.close=lambda:closed.append('transport')
    backend.start();assert entered.wait(1)
    try:
        with pytest.raises(TimeoutError,match='ownership retained'):backend.close()
        runtime[0].lease.check()
        assert not closed and not backend._closed
    finally:release.set()
    wait_lifecycle(lambda:not backend._pump_thread.is_alive())
    runtime[0].close();assert closed==['transport']
    runtime[0].lease.acquire()


@pytest.mark.parametrize('cause',['callback','capture','channel','sensor','thermal'])
def test_prompt_consumer_loss_retires_shared_dispatch_without_tool_poll(runtime,cause):
    backend,capture=lifecycle(runtime)
    if cause=='callback':
        def fail():raise OSError('fixture failure')
        backend.prompt.context=fail
    elif cause in ('channel','sensor','thermal'):
        backend.prompt.channel.state='uncertain'
        if cause!='channel':backend.prompt.channel.invalidation_reason='sensor_suspect' if cause=='sensor' else 'firmware_fault'
    if cause=='capture':
        # Start with a usable capture, then lose it after the consumer is live.
        backend.start();capture._fault='fixture_capture_failed'
    else:backend.start()
    wait_lifecycle(lambda:backend._pump_error is not None)
    expected={'sensor':'assigned_serial_sensor_suspect','thermal':'assigned_serial_fault'}.get(cause,'assigned_prompt_pump_unavailable')
    assert backend.prompt.receive_hub.fault==expected and backend._pump_error==expected
    assert backend._fault and backend._phase=='outcome_unknown' and backend.checkpoint is None


def test_interrupted_sender_start_retires_connection_and_is_not_retried(runtime):
    backend,_=lifecycle(runtime)
    def fail():raise OSError('fixture start interrupted')
    runtime[3].start=fail
    with pytest.raises(OSError):backend.start()
    assert backend.prompt.receive_hub.fault=='assigned_start_interrupted'
    with pytest.raises(ValueError,match='already started'):backend.start()
    assert not backend._pump_thread.is_alive()


@pytest.mark.parametrize('change', ['clearance', 'evidence_error', 'staged_bytes'])
def test_start_rechecks_real_adapter_admission_after_durable_claim(runtime, monkeypatch, change):
    service, backend, provider, sender, now, state, cursor, actions, path = runtime
    complete(runtime, 'job.stage')
    claim = service.journal.claim
    def changed_after_claim(*args, **kwargs):
        claimed = claim(*args, **kwargs)
        if change == 'clearance':
            provider.start_blockers = lambda *a: ['clearance_withdrawn']
        elif change == 'evidence_error':
            def unavailable(*a):
                raise OSError('physical evidence source unavailable')
            provider.start_blockers = unavailable
        else:
            backend.path.chmod(0o600)
            backend.path.write_bytes(b'changed while request was committed')
        return claimed
    monkeypatch.setattr(service.journal, 'claim', changed_after_claim)
    req = request(runtime, 'job.start')
    result = service.submit(req)
    assert result['status'] == ('failed' if change == 'evidence_error' else 'rejected')
    assert result['result']['dispatched'] is False
    assert actions == ['stage'] and not backend._started
    assert not service._pending
    assert service.submit(req) == result and actions == ['stage']
