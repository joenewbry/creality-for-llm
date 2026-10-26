"""Pre-open controller construction. No physical backends are instantiated."""
import threading
from pathlib import Path
import pytest
from printer_control.journal import Journal, RequestRejected
from printer_control.service import OperationService,StartupAdmission
from printer_control.ownership import ControllerLease,OwnershipError


class Backend:
    def __init__(self):self.closed=False;self.lock=threading.RLock()
    def guard(self):return self.lock
    def close(self):self.closed=True


def admitted(_):return StartupAdmission('fixture-readiness',False)


def test_lease_and_recovery_precede_every_factory_call(tmp_path):
    path=tmp_path/'printer.sqlite';events=[];backend=Backend()
    def check(state):
        events.append('readiness')
        assert not state.has_history
        with pytest.raises(OwnershipError):ControllerLease(str(path)+'.owner').acquire()
        return admitted(state)
    def factory(lease,state,admission):
        events.append('factory');lease.check()
        with Journal(path)._db() as db:assert db.execute('SELECT count(*) FROM backend_startups').fetchone()[0]==1
        assert admission.evidence_id=='fixture-readiness'
        return backend
    with OperationService.from_backend_factory(path,factory,readiness_check=check) as service:
        assert service.backend is backend and events==['readiness','factory']
    assert backend.closed
    with ControllerLease(str(path)+'.owner'):pass


def test_competing_or_alias_owner_never_invokes_factory_or_readiness(tmp_path):
    path=tmp_path/'printer.sqlite';path.touch();alias=tmp_path/'alias';alias.symlink_to(path)
    with ControllerLease(str(path.resolve())+'.owner'):
        def forbidden(*args):raise AssertionError('must not construct backend or inspect hardware')
        with pytest.raises(OwnershipError):
            OperationService.from_backend_factory(alias,forbidden,readiness_check=forbidden)


@pytest.mark.parametrize('status',['accepted','running','outcome_unknown','completed'])
def test_history_requires_explicit_reconciliation_before_backend_creation(tmp_path,status):
    path=tmp_path/'printer.sqlite';journal=Journal(path)
    req=dict(request_id='old',printer_id='dev',connection_generation='g1',expected_state_version=0,
        deadline=20,operation='job.start',arguments={})
    receipt=journal.accept(req,now=10,printer_id='dev',generation='g1',state_version=0)
    if status!='accepted':journal.claim(receipt['operation_id'],now=10)
    if status in ('completed','outcome_unknown'):journal.finish(receipt['operation_id'],status,{'fixture':True},now=10)
    seen=[]
    def readiness(state):
        seen.append(state)
        assert state.has_history
        return StartupAdmission('unreconciled-fixture',False)
    def forbidden(*args):raise AssertionError('device factory must not run')
    with pytest.raises(RequestRejected,match='startup_requires_reconciliation'):
        OperationService.from_backend_factory(path,forbidden,readiness_check=readiness,clock=lambda:10)
    current=journal.get(receipt['operation_id'])
    assert current['status']==('completed' if status=='completed' else 'outcome_unknown')
    assert seen[0].recovered_operations==(1 if status in ('accepted','running') else 0)
    with ControllerLease(str(path)+'.owner'):pass


def test_readiness_rejection_or_invalid_admission_never_constructs_backend(tmp_path):
    for index,check in enumerate([lambda _:True,lambda _:StartupAdmission('',False),lambda _:StartupAdmission(' '+('a'*128)+' ',False),
            lambda _:StartupAdmission('fixture',1),lambda _:(_ for _ in ()).throw(ValueError('board unknown'))]):
        calls=[]
        with pytest.raises((RequestRejected,ValueError)):
            OperationService.from_backend_factory(tmp_path/str(index),lambda *args:calls.append(args),readiness_check=check)
        assert calls==[]


def test_camera_journal_is_rejected_without_recovering_camera_operations(tmp_path):
    path=tmp_path/'camera.sqlite';journal=Journal(path)
    with journal._db() as db:db.execute('CREATE TABLE camera_binding (binding TEXT)')
    req=dict(request_id='cam',printer_id='cam',connection_generation='g1',expected_state_version=0,
        deadline=20,operation='camera.set_controls',arguments={})
    receipt=journal.accept(req,now=10,printer_id='cam',generation='g1',state_version=0)
    def forbidden(*args):raise AssertionError('must not open hardware')
    with pytest.raises(RequestRejected,match='camera_journal'):
        OperationService.from_backend_factory(path,forbidden,readiness_check=forbidden,clock=lambda:10)
    assert journal.get(receipt['operation_id'])==receipt


def test_factory_failure_releases_lease_and_partial_cleanup_belongs_to_factory(tmp_path):
    path=tmp_path/'printer.sqlite';resources=[]
    def factory(lease,*_):
        lease.check();backend=Backend();resources.append(backend)
        try:raise OSError('fixture connection failed')
        finally:backend.close()
    with pytest.raises(OSError):OperationService.from_backend_factory(path,factory,readiness_check=admitted)
    assert resources[0].closed
    with ControllerLease(str(path)+'.owner'):pass


def test_qualified_reconciliation_preserves_old_unknown_receipt(tmp_path):
    path=tmp_path/'printer.sqlite';j=Journal(path)
    req=dict(request_id='old',printer_id='dev',connection_generation='g1',expected_state_version=0,
        deadline=20,operation='job.pause',arguments={})
    old=j.accept(req,now=10,printer_id='dev',generation='g1',state_version=0)
    j.claim(old['operation_id'],now=10)
    j.finish(old['operation_id'],'outcome_unknown',{'fixture':True},now=10)
    def check(state):
        assert state.unknown_operations==1
        return StartupAdmission('independent-fixture-reconciliation',True)
    with OperationService.from_backend_factory(path,lambda *_:Backend(),readiness_check=check,clock=lambda:10):pass
    assert j.get(old['operation_id'])['status']=='outcome_unknown'


def test_readiness_cannot_replace_lease_and_then_construct_backend(tmp_path):
    path=tmp_path/'printer.sqlite';calls=[]
    def check(state):
        lock=Path(str(path)+'.owner');lock.unlink();lock.touch()
        return admitted(state)
    with pytest.raises(OwnershipError):
        OperationService.from_backend_factory(path,lambda *_:calls.append(True),readiness_check=check)
    assert calls==[]


def test_linux_owned_transport_uses_the_service_lease_before_any_open(tmp_path):
    import sys,os,pty,select,time
    if sys.platform!='linux':pytest.skip('Linux software terminal integration')
    from printer_control.devices import Device,Assignment
    from printer_control.tty_transport import open_assigned_transport
    from printer_control.shared_writer import SharedFrameWriter
    master,slave=pty.openpty();path=os.ttyname(slave);os.close(slave)
    device=Device(path,'fixture-location','fixture-instance',1,2)
    assignment=Assignment('dev',device,time.time()+30,'PTY fixture only',frozenset({'excluded-location'}),frozenset({'excluded-path'}))
    class TerminalBackend(Backend):
        def __init__(self,transport):super().__init__();self.transport=transport
        def close(self):self.transport.close();super().close()
    def factory(lease,*_):
        transport=open_assigned_transport(assignment,lambda:[device],time.time,lambda _:False,lease)
        return TerminalBackend(transport)
    try:
        with OperationService.from_backend_factory(tmp_path/'tty.sqlite',factory,readiness_check=admitted) as service:
            assert SharedFrameWriter(service.backend.transport.write).write_job(b'M105\n')==5
            assert select.select([master],[],[],1)[0]
            assert os.read(master,100)==b'M105\n'
            os.write(master,b'ok\n')
            assert service.backend.transport.read()==b'ok\n'
        fd=os.open(path,os.O_RDWR|os.O_NOCTTY);os.close(fd)
    finally:os.close(master)


def test_failed_backend_open_intent_survives_without_operation_receipts(tmp_path):
    path=tmp_path/'printer.sqlite'
    def failed(*_):raise OSError('fixture open uncertain')
    with pytest.raises(OSError):OperationService.from_backend_factory(path,failed,readiness_check=admitted)
    seen=[]
    def check(state):
        seen.append(state)
        assert state.has_history and state.previous_backend_attempts==1
        assert state.unknown_operations==0 and state.recovered_operations==0
        return admitted(state)
    def forbidden(*_):raise AssertionError('must not reopen after uncertain construction')
    with pytest.raises(RequestRejected,match='reconciliation'):
        OperationService.from_backend_factory(path,forbidden,readiness_check=check)
    assert len(seen)==1


def test_prior_clean_close_is_not_current_machine_state_evidence(tmp_path):
    path=tmp_path/'printer.sqlite'
    with OperationService.from_backend_factory(path,lambda *_:Backend(),readiness_check=admitted):pass
    with pytest.raises(RequestRejected,match='reconciliation'):
        OperationService.from_backend_factory(path,lambda *_:Backend(),readiness_check=admitted)


def test_lease_loss_while_persisting_startup_cannot_reach_factory(tmp_path):
    path=tmp_path/'printer.sqlite';ticks=[];calls=[]
    def clock():
        ticks.append(1)
        if len(ticks)==2:
            lock=Path(str(path)+'.owner');lock.unlink();lock.touch()
        return 10
    with pytest.raises(OwnershipError):
        OperationService.from_backend_factory(path,lambda *_:calls.append(True),readiness_check=admitted,clock=clock)
    assert calls==[]


def test_camera_cannot_repurpose_printer_startup_history_without_operations(tmp_path):
    from printer_control.camera import CameraBroker
    path=tmp_path/'printer.sqlite'
    with OperationService.from_backend_factory(path,lambda *_:Backend(),readiness_check=admitted):pass
    with pytest.raises(RequestRejected,match='printer_startup_journal'):
        CameraBroker(path,object(),camera_id='cam',printer_id='dev',capture_owner='owner')
    with Journal(path)._db() as db:
        assert db.execute('SELECT count(*) FROM backend_startups').fetchone()[0]==1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='camera_binding'").fetchone()
