import hashlib
import threading
import time
from types import SimpleNamespace
import pytest
from printer_control.assigned_observations import AssignedMachineEventRecorder
from printer_control.assigned_sender import AssignedSerialEndpoint
from printer_control.journal import Journal
from printer_control.ownership import ControllerLease
from printer_control.shared_writer import SharedFrameWriter
from printer_control.tty_prompt import TTYPromptBackend
from printer_control.tty_receiver import TTYReceiveHub


def wait_for(predicate):
    end=time.monotonic()+2
    while not predicate():
        assert time.monotonic()<end
        time.sleep(.005)


def make_endpoint(now,chunks):
    backend=TTYPromptBackend.__new__(TTYPromptBackend)
    backend.printer_id='fixture';backend.channel=SimpleNamespace(generation='g1')
    backend.receive_hub=TTYReceiveHub(lambda:chunks.pop(0) if chunks else b'',lambda:None,lambda:now[0],copy_jobs=True)
    backend.frame_writer=SharedFrameWriter(lambda data:len(data))
    return AssignedSerialEndpoint(backend)


@pytest.fixture
def capture(tmp_path):
    now=[10.];chunks=[];endpoint=make_endpoint(now,chunks)
    journal=Journal(tmp_path/'journal.sqlite')
    with ControllerLease(str(journal.path)+'.owner') as lease:
        endpoint.backend.controller_lease=lease
        recorder=AssignedMachineEventRecorder(journal,endpoint=endpoint,lease=lease,clock=lambda:now[0])
        endpoint.claim();sender=SimpleNamespace(_serial=endpoint)
        yield recorder,endpoint,sender,now,chunks
        now[0]=20.;recorder.close();endpoint.close()


def deliver(capture,data):
    recorder,endpoint,sender,_,chunks=capture;chunks.append(data)
    line=endpoint.readline().decode('ascii')
    assert recorder.received_hook(sender,line) is line


def test_delayed_delivery_keeps_arrival_and_exact_source_bytes(capture):
    recorder,endpoint,sender,now,chunks=capture
    raw=b'ok T:20 /0 B:18 /0\r\n';chunks.append(raw)
    line=endpoint.readline().decode('ascii');now[0]=16.
    recorder.received_hook(sender,line)
    wait_for(lambda:'temperature' in recorder.snapshot()['reports'])
    snapshot=recorder.snapshot();report=snapshot['reports']['temperature']
    assert report['observed_at']==10. and report['arrival_age_seconds']==6. and not report['arrival_fresh']
    provenance=report['data']['receive_provenance']
    assert provenance['source_sequence']==1 and provenance['sha256']==hashlib.sha256(raw).hexdigest()
    assert provenance['printer_id']=='fixture' and provenance['connection_generation']=='g1'
    assert not snapshot['physical_readiness_qualified'] and snapshot['sensor_validity'] is None
    raw_events=[r['event'] for r in recorder.read()['events'] if r['event']['kind']=='serial_line_received']
    assert raw_events[0]['data']['text'].encode('ascii')==raw
    assert not recorder.read()['recovery_authority']


@pytest.mark.parametrize('bad',['modified','replay','gap','sender','generation','future'])
def test_source_mismatch_latches_loss_and_never_changes_sender_input(capture,bad):
    recorder,endpoint,sender,now,chunks=capture
    chunks.append(b'ok\n');line=endpoint.readline().decode('ascii')
    if bad=='modified':line='ok changed\n'
    if bad=='replay':
        recorder.received_hook(sender,line)
    if bad=='gap':
        chunks.append(b'ok\n');line=endpoint.readline().decode('ascii')
    if bad=='sender':sender=SimpleNamespace(_serial=object())
    if bad=='generation':
        endpoint.backend.channel.generation='g2';chunks.append(b'ok\n');line=endpoint.readline().decode('ascii')
    if bad=='future':now[0]=9.
    assert recorder.received_hook(sender,line) is line
    assert recorder.fault=='assigned_receive_source_invalid'
    assert not recorder.snapshot()['capturing']


def test_controller_reset_retires_prior_reports_and_changes_observer_generation(capture):
    recorder,_,_,_,_=capture
    deliver(capture,b'T:20 /0\n');wait_for(lambda:'temperature' in recorder.snapshot()['reports'])
    deliver(capture,b'start\n');wait_for(lambda:recorder.snapshot()['controller_resets_observed']==1)
    assert recorder.snapshot()['reports']=={}
    deliver(capture,b'X:1 Y:2 Z:3 E:4 Count X:80 Y:160 Z:1200\n')
    wait_for(lambda:'reported_position' in recorder.snapshot()['reports'])
    report=recorder.snapshot()['reports']['reported_position']
    assert report['generation']!='g1'
    assert report['data']['receive_provenance']['connection_generation']=='g1'
    assert report['data']['homing_state'] is None and not report['data']['independent_measurement']


@pytest.mark.parametrize('invalid_clock',[None,True,float('nan'),float('inf')])
def test_invalid_snapshot_clock_never_makes_cached_reports_fresh(capture,invalid_clock):
    recorder,_,_,now,_=capture
    deliver(capture,b'T:20 /0\n');wait_for(lambda:'temperature' in recorder.snapshot()['reports'])
    now[0]=invalid_clock
    report=recorder.snapshot()['reports']['temperature']
    assert not report['arrival_fresh'] and report['arrival_age_seconds'] is None


def test_closed_endpoint_and_lost_lease_retire_freshness(capture):
    recorder,endpoint,_,_,_=capture
    deliver(capture,b'T:20 /0\n');wait_for(lambda:'temperature' in recorder.snapshot()['reports'])
    assert recorder.snapshot()['reports']['temperature']['arrival_fresh']
    endpoint.close()
    assert not recorder.snapshot()['reports']['temperature']['arrival_fresh']
    assert not recorder.read()['health']['capturing']
    recorder.close();recorder.lease.close()
    assert not recorder.snapshot()['source_usable']


def test_wrong_journal_and_late_attach_reject_before_creating_event_table(tmp_path):
    now=[10.];endpoint=make_endpoint(now,[]);journal=Journal(tmp_path/'journal.sqlite')
    with ControllerLease(tmp_path/'other.owner') as wrong:
        endpoint.backend.controller_lease=wrong
        with pytest.raises(ValueError):AssignedMachineEventRecorder(journal,endpoint=endpoint,lease=wrong,clock=lambda:10.)
    with journal._db() as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='machine_events'").fetchone()
    endpoint.claim()
    with ControllerLease(str(journal.path)+'.owner') as lease:
        endpoint.backend.controller_lease=lease
        with pytest.raises(ValueError):AssignedMachineEventRecorder(journal,endpoint=endpoint,lease=lease,clock=lambda:10.)
    endpoint.close()


def test_foreign_thread_cannot_attest_the_reader_line(capture):
    recorder,endpoint,sender,_,chunks=capture;chunks.append(b'ok\n');line=endpoint.readline().decode('ascii')
    thread=threading.Thread(target=lambda:recorder.received_hook(sender,line));thread.start();thread.join(1)
    assert not thread.is_alive() and recorder.fault=='assigned_receive_source_invalid'


def test_foreign_process_is_refused_before_inherited_locks(capture,monkeypatch):
    recorder,endpoint,sender,_,_=capture
    with recorder._source_lock,recorder._condition:
        monkeypatch.setattr('os.getpid',lambda:recorder._pid+1)
        for call in (lambda:recorder.received_hook(sender,'ok\n'),recorder.snapshot,recorder.read,recorder.close,endpoint.readline,endpoint.close):
            with pytest.raises(OSError):call()
    monkeypatch.undo()


def test_capture_refuses_an_unrelated_lease_before_journal_writes(tmp_path):
    endpoint=make_endpoint([10.],[]);journal=Journal(tmp_path/'journal.sqlite')
    with ControllerLease(str(journal.path)+'.owner') as lease:
        with pytest.raises(ValueError,match='connection owner lease'):
            AssignedMachineEventRecorder(journal,endpoint=endpoint,lease=lease,clock=lambda:10.)
    with journal._db() as db:assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='machine_events'").fetchone()
    endpoint.close()
