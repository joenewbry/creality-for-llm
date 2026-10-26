import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import pytest
from printer_control.assigned_sender import AssignedSerialEndpoint,AssignedSender
from printer_control.tty_prompt import TTYPromptBackend
from printer_control.tty_receiver import TTYReceiveHub
from printer_control.shared_writer import SharedFrameWriter


@pytest.fixture
def endpoint():
    writes=[];chunks=[b'ok\n']
    hub=TTYReceiveHub(lambda:chunks.pop(0) if chunks else b'',lambda:None,lambda:10.,copy_jobs=True)
    backend=TTYPromptBackend.__new__(TTYPromptBackend)
    backend.receive_hub=hub
    backend.frame_writer=SharedFrameWriter(lambda data:hub.write_frame(lambda d:writes.append(d) or len(d),data))
    return AssignedSerialEndpoint(backend),writes,chunks


def test_endpoint_claim_routes_only_shared_frames_and_close_keeps_no_read_authority(endpoint):
    serial,writes,_=endpoint
    with pytest.raises(OSError):serial.write(b'M400\n')
    serial.claim()
    assert serial.readline()==b'ok\n'
    assert serial.write(b'M400\n')==5 and writes==[b'M400\n']
    serial.timeout=30.;assert serial.timeout==30. and serial.baudrate==115200
    serial.baudrate=115200
    with pytest.raises(ValueError):serial.claim()
    serial.close();serial.close();assert not serial.is_open
    with pytest.raises(OSError):serial.readline()
    with pytest.raises(OSError):serial.write(b'M400\n')
    assert len(writes)==1


@pytest.mark.parametrize('timeout',[None,0,-1,True,float('nan'),float('inf'),61,'1'])
def test_invalid_logical_timeout_does_not_change_endpoint_or_emit(endpoint,timeout):
    serial,writes,_=endpoint
    with pytest.raises(ValueError):serial.timeout=timeout
    assert serial.timeout==.05 and not writes


@pytest.mark.parametrize('port',['AUTO','VIRTUAL','','with space','/invalid/path',True,'a'*129])
def test_invalid_port_refuses_construction_without_connection_actions(endpoint,port):
    serial,writes,_=endpoint
    with pytest.raises(ValueError):AssignedSerialEndpoint(serial.backend,port=port)
    assert not writes and not serial._claimed


@pytest.mark.parametrize('baud',[9600,True,None,'115200'])
def test_baud_change_never_reconfigures_connection(endpoint,baud):
    serial,writes,_=endpoint
    with pytest.raises(ValueError):serial.baudrate=baud
    assert not writes and serial.baudrate==115200


def test_prompt_command_cannot_bypass_prompt_lane_through_endpoint(endpoint):
    from printer_control.prompt_wire import PromptWireCommand
    serial,writes,_=endpoint;serial.claim()
    with pytest.raises(ValueError):serial.write(PromptWireCommand('inspect',1).encode())
    assert not writes


def test_sender_open_refuses_retry_and_never_calls_discovery_or_another_factory(endpoint):
    serial,_,_=endpoint
    sender=AssignedSender.__new__(AssignedSender)
    sender._assigned_endpoint=serial;sender._assigned_open_attempted=False
    reset=[];sender._clear_to_send=SimpleNamespace(reset=lambda:reset.append(1))
    assert sender._open_serial(serial.port,115200)
    assert sender._serial is serial and reset==[1]
    assert not sender._open_serial(serial.port,115200,trigger_errors=False)
    assert not sender._open_serial('OTHER',115200,trigger_errors=False)
    assert serial.is_open


@pytest.mark.parametrize('scenario',['resume','native_prompt'])
def test_real_pinned_sender_uses_owned_software_terminal_once(scenario):
    if sys.platform!='linux':pytest.skip('Linux software-terminal sender integration')
    root=Path(__file__).resolve().parents[1]
    run=subprocess.run([sys.executable,'tests/assigned_sender_smoke.py','--scenario',scenario],cwd=root,capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stderr[-5000:]
    result=json.loads(run.stdout)
    assert result['completed_moves_once_in_order']==80 and 0<result['moves_at_pause']<80
    assert result['single_endpoint'] and result['physical_serial_open_or_discovery_attempts']==0
    assert not result['peer_errors'] and not result['copy_drainer_errors']
    assert result['native_prompt_receipts']==(3 if scenario=='native_prompt' else 0)


def test_invalid_sender_argument_is_rejected_before_any_runtime_initialization():
    with pytest.raises(TypeError):AssignedSender(None,callbackObject=None,printerProfileManager=None)


def test_pinned_sender_does_not_retry_a_partial_frame_or_emit_its_suffix(endpoint):
    import logging
    serial,_,_=endpoint;serial.claim();calls=[];closed=[]
    def partial(data):calls.append(data);return 1
    hub=serial.backend.receive_hub
    serial.backend.frame_writer=SharedFrameWriter(lambda data:hub.write_frame(partial,data))
    sender=AssignedSender.__new__(AssignedSender)
    sender._serial=serial;sender._serial_encoding='ascii';sender._connection_closing=False;sender._transmitted_lines=0
    sender._logger=logging.getLogger('assigned-partial-fixture');sender._log=lambda _:None
    sender.close=lambda **kwargs:closed.append(kwargs)
    sender._do_send_without_checksum(b'M400')
    assert calls==[b'M400\n'] and closed==[{'is_error':True}]
    assert hub.fault=='write_interrupted' and serial.backend.frame_writer.fault
