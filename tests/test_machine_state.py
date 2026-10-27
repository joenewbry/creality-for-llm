import json
from pathlib import Path
import struct
import pytest
from printer_control.machine_state import MachineStateReport
from printer_control.protocol import ProtocolObserver

FIXTURE=json.loads((Path(__file__).parent/'fixtures/machine-state-native.json').read_text())
LINE=FIXTURE['line']


def change(field,value,line=LINE):
    tokens=line.split(' ')
    index=next(i for i,t in enumerate(tokens[1:],1) if t.startswith(field))
    tokens[index]=field+value
    return ' '.join(tokens)


def test_actual_native_serializer_values_preserve_controller_scope():
    state=MachineStateReport.decode(LINE).to_dict()
    assert state['request']==3 and state['session']==1 and state['sequence']==1
    assert state['uptime_ms']==1234
    assert state['position']==dict(x=.25,y=1.25,z=2.25,e=3.25)
    assert state['workspace_offset']==dict(x=-2,y=-1,z=0)
    assert state['home_offset']==dict(x=4,y=5,z=6)
    assert state['homed']==dict(x=True,y=False,z=True)
    assert state['trusted']==dict(x=True,y=False,z=False)
    assert state['enabled_logical']==dict(x=True,y=True,z=False,e=True)
    assert state['relative']==dict(x=False,y=False,z=False,e=True)
    assert state['planner_blocks']==7 and state['tool']==0
    assert state['feedrate_mm_s']==12.5 and state['feedrate_percent']==100 and state['flow_percent']==95
    assert state['fan_pwm']==128 and state['linear_unit_factor']==1
    assert state['temperatures']=={'T':dict(actual=23.5,target=0),'B':dict(actual=22.25,target=0)}
    assert state['firmware_running'] and state['waiting_for_user'] and state['waiting_for_heatup'] and state['leveling_active']
    assert state['position_kind']=='commanded_native'
    assert not any(state[k] for k in ('independent_measurement','physical_readiness_qualified','planner_completion_proven','sensor_health_proven'))


@pytest.mark.parametrize('bad',[None,b'',LINE+' ', ' '+LINE,LINE+'\n',LINE.replace('v1','v2'),LINE[:-1],LINE+' extra'])
def test_no_normalization_or_extension_guessing(bad):
    with pytest.raises(ValueError):MachineStateReport.decode(bad)


@pytest.mark.parametrize('field,value',[('Q','00000000'),('T','0'*32),('N','00000000'),
    ('F','10'),('H','08'),('K','08'),('E','10'),('R','10'),('A','01'),('P','F'*32)])
def test_invalid_identity_flags_tool_and_noncanonical_hex(field,value):
    with pytest.raises(ValueError):MachineStateReport.decode(change(field,value))


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf')])
def test_nonfinite_state_is_not_usable(value):
    bad=change('P',struct.pack('>f',value).hex()+LINE.split(' P')[1].split(' ')[0][8:])
    with pytest.raises(ValueError,match='nonfinite'):MachineStateReport.decode(bad)
    event,=ProtocolObserver('g1').feed((bad+'\n').encode(),10)
    assert event.kind=='protocol_error' and event.data['reason']=='nonfinite_machine_state'


def test_fragmented_report_sequence_session_and_reset_are_explicit():
    observer=ProtocolObserver('g1')
    assert observer.feed(LINE[:100].encode(),10)==[]
    event,=observer.feed((LINE[100:]+'\n').encode(),11)
    assert event.kind=='machine_state' and event.data['sequence']==1
    replay,=observer.feed((LINE+'\n').encode(),12)
    assert replay.kind=='protocol_error' and replay.data['reason']=='machine_state_replayed'
    changed=change('T',f'{2:032x}',change('N','00000002'))
    event,=observer.feed((changed+'\n').encode(),13)
    assert event.data['reason']=='machine_state_session_changed'
    observer.reset(14)
    event,=observer.feed((changed+'\n').encode(),15)
    assert event.kind=='machine_state'


def test_state_message_whitespace_stays_malformed_in_protocol_observer():
    for bad in (' '+LINE,LINE+' '):
        event,=ProtocolObserver().feed((bad+'\n').encode(),10)
        assert event.kind=='protocol_error' and event.data['reason']=='invalid_machine_state'


def test_negative_sensor_report_remains_visible_and_inhibits_heating():
    values=list(MachineStateReport.decode(LINE).values);values[5]=-15
    bad=change('V',struct.pack('>9f',*values).hex())
    state,warning=ProtocolObserver('g1').feed((bad+'\n').encode(),10)
    assert state.kind=='machine_state' and state.data['temperatures']['T']['actual']==-15
    assert warning.kind=='sensor_suspect' and not warning.data['heating_permitted']
