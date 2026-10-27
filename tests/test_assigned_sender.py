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
    backend.printer_id='unit-fixture';backend.channel=SimpleNamespace(generation='unit-g1')
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


def test_endpoint_observation_is_bound_to_read_sequence_identity_and_arrival(endpoint):
    serial,_,_=endpoint;serial.claim()
    assert serial.readline()==b'ok\n'
    packet=serial.last_observation
    assert packet.sequence==1 and packet.printer_id=='unit-fixture' and packet.connection_generation=='unit-g1'
    assert packet.received.data==b'ok\n' and packet.received.observed_at==10.
    assert serial.readline()==b'' and serial.last_observation is None and serial.read_sequence==1


def test_another_thread_cannot_consume_the_assigned_reader_stream(endpoint):
    import threading
    serial,_,_=endpoint;serial.claim();errors=[]
    def other():
        try:serial.readline()
        except OSError as exc:errors.append(str(exc))
    thread=threading.Thread(target=other);thread.start();thread.join(1)
    assert not thread.is_alive() and len(errors)==1 and serial.read_sequence==0
    assert serial.readline()==b'ok\n'


@pytest.mark.parametrize('kind',['resend','timeout'])
def test_assigned_sender_never_delegates_unqualified_replay_or_ack_credit(endpoint,monkeypatch,kind):
    from octoprint.util.comm import MachineCom
    serial,writes,_=endpoint;serial.claim();serial.write(b'M400\n')
    sender=AssignedSender.__new__(AssignedSender);sender._assigned_endpoint=serial
    inherited=[];errors=[]
    monkeypatch.setattr(MachineCom,'_handle_resend_request',lambda *a:inherited.append('resend'))
    monkeypatch.setattr(MachineCom,'_handle_timeout',lambda *a:inherited.append('timeout'))
    sender._trigger_error=lambda text,reason:errors.append((text,reason))
    if kind=='resend':sender._handle_resend_request('Resend: 1')
    else:sender._handle_timeout()
    assert not inherited
    assert serial.backend.receive_hub.fault==('assigned_resend_unqualified' if kind=='resend' else 'assigned_timeout_unqualified')
    assert len(errors)==1 and errors[0][1]=='connection'
    with pytest.raises(OSError):serial.write(b'M400\n')
    from printer_control.prompt_wire import PromptWireCommand
    with pytest.raises(OSError):serial.backend.frame_writer.write_prompt(PromptWireCommand('inspect',1).encode())
    assert writes==[b'M400\n']


@pytest.fixture
def error_sender(monkeypatch):
    """Actual MachineCom error/close methods; no reader threads or device IO."""
    import logging
    import queue
    import threading
    import octoprint.util.comm as comm
    errors, closes, states = [], [], []
    sender = AssignedSender.__new__(AssignedSender)
    sender._assigned_initialized = False
    sender._assigned_error_lock = threading.Lock()
    sender._assigned_error_closing = False
    sender._state = sender.STATE_OPERATIONAL
    sender._currentFile = None
    sender._send_m112_on_error = False
    sender._connection_closing = False
    sender._temperature_timer = sender._sd_status_timer = None
    sender._serial = SimpleNamespace(close=lambda: closes.append('closed'))
    sender._assigned_endpoint = SimpleNamespace(retire=lambda reason: None)
    sender._clear_to_send = threading.Semaphore(1)
    sender._send_queue = queue.Queue()
    sender._monitoring_active = sender._send_queue_active = True
    sender._logger = logging.getLogger('assigned-error-unit')
    sender._log = lambda *a: None
    sender.isSdPrinting = lambda: False
    sender._payload_for_error = lambda *a, **k: {}
    sender._callback = SimpleNamespace(
        on_comm_error=lambda text, reason, **kwargs: errors.append((text, reason)),
        on_comm_state_change=lambda state: states.append(state))
    monkeypatch.setattr(comm, 'settings', lambda: SimpleNamespace(getBoolean=lambda *a: False))
    monkeypatch.setattr(comm, 'eventManager', lambda: SimpleNamespace(fire=lambda *a: None))
    return sender, errors, closes, states


def test_late_firmware_error_cannot_overwrite_terminal_error_close(error_sender):
    sender, errors, closes, states = error_sender
    sender._trigger_error('transport unavailable', 'connection')
    assert sender.getState() == sender.STATE_CLOSED_WITH_ERROR
    sender._trigger_error('THERMAL RUNAWAY', 'firmware')
    assert sender.getState() == sender.STATE_CLOSED_WITH_ERROR
    assert errors == [('transport unavailable', 'connection')]
    assert closes == ['closed']
    assert states == [sender.STATE_CLOSED_WITH_ERROR]


def test_nonclosing_error_does_not_consume_later_error_closure(error_sender):
    sender, errors, closes, states = error_sender
    sender._trigger_error('diagnostic', 'connection', close=False)
    assert not closes and sender.getState() == sender.STATE_ERROR
    sender._trigger_error('transport unavailable', 'connection')
    assert sender.getState() == sender.STATE_CLOSED_WITH_ERROR
    assert len(errors) == 2 and closes == ['closed']


def test_competing_errors_share_one_terminal_close(error_sender):
    import threading
    sender, errors, closes, states = error_sender
    entered, release, attempted, second_done = (threading.Event() for _ in range(4))
    failures = []
    def close():
        closes.append('closed')
        entered.set()
        assert release.wait(2)
    sender._serial.close = close
    def trigger(text):
        try:
            if text == 'second':
                attempted.set()
            sender._trigger_error(text, 'connection')
            if text == 'second':
                second_done.set()
        except BaseException as error:
            failures.append(error)
    first = threading.Thread(target=trigger, args=('first',))
    second = threading.Thread(target=trigger, args=('second',))
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        assert attempted.wait(2)
        assert second_done.wait(1), 'duplicate error blocked behind inherited close'
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not failures and not first.is_alive() and not second.is_alive()
    assert sender.getState() == sender.STATE_CLOSED_WITH_ERROR
    assert errors == [('first', 'connection')] and closes == ['closed']
    assert states == [sender.STATE_CLOSED_WITH_ERROR]


def test_owned_error_close_never_inherits_automatic_emergency_writes(error_sender):
    sender, errors, closes, states = error_sender
    emergencies = []
    sender._send_m112_on_error = True
    sender._trigger_emergency_stop = lambda *a, **k: emergencies.append('emergency')
    sender._trigger_error('THERMAL RUNAWAY', 'firmware')
    assert not emergencies and closes == ['closed']
    assert errors == [('THERMAL RUNAWAY', 'firmware')]
    assert sender.getState() == sender.STATE_CLOSED_WITH_ERROR
