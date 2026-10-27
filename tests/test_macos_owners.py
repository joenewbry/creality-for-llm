"""Mac ownership metadata uses private ordinary files, never printer nodes."""
from dataclasses import replace
import os
import stat
import sys
from types import SimpleNamespace
import pytest
from printer_control import macos_owners as owners
from printer_control.devices import Device
from printer_control.ownership import ControllerLease

CALLOUT='/dev/'+'cu.fixture'
DIALIN='/dev/'+'tty.fixture'


def test_all_descriptors_including_same_process_are_preserved():
    assert owners.parse_owner_descriptors(b'p12\nf3\nf4u\np42\nf0r\n') == frozenset({
        owners.OwnerDescriptor(12, 3), owners.OwnerDescriptor(12, 4), owners.OwnerDescriptor(42, 0)})
    assert owners.parse_owner_descriptors(b'') == frozenset()


@pytest.mark.parametrize('data', [None, '', b'p12', b'p0\nf3\n', b'f3\n', b'p12\n',
    b'p12\np13\nf3\n', b'p12\nf3\nf3\n', b'p12\nf-1\n', b'p12\nfcwd\n',
    b'p12\nf3\nnprivate\n', b'p12\nf03\n', b'p12\nf3\n\n', b'x'*16385])
def test_malformed_or_incomplete_never_means_unowned(data):
    with pytest.raises(owners.OwnerInspectionError):
        owners.parse_owner_descriptors(data)


@pytest.mark.parametrize('status,out,err', [(0,b'',b''),(1,b'p12\nf3\n',b''),
    (2,b'',b''),(1,b'',b'warning'),(0,b'p12\nf3\n',b'warning')])
def test_command_failure_empty_success_and_warning_are_not_unowned(monkeypatch,status,out,err):
    monkeypatch.setattr(owners,'_run_lsof',lambda *a:(status,out,err))
    with pytest.raises(owners.OwnerInspectionError):
        owners._read_owners(('/fixture',),1)


def test_exact_paths_are_passed_as_arguments_and_no_owners_requires_status_one(monkeypatch):
    calls=[]
    def run(args,timeout):
        calls.append((args,timeout));return 1,b'',b''
    monkeypatch.setattr(owners,'_run_lsof',run)
    assert not owners._read_owners((CALLOUT,DIALIN),.5)
    assert calls==[(['-nP','-Fpf','--',CALLOUT,DIALIN],.5)]


@pytest.fixture
def inspector(monkeypatch):
    monkeypatch.setattr(sys,'platform','darwin')
    item=owners.MacOSOwnerInspector(timeout=1)
    device=Device(CALLOUT,'explicit-location','explicit-instance',1,2)
    nodes={device.path:SimpleNamespace(st_mode=stat.S_IFCHR,st_dev=1,st_ino=2,st_rdev=3),
           DIALIN:SimpleNamespace(st_mode=stat.S_IFCHR,st_dev=1,st_ino=4,st_rdev=5)}
    monkeypatch.setattr(os,'stat',lambda path,**kw:nodes[path])
    monkeypatch.setattr(owners,'_visibility_check',lambda _:None)
    monkeypatch.setattr(owners,'_read_owners',lambda *a:frozenset())
    return item,device,nodes


def test_preopen_busy_check_never_drops_an_owner(inspector,monkeypatch):
    item,device,_=inspector
    assert item.is_busy(device) is False
    monkeypatch.setattr(owners,'_read_owners',lambda *a:frozenset({owners.OwnerDescriptor(42,0)}))
    assert item.is_busy(device) is True


@pytest.mark.parametrize('path',[DIALIN,CALLOUT+'\n','/tmp/file','/dev/'+'cu.a/../b','/dev/cu.'])
def test_only_explicit_callout_path_can_reach_inspection(inspector,monkeypatch,path):
    item,device,_=inspector
    monkeypatch.setattr(owners,'_read_owners',lambda *a:pytest.fail('unexpected metadata inspection'))
    with pytest.raises(owners.OwnerInspectionError):item.snapshot(replace(device,path=path))


@pytest.mark.parametrize('mode',[stat.S_IFREG,stat.S_IFLNK,stat.S_IFDIR])
def test_alias_must_be_a_real_character_node(inspector,mode):
    item,device,nodes=inspector
    nodes[DIALIN].st_mode=mode
    with pytest.raises(owners.OwnerInspectionError):item.snapshot(device)


def test_alias_replacement_during_query_refuses_result(inspector,monkeypatch):
    item,device,nodes=inspector
    def query(*a):nodes[DIALIN].st_ino+=1;return frozenset()
    monkeypatch.setattr(owners,'_read_owners',query)
    with pytest.raises(owners.OwnerInspectionError,match='changed'):item.snapshot(device)


def test_inspector_cannot_be_inherited(inspector,monkeypatch):
    item,device,_=inspector
    monkeypatch.setattr(os,'getpid',lambda:item._pid+1)
    monkeypatch.setattr(owners,'_read_owners',lambda *a:pytest.fail('child queried metadata'))
    with pytest.raises(owners.OwnerInspectionError,match='another process'):item.snapshot(device)


def test_total_snapshot_time_is_bounded(inspector,monkeypatch):
    item,device,_=inspector
    ticks=iter([10.,10.1,11.])
    monkeypatch.setattr(owners.time,'monotonic',lambda:next(ticks))
    with pytest.raises(owners.OwnerInspectionError,match='timeout'):item.snapshot(device)


def test_visibility_must_be_available(monkeypatch):
    monkeypatch.setattr(owners,'_run_lsof',lambda *a:(0,b'',b'not configured'))
    with pytest.raises(owners.OwnerInspectionError,match='visibility'):owners._visibility_check(1)


def test_wrong_platform_and_invalid_timeouts_reject_before_process(monkeypatch):
    monkeypatch.setattr(sys,'platform','unsupported')
    with pytest.raises(owners.OwnerInspectionError):owners.MacOSOwnerInspector()
    monkeypatch.setattr(sys,'platform','darwin')
    for value in (True,0,-1,3,float('nan'),float('inf'),'1'):
        with pytest.raises(ValueError):owners.MacOSOwnerInspector(timeout=value)


@pytest.fixture
def owned_inspector(tmp_path,monkeypatch):
    # Acquire a real journal lease before mocking stat for device-node fixtures.
    lease=ControllerLease(tmp_path/'controller.owner').acquire()
    item=owners.MacOSOwnerInspector.__new__(owners.MacOSOwnerInspector)
    item.timeout=1.;item._pid=os.getpid()
    device=Device(CALLOUT,'location','instance',1,2)
    node=SimpleNamespace(st_mode=stat.S_IFCHR,st_dev=1,st_ino=2,st_rdev=3)
    monkeypatch.setattr(item,'_paths',lambda d:((d.path,DIALIN),((1,2,3),(1,4,5))))
    original_fstat=os.fstat
    monkeypatch.setattr(os,'fstat',lambda fd:node if fd==7000 else original_fstat(fd))
    monkeypatch.setattr(item,'snapshot',lambda d:frozenset({owners.OwnerDescriptor(item._pid,7000)}))
    try:yield item,device,lease,node
    finally:lease.close()


def test_exact_own_descriptor_is_required(owned_inspector,monkeypatch):
    item,device,lease,_=owned_inspector
    item.check_owned(device,7000,lease)
    for result in (frozenset(),frozenset({owners.OwnerDescriptor(item._pid,18)}),
                   frozenset({owners.OwnerDescriptor(item._pid,7000),owners.OwnerDescriptor(item._pid,18)}),
                   frozenset({owners.OwnerDescriptor(item._pid,7000),owners.OwnerDescriptor(42,17)})):
        monkeypatch.setattr(item,'snapshot',lambda d:result)
        with pytest.raises(owners.OwnerInspectionError,match='not_exclusive'):item.check_owned(device,7000,lease)


def test_descriptor_change_or_lease_loss_during_query_rejects(owned_inspector,monkeypatch):
    item,device,lease,node=owned_inspector
    def query(_):
        lease.close();return frozenset({owners.OwnerDescriptor(item._pid,7000)})
    monkeypatch.setattr(item,'snapshot',query)
    with pytest.raises(Exception,match='lease'):item.check_owned(device,7000,lease)


def test_descriptor_from_wrong_node_is_refused(owned_inspector,monkeypatch):
    item,device,lease,node=owned_inspector
    node.st_rdev=99
    monkeypatch.setattr(item,'snapshot',lambda _:pytest.fail('wrong descriptor reached query'))
    with pytest.raises(owners.OwnerInspectionError,match='mismatch'):item.check_owned(device,7000,lease)


@pytest.mark.skipif(sys.platform!='darwin',reason='actual Mac lsof metadata')
def test_native_lsof_sees_duplicate_descriptors_in_one_process(tmp_path):
    # Ordinary private file only. No device enumeration, serial open or commands.
    path=tmp_path/'ownership-fixture';path.write_bytes(b'fixture')
    owners._visibility_check(2)
    with path.open('rb') as file:
        duplicate=os.dup(file.fileno())
        try:
            result=owners._read_owners((str(path),),2)
            assert result==frozenset({owners.OwnerDescriptor(os.getpid(),file.fileno()),
                                      owners.OwnerDescriptor(os.getpid(),duplicate)})
        finally:os.close(duplicate)
        assert owners._read_owners((str(path),),2)==frozenset({owners.OwnerDescriptor(os.getpid(),file.fileno())})
    assert not owners._read_owners((str(path),),2)


@pytest.mark.parametrize('code,reason',[
    ('import time;time.sleep(5)','timeout'),
    ('import os;os.write(1,b"x"*32768)','output_limit'),
    ('import os;os.write(2,b"x"*32768)','output_limit')])
def test_failed_metadata_child_is_reaped_with_bounded_output(monkeypatch,code,reason):
    original=owners.subprocess.Popen
    children=[]
    def substitute(arguments,**kwargs):
        assert arguments[0]=='/usr/sbin/lsof'
        child=original([sys.executable,'-c',code],**kwargs)
        children.append(child);return child
    monkeypatch.setattr(owners.subprocess,'Popen',substitute)
    with pytest.raises(owners.OwnerInspectionError,match=reason):owners._run_lsof(['-h'],.2)
    assert len(children)==1 and children[0].poll() is not None
    assert children[0].stdout.closed and children[0].stderr.closed


def test_descriptor_replaced_during_query_is_refused(owned_inspector,monkeypatch):
    item,device,lease,node=owned_inspector
    original_fstat=os.fstat
    records=iter([SimpleNamespace(**vars(node)),SimpleNamespace(**{**vars(node),'st_ino':99})])
    monkeypatch.setattr(os,'fstat',lambda fd:next(records) if fd==7000 else original_fstat(fd))
    with pytest.raises(owners.OwnerInspectionError,match='changed'):item.check_owned(device,7000,lease)
