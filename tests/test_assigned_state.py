import json
import os
import threading
from types import SimpleNamespace
import pytest
from printer_control.assigned_state import AssignedStateReader
from printer_control.journal import RequestRejected
from printer_control.prompt_backend import PromptBackend
from printer_control.prompt_channel import PromptChannel
from printer_control.shared_receiver import ObservedBytes
from test_assigned_observations import capture,deliver,wait_for
from test_machine_state import LINE,change


def response(request,session):
    return (f'//llm:v1 Q{request:08x} C00 D0 T{session:032x} P00000001 A1 H1 B01 R02 W1\n').encode()


@pytest.fixture
def inspection(capture):
    recorder,endpoint,_,now,_=capture
    writes=[];channel=PromptChannel(lambda data:writes.append(data) or len(data),'g1',session=1)
    channel.dispatch('inspect',now=8,deadline=20);channel.feed(response(1,0),now=8.1)
    channel.dispatch('bind',now=9,deadline=20);channel.feed(response(2,1),now=9.1)
    backend=endpoint.backend
    PromptBackend.__init__(backend,channel,printer_id='fixture',clock=lambda:now[0],
        pump=lambda:ObservedBytes(b'',None),close_transport=lambda:None)
    request=dict(request_id='state-fixture',printer_id='fixture',connection_generation='g1',
        expected_state_version=0,deadline=20.,operation='prompt.inspect',arguments={})
    operation=recorder.journal.accept(request,now=10,printer_id='fixture',generation='g1',state_version=0)['operation_id']
    assert recorder.journal.claim(operation,now=10)
    backend.dispatch(operation,'prompt.inspect',{},deadline=20)
    channel.feed(response(3,1),now=10.)
    result=backend.poll(operation)
    assert result.status=='completed'
    recorder.journal.finish(operation,result.status,result.detail,now=10.)
    now[0]=10.1
    return capture,AssignedStateReader(recorder),operation,writes


def emit(inspection,line=LINE):
    capture,reader,operation,_=inspection
    deliver(capture,(line+'\n').encode())
    wait_for(lambda:'machine_state' in capture[0].snapshot()['reports'])
    return reader.read(operation)


def test_owned_capture_correlates_native_wire_to_real_journal_and_prompt_channel(inspection):
    capture,reader,operation,writes=inspection
    before=len(writes)
    result=emit(inspection)
    assert result['operation_id']==operation and result['observed_at']==10.1
    assert result['state']['request']==3 and result['state']['session']==1
    assert result['state']['receive_provenance']['capture_id']==capture[0].capture_id
    assert not result['physical_readiness_qualified'] and not result['resume_authorized']
    result['state']['position']['x']=99
    assert reader.read(operation)['state']['position']['x']==.25
    assert len(writes)==before  # Reader cannot issue its own USB query.


def test_prompt_receipt_without_state_report_is_pending(inspection):
    capture,reader,operation,_=inspection
    with pytest.raises(RequestRejected,match='pending'):reader.read(operation)
    capture[3][0]=20.
    with pytest.raises(RequestRejected,match='unavailable'):reader.read(operation)


@pytest.mark.parametrize('change_kind',['request','session','status','operation','scope','generation','before_claim','late_report'])
def test_wrong_or_out_of_window_correlation_is_refused(inspection,change_kind):
    capture,reader,operation,_=inspection
    recorder,_,_,now,_=capture
    line=LINE
    if change_kind=='request':line=change('Q','00000004')
    if change_kind=='session':line=change('T',f'{2:032x}')
    if change_kind=='before_claim':now[0]=9.9
    if change_kind=='late_report':now[0]=20.
    deliver(capture,(line+'\n').encode());wait_for(lambda:'machine_state' in recorder.snapshot()['reports'])
    with recorder.journal._db() as db:
        if change_kind=='status':db.execute("UPDATE operations SET status='running' WHERE operation_id=?",(operation,))
        if change_kind in ('operation','generation'):
            row=json.loads(db.execute('SELECT request FROM operations WHERE operation_id=?',(operation,)).fetchone()[0])
            row['operation' if change_kind=='operation' else 'connection_generation']='wrong'
            db.execute('UPDATE operations SET request=? WHERE operation_id=?',(json.dumps(row),operation))
        if change_kind=='scope':
            row=json.loads(db.execute('SELECT result FROM operations WHERE operation_id=?',(operation,)).fetchone()[0]);row['scope']='not-firmware'
            db.execute('UPDATE operations SET result=? WHERE operation_id=?',(json.dumps(row),operation))
    with pytest.raises(RequestRejected):reader.read(operation)


@pytest.mark.parametrize('lost',['stale','endpoint','lease','channel','superseded'])
def test_expiry_lost_ownership_and_new_prompt_request_withdraw_usable_state(inspection,lost):
    capture,reader,operation,_=inspection
    recorder,endpoint,_,now,_=capture
    emit(inspection)
    if lost=='stale':now[0]=11.2
    if lost=='endpoint':endpoint.close()
    if lost=='lease':recorder.lease.close()
    if lost=='channel':endpoint.backend.channel.invalidate('fixture_loss',now=10.2)
    if lost=='superseded':
        endpoint.backend.channel.dispatch('inspect',now=10.2,deadline=20)
        endpoint.backend.channel.feed(response(4,1),now=10.2)
    with pytest.raises(Exception):reader.read(operation)


@pytest.mark.parametrize('bad',[LINE,change('T',f'{2:032x}',change('N','00000002')),
    '//llmstate:broken',change('P','7fc00000'+LINE.split(' P')[1].split(' ')[0][8:])])
def test_source_hazard_or_replayed_machine_report_retires_dispatch_synchronously(inspection,bad):
    capture,reader,operation,_=inspection
    recorder,endpoint,_,_,_=capture
    emit(inspection)
    deliver(capture,(bad+'\n').encode())
    assert endpoint.backend.receive_hub.fault=='assigned_serial_protocol_error'
    with pytest.raises(RequestRejected):reader.read(operation)


def test_read_failure_or_inheritance_cannot_query_another_capture(inspection,monkeypatch):
    _,reader,operation,_=inspection
    with pytest.raises(RequestRejected):reader.read('unknown')
    monkeypatch.setattr(os,'getpid',lambda:reader._pid+1)
    with pytest.raises(RequestRejected,match='another_process'):reader.read(operation)


def test_slow_final_source_check_does_not_refresh_or_backdate_state(inspection,monkeypatch):
    capture,reader,operation,_=inspection
    emit(inspection)
    recorder,_,_,now,_=capture
    original=recorder._source_usable;calls=[]
    def slow():
        calls.append(1)
        if len(calls)==2:now[0]+=1.1
        return original()
    monkeypatch.setattr(recorder,'_source_usable',slow)
    with pytest.raises(RequestRejected,match='expired_during_read'):reader.read(operation)


@pytest.mark.parametrize('age',[True,0,-1,2,float('nan'),float('inf'),'1'])
def test_age_contract_cannot_be_widened(inspection,age):
    _,reader,operation,_=inspection
    with pytest.raises(RequestRejected,match='age'):reader.read(operation,max_age=age)


def test_typed_tool_exposes_only_correlated_read_and_rejects_unknown_fields(inspection):
    from printer_control.tools import ToolRouter
    capture,reader,operation,writes=inspection
    emit(inspection)
    backend=SimpleNamespace(supported_operations=(),supported_reads=('printer.machine_state',),
        guard=threading.RLock,state_reader=reader)
    router=ToolRouter(SimpleNamespace(backend=backend,lease=capture[0].lease))
    schema=router.definitions()[0]['inputSchema']
    assert schema['required']==['operation_id'] and not schema['additionalProperties']
    before=len(writes)
    assert router.call('printer.machine_state',{'operation_id':operation})['operation_id']==operation
    with pytest.raises(RequestRejected):router.call('printer.machine_state',{'operation_id':operation,'max_age':60})
    assert len(writes)==before
