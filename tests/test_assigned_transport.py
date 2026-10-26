"""Factory admission and authority invalidation; stubs or Linux PTYs only."""
from dataclasses import replace
import os
import pty
import sys
import pytest
from printer_control.devices import Assignment, Device, DeviceRejected
from printer_control.ownership import ControllerLease, OwnershipError
from printer_control.tty_transport import open_assigned_transport

DEV = Device('/virtual/development', 'hub.4', 'connection-1', 0, 0)
PROTECTED = Device('/virtual/protected', 'hub.3', 'protected-1', 0, 0)
ASSIGN = Assignment('development', DEV, 100, 'fixture identity',
                    frozenset([PROTECTED.location]), frozenset([PROTECTED.path]))


@pytest.mark.parametrize('case', ['no_lease','expired','protected','moved','busy'])
def test_admission_rejects_before_terminal_open(case, tmp_path, monkeypatch):
    lease = ControllerLease(tmp_path/'controller.lock')
    if case != 'no_lease':
        lease.acquire()
    opened = []
    monkeypatch.setattr(os, 'open', lambda *args: opened.append(args))
    assignment = replace(ASSIGN, device=PROTECTED) if case == 'protected' else ASSIGN
    devices = [replace(DEV, instance_id='changed'), PROTECTED] if case == 'moved' else [DEV, PROTECTED]
    try:
        with pytest.raises((OwnershipError, DeviceRejected)):
            open_assigned_transport(assignment, lambda: devices,
                                    lambda: 100 if case == 'expired' else 1,
                                    lambda _: case == 'busy', lease)
        assert opened == []
    finally:
        lease.close()


@pytest.fixture
def setup(tmp_path):
    if sys.platform != 'linux':
        pytest.skip('Linux factory PTY qualification')
    master, slave = pty.openpty(); path = os.ttyname(slave); os.close(slave)
    device = replace(DEV, path=path)
    assignment = replace(ASSIGN, device=device)
    state = {'devices':[device, PROTECTED], 'now':1}
    lease = ControllerLease(tmp_path/'controller.lock').acquire()
    transport = open_assigned_transport(assignment, lambda: state['devices'],
                                        lambda: state['now'], lambda _:False, lease)
    yield master, assignment, state, lease, transport
    transport.close(); lease.close(); os.close(master)


@pytest.mark.parametrize('loss', ['expiry','reconnect','lease_closed','lease_replaced'])
def test_lost_authority_prevents_new_bytes_and_cannot_be_restored(setup, loss, monkeypatch):
    master, assignment, state, lease, transport = setup
    if loss == 'expiry': state['now'] = 100
    elif loss == 'reconnect': state['devices'] = [replace(assignment.device, instance_id='changed'), PROTECTED]
    elif loss == 'lease_closed': lease.close()
    else:
        lease.path.unlink(); lease.path.write_text('replacement')
    writes=[]
    monkeypatch.setattr(os,'write',lambda *args:writes.append(args))
    with pytest.raises((DeviceRejected, OwnershipError)):
        transport.write(b'M105\n')
    assert transport.fault == 'authority_lost' and writes == []
    # Even restoring metadata cannot make this connection authoritative again.
    state.update(now=1, devices=[assignment.device, PROTECTED])
    with pytest.raises(OSError, match='uncertain'):
        transport.write(b'M105\n')
    assert writes == []


def test_factory_round_trip_uses_one_claimed_connection(setup):
    master, _, _, _, transport = setup
    assert transport.write(b'M105\n') == 5
    assert os.read(master,100) == b'M105\n'
    os.write(master,b'ok\n')
    assert transport.read() == b'ok\n'


def test_topology_change_during_configuration_closes_without_commands(tmp_path, monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('Linux factory PTY qualification')
    import termios
    master, slave = pty.openpty(); path = os.ttyname(slave); os.close(slave)
    device = replace(DEV, path=path); assignment = replace(ASSIGN, device=device)
    state = [device, PROTECTED]
    real_config = termios.tcsetattr
    def config(*args):
        real_config(*args)
        state[0] = replace(device, instance_id='new')
    monkeypatch.setattr(termios,'tcsetattr',config)
    writes=[]; monkeypatch.setattr(os,'write',lambda *args:writes.append(args))
    try:
        with ControllerLease(tmp_path/'controller.lock') as lease:
            with pytest.raises(DeviceRejected):
                open_assigned_transport(assignment,lambda:state,lambda:1,lambda _:False,lease)
        assert writes == []
        fd=os.open(path,os.O_RDWR|os.O_NOCTTY);os.close(fd)
    finally:
        os.close(master)


@pytest.mark.parametrize('timeout', [0, -1, .2, float('inf'), float('nan'), True, None, '0.1'])
def test_invalid_timeout_rejected_before_any_terminal_acquisition(tmp_path, monkeypatch, timeout):
    import printer_control.tty_transport as module
    attempts=[]
    def forbidden(*args):
        attempts.append(args)
        raise AssertionError('invalid configuration reached terminal acquisition')
    monkeypatch.setattr(module, 'open_assigned_tty', forbidden)
    with ControllerLease(tmp_path/'controller.lock') as lease:
        with pytest.raises(ValueError, match='I/O timeout'):
            open_assigned_transport(ASSIGN,lambda:[DEV,PROTECTED],lambda:1,lambda _:False,
                                    lease,io_timeout=timeout)
        lease.check()  # rejection must not consume the caller's controller lease
    assert attempts == []
