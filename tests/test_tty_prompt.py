"""Linux software-terminal -> real extracted firmware handlers, never USB."""
import os
import pty
import select
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace
import pytest
from printer_control.tty_prompt import TTYPromptBackend
from printer_control.devices import Device,Assignment
from printer_control.service import OperationService,StartupAdmission
from printer_control.tools import ToolRouter
from printer_control.journal import RequestRejected
from test_firmware_host_actions import actions_binary


@pytest.mark.parametrize('max_age',[0,-1,True,None,float('nan'),float('inf'),'5'])
def test_invalid_prompt_options_never_reach_transport_open(monkeypatch,max_age):
    def forbidden(*a,**k):raise AssertionError('must not acquire a device')
    monkeypatch.setattr('printer_control.tty_prompt.open_assigned_transport',forbidden)
    with pytest.raises(ValueError):TTYPromptBackend.open(SimpleNamespace(printer_id='dev'),None,None,None,None,max_age=max_age)


@pytest.mark.parametrize('printer_id',['',' ',True,42,'x'*129])
def test_invalid_identity_never_reaches_transport_open(monkeypatch,printer_id):
    def forbidden(*a,**k):raise AssertionError('must not acquire a device')
    monkeypatch.setattr('printer_control.tty_prompt.open_assigned_transport',forbidden)
    with pytest.raises(ValueError):TTYPromptBackend.open(SimpleNamespace(printer_id=printer_id),None,None,None,None)


class NativeDevice:
    """PTY master belongs to this fixture. ~ schedules only the native test idle loop."""
    def __init__(self,binary,master,scenario):
        self.master=master;self.master_closed=False;self.frames=[];self.output=[];self.errors=[];self.drop=False
        self.stop=threading.Event()
        self.process=subprocess.Popen([str(binary),scenario],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
    def loop(self):
        pending=b''
        try:
            while not self.stop.is_set():
                ready=select.select([self.master,self.process.stdout],[],[],.01)[0]
                if self.master in ready:
                    data=os.read(self.master,4096)
                    if not data:break
                    pending+=data
                    assert len(pending)<=4096
                    while b'\n' in pending:
                        frame,pending=pending.split(b'\n',1)
                        frame+=b'\n';self.frames.append(frame)
                        self.process.stdin.write(frame+b'~');self.process.stdin.flush()
                if self.process.stdout in ready:
                    data=os.read(self.process.stdout.fileno(),4096)
                    if not data:break
                    self.output.append(data)
                    if not self.drop:os.write(self.master,data)
        except (OSError,ValueError) as exc:
            if not self.stop.is_set():self.errors.append(type(exc).__name__)
    def disconnect(self):
        self.stop.set();self.thread.join(timeout=2);assert not self.thread.is_alive()
        if not self.master_closed:os.close(self.master);self.master_closed=True
    def close(self):
        self.disconnect()
        self.process.stdin.close()
        try:self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        self.process.stdout.close();self.process.stderr.close()


@pytest.fixture
def connection(tmp_path,actions_binary,request):
    if sys.platform!='linux':pytest.skip('Linux owned terminal integration')
    master,slave=pty.openpty();path=os.ttyname(slave);os.close(slave)
    device=Device(path,'fixture-port','fixture-instance',1,2);devices=[device]
    assignment=Assignment('dev',device,time.time()+60,'native PTY fixture only',frozenset({'excluded-location'}),frozenset({'excluded-path'}))
    journal=tmp_path/'printer.sqlite'
    def factory(lease,*_):return TTYPromptBackend.open(assignment,lambda:devices,time.time,lambda _:False,lease,copy_jobs=True)
    service=OperationService.from_backend_factory(journal,factory,
        readiness_check=lambda _:StartupAdmission('native-software-fixture',False))
    native=NativeDevice(actions_binary,master,getattr(request,'param','runtime'))
    router=ToolRouter(service)
    original=service.backend.transport.write
    def checked(data):
        with sqlite3.connect(journal) as db:assert db.execute("SELECT count(*) FROM operations WHERE status='running'").fetchone()[0]==1
        return original(data)
    service.backend.transport.write=checked
    try:yield service,router,native,devices,device
    finally:
        native.stop.set();native.thread.join(timeout=2);assert not native.thread.is_alive()
        if native.master_closed:
            # Disconnected Linux PTYs reject release-exclusion ioctl. The fd
            # still closes, but the first service close retains its lease.
            with pytest.raises(OSError):service.close()
            service.lease.check()
        service.close();native.close()
    assert not native.errors


def submit(router,name,args=None,*,deadline=None):
    state=router.call('printer.status',{})
    request=dict(request_id=name+uuid.uuid4().hex,printer_id='dev',connection_generation=state['connection_generation'],
        expected_state_version=state['state_version'],deadline=deadline or time.time()+3,arguments=args or {})
    return request,router.call(name,request)


def finish(router,receipt):
    end=time.monotonic()+4
    while receipt['status']=='running' and time.monotonic()<end:
        receipt=router.call('operation.get',{'operation_id':receipt['operation_id']})
    assert receipt['status']!='running'
    return receipt


def bind(router):
    for name in ('prompt.inspect','prompt.bind'):
        _,receipt=submit(router,name)
        assert finish(router,receipt)['status']=='completed'


def test_journaled_tools_resolve_native_wait_once_over_owned_descriptor(connection):
    service,router,native,_,_=connection
    bind(router)
    req,receipt=submit(router,'prompt.respond',{'prompt':1,'choice':0})
    final=finish(router,receipt)
    assert final['status']=='completed' and final['result']['resolution']=='firmware_user_wait_cleared'
    assert router.call('prompt.respond',req)==final
    assert len(native.frames)==3 and all(frame.startswith(b'M876 V1 ') and b'~' not in frame for frame in native.frames)
    assert b'STATE 0 0' in b''.join(native.output)
    assert not router.call('printer.describe',{})['physical_control_qualified']
    with pytest.raises(RequestRejected):router.call('job.start',{})


@pytest.mark.parametrize('connection',['runtime_runout'],indirect=True)
def test_native_runout_requires_intervention_and_no_repeated_wire_response(connection):
    _,router,native,_,_=connection
    bind(router)
    _,receipt=submit(router,'prompt.respond',{'prompt':1,'choice':0})
    assert finish(router,receipt)['status']=='failed'
    state=router.call('printer.status',{})
    assert state['prompt_guidance']['requires_physical_action'] is True
    before=len(native.frames)
    _,again=submit(router,'prompt.respond',{'prompt':1,'choice':0})
    assert again['status']=='rejected' and len(native.frames)==before


def test_assignment_change_invalidates_pending_receipt_and_restoring_metadata_does_not_retry(connection):
    _,router,native,devices,device=connection
    bind(router);native.drop=True
    req,pending=submit(router,'prompt.respond',{'prompt':1,'choice':0})
    devices[:]=[Device(device.path,device.location,'replacement-instance',1,2)]
    result=finish(router,pending)
    assert result['status']=='outcome_unknown'
    state=router.call('printer.status',{})
    assert state['transport_fault']=='authority_lost' and not state['session_bound']
    devices[:]=[device]
    count=len(native.frames)
    assert router.call('prompt.respond',req)==result
    assert len(native.frames)==count


def test_lost_receipt_expires_without_wire_replay(connection):
    _,router,native,_,_=connection
    bind(router);native.drop=True
    req,pending=submit(router,'prompt.respond',{'prompt':1,'choice':0},deadline=time.time()+.25)
    result=finish(router,pending)
    assert result['status']=='outcome_unknown' and result['result']['reason']=='receipt_deadline_expired'
    assert router.call('prompt.respond',req)==result
    assert len(native.frames)==3


def test_peer_disconnect_retains_unknown_outcome(connection):
    _,router,native,_,_=connection
    bind(router);native.drop=True
    _,pending=submit(router,'prompt.respond',{'prompt':1,'choice':0})
    native.disconnect()
    assert finish(router,pending)['status']=='outcome_unknown'
    assert router.call('printer.status',{})['channel_state']=='uncertain'


def test_native_prompt_and_job_line_copy_share_owned_descriptor_without_consuming_each_other(connection):
    service,router,native,_,_=connection
    bind(router)
    _,receipt=submit(router,'prompt.respond',{'prompt':1,'choice':0})
    assert receipt['status']=='running'
    lines=[]
    end=time.monotonic()+3
    while b''.join(lines).count(b'//llm:v1 ')<3 and time.monotonic()<end:
        line=service.backend.receive_hub.job_readline()
        if line:lines.append(line)
    assert b''.join(lines).count(b'//llm:v1 ')==3
    # The job consumer read the third receipt first. The prompt operation still
    # completes from its own timestamped copy without another descriptor owner.
    assert finish(router,receipt)['status']=='completed'
    while True:
        line=service.backend.receive_hub.job_readline()
        if not line:break
        lines.append(line)
    stream=b''.join(lines)
    assert stream.count(b'//llm:v1 ')==3 and b'STATE 0 0' in stream
    assert len(native.frames)==3 and not service.backend.receive_hub.fault
    assert service.backend.capabilities()['job_receive_copy_enabled']
    assert not service.backend.capabilities()['job_sender_integrated']
    service.backend.receive_hub.close()
    with pytest.raises(OSError):service.backend.frame_writer.write_job(b'M400\n')
    assert len(native.frames)==3


@pytest.mark.parametrize('mode',[0,1,None,'yes'])
def test_invalid_copy_mode_never_acquires_transport(monkeypatch,mode):
    def forbidden(*a,**k):raise AssertionError('must not open')
    monkeypatch.setattr('printer_control.tty_prompt.open_assigned_transport',forbidden)
    with pytest.raises(ValueError):
        TTYPromptBackend.open(SimpleNamespace(printer_id='dev'),None,lambda:10.,None,None,copy_jobs=mode)
