"""Real journal/source recorder and shared writer; no printer or sender model proof."""
import json
import pytest
from test_assigned_observations import make_endpoint, deliver, wait_for
from printer_control.assigned_barrier import AssignedCompletionBarrier
from printer_control.assigned_observations import AssignedMachineEventRecorder
from printer_control.journal import Journal, RequestRejected
from printer_control.ownership import ControllerLease
from types import SimpleNamespace


@pytest.fixture
def barrier(tmp_path):
    now=[10.]; chunks=[]; endpoint=make_endpoint(now,chunks)
    journal=Journal(tmp_path/'barrier.sqlite')
    with ControllerLease(str(journal.path)+'.owner') as lease:
        endpoint.backend.controller_lease=lease
        recorder=AssignedMachineEventRecorder(journal,endpoint=endpoint,lease=lease,clock=lambda:now[0])
        check=AssignedCompletionBarrier(recorder)
        endpoint.claim(); sender=SimpleNamespace(_serial=endpoint)
        capture=(recorder,endpoint,sender,now,chunks)
        identity=dict(printer_id='fixture',generation='g1',host_session='host',job_id='job',file_sha256='a'*64)
        request=dict(request_id='pause',printer_id='fixture',connection_generation='g1',expected_state_version=1,
            deadline=20.,operation='job.pause',arguments={'job_id':'job'})
        operation=journal.accept(request,now=10.,printer_id='fixture',generation='g1',state_version=1)['operation_id']
        assert journal.claim(operation,now=10.)
        yield check,capture,identity,operation
        recorder.close();endpoint.close()


def send(capture,command): return capture[1].write(command.encode()+b'\n')

def reply(check,capture,token):
    def count():
        return sum(e['event']['kind']=='serial_line_received' and 'LLM_BARRIER_' in e['event']['data'].get('text','') for e in check.recorder.read()['events'])
    prior=count()
    deliver(capture,('echo:LLM_BARRIER_'+token+'\n').encode())
    wait_for(lambda:count()==prior+1)


def test_owned_pair_and_original_marker_produce_scoped_receipt(barrier):
    check,capture,identity,op=barrier
    token=check.dispatch(op,'paused',identity,lambda c:send(capture,c))
    assert check.poll() is None
    deliver(capture,b'ok\n')
    assert check.poll() is None
    reply(check,capture,token)
    result=check.poll()
    assert result['identity']==identity and result['operation_id']==op
    assert result['receipt']['observed_at']==10.
    assert [w['sequence'] for w in result['writes']]==[1,2]
    assert all(w['confirmed'] for w in result['writes'])
    assert not result['resume_authorized'] and not result['physical_readiness_qualified']
    assert check.poll()==result
    with pytest.raises(RequestRejected):check.dispatch(op,'paused',identity,lambda c:send(capture,c))


@pytest.mark.parametrize('where',['before','between','after'])
def test_interleaved_frame_never_becomes_completion(barrier,where):
    check,capture,identity,op=barrier
    def enqueue(command):
        if where=='before' and command=='M400':send(capture,'G1 X1')
        if where=='between' and command.startswith('M118'):send(capture,'M105')
        send(capture,command)
        if where=='after' and command.startswith('M118'):send(capture,'G1 X2')
    token=check.dispatch(op,'paused',identity,enqueue)
    reply(check,capture,token)
    with pytest.raises(RequestRejected,match='interleaved'):check.poll()
    with check.recorder.journal._db() as db:
        assert db.execute('SELECT status FROM assigned_barriers').fetchone()[0]=='outcome_unknown'


def test_receive_before_write_return_is_not_lost_or_prematurely_confirmed(barrier):
    check,capture,identity,op=barrier
    writer=capture[1].backend.frame_writer
    def raw(data):
        if b'LLM_BARRIER_' in data:
            token=data.decode().strip().split('_')[-1]
            reply(check,capture,token)
            assert check.poll() is None
        return len(data)
    writer._writer=raw
    check.dispatch(op,'paused',identity,lambda c:send(capture,c))
    assert check.poll()['writes'][1]['confirmed']


@pytest.mark.parametrize('case',['stale','expired','reset','duplicate','wrong','partial','enqueue'])
def test_uncertain_paths_do_not_claim_barrier(barrier,case):
    check,capture,identity,op=barrier
    if case=='partial':capture[1].backend.frame_writer._writer=lambda data:2
    def enqueue(command):
        if case=='enqueue':raise OSError('queue unavailable')
        send(capture,command)
    if case in ('partial','enqueue'):
        with pytest.raises(OSError):check.dispatch(op,'paused',identity,enqueue)
        with pytest.raises((RequestRejected,OSError)):check.poll()
        return
    token=check.dispatch(op,'paused',identity,enqueue)
    reply(check,capture,token if case!='wrong' else '0'*32)
    if case=='stale':capture[3][0]=11.1
    if case=='expired':capture[3][0]=20.
    if case=='reset':
        deliver(capture,b'start\n')
        wait_for(lambda:check.recorder.snapshot()['controller_resets_observed']==1)
    if case=='duplicate':reply(check,capture,token)
    if case=='wrong':assert check.poll() is None
    else:
        with pytest.raises(RequestRejected):check.poll()


def test_intent_is_durable_before_queue_and_restart_does_not_replay(barrier):
    check,capture,identity,op=barrier
    def enqueue(command):
        with check.recorder.journal._db() as db:
            row=db.execute('SELECT status,intent FROM assigned_barriers').fetchone()
            assert row['status']=='dispatching' and op in row['intent']
        send(capture,command)
    check.dispatch(op,'paused',identity,enqueue)
    with pytest.raises(RequestRejected,match='prior_barrier_session'):
        AssignedCompletionBarrier(check.recorder)


@pytest.mark.parametrize('data,expected',[(b'M400\n','M400'),(b'N1 M400*38\n','M400'),
    (b'N1 M400*0\n',None),(b' M400\n',' M400'),(b'M400\r\n','M400\r')])
def test_only_exact_frame_commands(data,expected):
    assert AssignedCompletionBarrier._command(data)==expected


def test_valid_checksum_frame():
    raw=b'N10 M400'; checksum=0
    for byte in raw:checksum^=byte
    assert AssignedCompletionBarrier._command(raw+b'*'+str(checksum).encode()+b'\n')=='M400'


@pytest.mark.parametrize('change',['generation','printer','terminal'])
def test_authority_loss_after_enqueue_rejects_receipt(barrier,change):
    check,capture,identity,op=barrier
    token=check.dispatch(op,'paused',identity,lambda c:send(capture,c))
    reply(check,capture,token)
    if change=='generation':capture[1].backend.channel.generation='new'
    if change=='printer':capture[1].backend.printer_id='other'
    if change=='terminal':check.recorder.journal.finish(op,'outcome_unknown',{},now=10.)
    with pytest.raises(RequestRejected):check.poll()


def test_completed_receipt_does_not_remain_fresh_after_later_motion(barrier):
    check,capture,identity,op=barrier
    token=check.dispatch(op,'paused',identity,lambda c:send(capture,c))
    reply(check,capture,token)
    assert check.poll()
    send(capture,'G1 X10')
    with pytest.raises(RequestRejected,match='interleaved'):check.poll()


def test_identity_cannot_be_mutated_after_dispatch(barrier):
    check,capture,identity,op=barrier
    token=check.dispatch(op,'paused',identity,lambda c:send(capture,c))
    identity['job_id']='other'
    reply(check,capture,token)
    assert check.poll()['identity']['job_id']=='job'


def test_commit_latency_expiry_prevents_queue_dispatch(barrier,monkeypatch):
    check,capture,identity,op=barrier
    original=check._check_source;calls=[];queued=[]
    def source():
        calls.append(1)
        if len(calls)==2:capture[3][0]=20.
        original()
    monkeypatch.setattr(check,'_check_source',source)
    with pytest.raises(RequestRejected,match='expired_before_enqueue'):
        check.dispatch(op,'paused',identity,queued.append)
    assert queued==[]
    with check.recorder.journal._db() as db:
        assert db.execute('SELECT status FROM assigned_barriers').fetchone()[0]=='dispatching'
    with pytest.raises(RequestRejected):check.poll()


def test_resume_request_correlates_numbered_barrier_frames(barrier):
    check,capture,identity,_=barrier
    journal=check.recorder.journal
    request=dict(request_id='resume',printer_id='fixture',connection_generation='g1',expected_state_version=1,
        deadline=20.,operation='job.resume',arguments={'job_id':'job'})
    operation=journal.accept(request,now=10.,printer_id='fixture',generation='g1',state_version=1)['operation_id']
    assert journal.claim(operation,now=10.)
    numbers=iter((7,8))
    def numbered(command):
        frame=('N'+str(next(numbers))+' '+command).encode();checksum=0
        for byte in frame:checksum^=byte
        capture[1].write(frame+b'*'+str(checksum).encode()+b'\n')
    token=check.dispatch(operation,'resuming',identity,numbered)
    reply(check,capture,token)
    result=check.poll()
    assert result['phase']=='resuming' and result['operation_id']==operation
    assert [w['line_number'] for w in result['writes']]==[7,8]
    assert not result['resume_authorized'] and not result['physical_readiness_qualified']


def test_final_lifecycle_window_can_follow_an_expired_start_dispatch_deadline(barrier):
    from printer_control.assigned_barrier import AssignedCompletionWindow
    check,capture,identity,_=barrier;journal=check.recorder.journal
    request=dict(request_id='start',printer_id='fixture',connection_generation='g1',expected_state_version=1,
        deadline=12.,operation='job.start',arguments={'job_id':'job'})
    operation=journal.accept(request,now=10.,printer_id='fixture',generation='g1',state_version=1)['operation_id']
    assert journal.claim(operation,now=10.);journal.finish(operation,'completed',{},now=11.)
    capture[3][0]=25.
    token=check.dispatch(operation,'completed',identity,lambda c:send(capture,c),completion_window=AssignedCompletionWindow(24.,5.))
    reply(check,capture,token);result=check.poll()
    assert result['completion_window']['observed_at']==24. and result['completion_window']['deadline']==29.
    with journal._db() as db:record=json.loads(db.execute('SELECT intent FROM assigned_barriers').fetchone()[0])
    assert record['request']['deadline']==12. and record['deadline']==29.


def test_command_phase_cannot_use_a_lifecycle_window_to_extend_its_deadline(barrier):
    from printer_control.assigned_barrier import AssignedCompletionWindow
    check,capture,identity,operation=barrier
    with pytest.raises(RequestRejected):
        check.dispatch(operation,'paused',identity,lambda c:send(capture,c),completion_window=AssignedCompletionWindow(10.,30.))
