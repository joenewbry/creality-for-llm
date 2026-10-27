"""Native V2 counters through strict parser and the owned inspect reader."""
import json
from pathlib import Path
import pytest
from printer_control.machine_state import MachineStateReport
from printer_control.protocol import ProtocolObserver
from printer_control.journal import RequestRejected
from test_machine_state import LINE as V1,change
from test_assigned_state import inspection,emit
from test_assigned_observations import capture,deliver

NATIVE=json.loads((Path(__file__).parent/'fixtures/machine-state-timers-native.json').read_text())['line']


def timers(previous,stepper,maximum,*,uptime=1234,axes=11,flags=0):
    line=change('U',f'{uptime:08x}',NATIVE)
    line=change('I',f'{previous:08x}{stepper:08x}{maximum:08x}',line)
    line=change('C',f'{axes:02x}',line)
    return change('J',f'{flags:02x}',line)


def test_native_v2_reports_actual_timer_values_without_physical_authority():
    result=MachineStateReport.decode(NATIVE).to_dict()
    assert len(NATIVE)==299 and result['state_schema_version']==2
    state=result['inactivity']
    assert state['previous_move_ms']==1200 and state['elapsed_modulo_ms']==34
    assert state['stepper_interval_ms']==60000 and state['max_interval_ms']==120000
    assert state['disable_idle_axes']==dict(x=True,y=True,z=False,e=True)
    assert not state['stepper_predicate'] and not state['max_predicate']
    assert not any(state[x] for x in ('actual_shutdown_proven','motor_power_proven','complete_runtime_policy'))
    assert not result['physical_readiness_qualified']


def test_v1_remains_readable_and_explicitly_lacks_timer_coverage():
    result=MachineStateReport.decode(V1).to_dict()
    assert result['state_schema_version']==1 and result['inactivity'] is None


@pytest.mark.parametrize('line,elapsed,stepper,maximum',[
    (timers(0xfffffff0,33,32,uptime=0x10,flags=2),32,False,True),
    (timers(0xfffffff0,33,32,uptime=0x11,flags=3),33,True,True),
    (timers(1200,0,0,flags=1),34,True,False),
    (timers(1200,0,0,axes=0,flags=0),34,False,False),
    (timers(0,0,0x80000001,uptime=0x80000001,flags=3),0x80000001,True,True),
    (timers(0,0xffffffff,0xffffffff,uptime=0xfffffffe,flags=0),0xfffffffe,False,False),
    (timers(0,0xffffffff,0xffffffff,uptime=0xffffffff,flags=3),0xffffffff,True,True),
])
def test_unsigned_production_predicate_semantics_and_zero_interval(line,elapsed,stepper,maximum):
    state=MachineStateReport.decode(line).to_dict()['inactivity']
    assert state['elapsed_modulo_ms']==elapsed
    assert state['stepper_predicate']==stepper and state['max_predicate']==maximum
    assert not state['actual_shutdown_proven']


@pytest.mark.parametrize('bad',[
    NATIVE.replace('v2','v3'),NATIVE+' ',NATIVE.replace(' C0b',' C10'),
    NATIVE.replace(' J00',' J04'),NATIVE.replace(' J00',' J01'),
    timers(1200,60000,120000,axes=0),timers(1200,0,0,flags=0),
    timers(1200,0,0,axes=0,flags=1),NATIVE[:-1],
    NATIVE.replace('v2','v1'),V1+' I'+'0'*24+' C00 J00',
])
def test_extensions_reserved_bits_and_contradictory_timer_predicates_are_rejected(bad):
    with pytest.raises(ValueError):MachineStateReport.decode(bad)
    event,=ProtocolObserver('g1').feed((bad+'\n').encode(),10.)
    assert event.kind=='protocol_error'


def test_v1_and_v2_share_replay_identity_and_session_guard():
    observer=ProtocolObserver('g1')
    assert observer.feed((V1+'\n').encode(),10)[0].kind=='machine_state'
    assert observer.feed((NATIVE+'\n').encode(),10.1)[0].data['reason']=='machine_state_replayed'
    new=change('N','00000002',NATIVE)
    assert observer.feed((new+'\n').encode(),10.2)[0].data['inactivity']['previous_move_ms']==1200
    foreign=change('N','00000003',change('T',f'{2:032x}',new))
    assert observer.feed((foreign+'\n').encode(),10.3)[0].data['reason']=='machine_state_session_changed'


def test_owned_source_journal_carries_native_timers_without_query_or_authority(inspection):
    context,reader,operation,writes=inspection
    before=len(writes)
    report=emit(inspection,NATIVE)
    assert report['state']['inactivity']['elapsed_modulo_ms']==34
    assert report['state']['receive_provenance']['capture_id']==context[0].capture_id
    assert not report['resume_authorized'] and not report['physical_readiness_qualified']
    report['state']['inactivity']['previous_move_ms']=999
    assert reader.read(operation)['state']['inactivity']['previous_move_ms']==1200
    assert len(writes)==before
    context[3][0]+=1.1
    with pytest.raises(RequestRejected):reader.read(operation)


def test_contradictory_timer_line_retires_owned_source(inspection):
    context,reader,operation,_=inspection
    emit(inspection,NATIVE)
    deliver(context,(change('N','00000002',NATIVE).replace(' J00',' J03')+'\n').encode())
    assert context[1].backend.receive_hub.fault=='assigned_serial_protocol_error'
    with pytest.raises(RequestRejected):reader.read(operation)


def test_linked_actual_getter_and_production_timer_methods_emit_v2(tmp_path):
    from state_native_harness import build_state_native_harness
    binary=build_state_native_harness(tmp_path/'native')
    assert binary.exists()
    lines=(tmp_path/'native/linked-state-runtime.txt').read_text().splitlines()
    reports=[MachineStateReport.decode(line).to_dict() for line in lines if line.startswith('//llmstate:')]
    assert len(reports)==2
    assert all(x['inactivity']['stepper_interval_ms']==60000 for x in reports)
    assert reports[0]['waiting_for_user'] and not reports[1]['waiting_for_user']
