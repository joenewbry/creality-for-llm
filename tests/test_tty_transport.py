"""Linux PTY I/O only; no device discovery or physical serial access."""
import os
import pty
import select
import sys
import termios
import pytest
from printer_control.tty_ownership import _acquire
from printer_control.tty_transport import TTYTransport
from printer_control.shared_writer import SharedFrameWriter


@pytest.fixture
def terminal():
    if sys.platform != 'linux':
        pytest.skip('Linux terminal transport')
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    os.close(slave)
    owner = _acquire(path)
    transport = TTYTransport(owner)
    yield master, path, owner, transport
    transport.close()
    os.close(master)


def test_same_descriptor_raw_io_and_consumed_owner(terminal):
    master, path, owner, transport = terminal
    with pytest.raises(ValueError):
        owner.fileno()
    owner.close()  # cannot close the transferred descriptor
    os.write(master, b'ok\r\n\x00')
    assert transport.read() == b'ok\r\n\x00'
    assert not select.select([master], [], [], .01)[0]  # no echo
    assert SharedFrameWriter(transport.write).write_job(b'M105\n') == 5
    assert select.select([master], [], [], .1)[0]
    assert os.read(master, 100) == b'M105\n'  # no CRLF rewriting
    with pytest.raises(OSError):
        os.open(path, os.O_RDWR | os.O_NOCTTY)


def test_read_timeout_is_not_fault_and_close_releases(terminal):
    _, path, _, transport = terminal
    assert transport.read() == b'' and transport.fault is None
    transport.close()
    transport.close()
    with pytest.raises(OSError, match='closed'):
        transport.write(b'M105\n')
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY)
    os.close(fd)


def test_partial_write_latches_both_transport_and_frame_writer(terminal, monkeypatch):
    master, _, _, transport = terminal
    real_write = os.write
    calls = []
    def partial(fd, data):
        calls.append(data)
        return real_write(fd, data[:2])
    monkeypatch.setattr(os, 'write', partial)
    writer = SharedFrameWriter(transport.write)
    with pytest.raises(OSError, match='incomplete'):
        writer.write_job(b'M105\n')
    assert os.read(master, 100) == b'M1'
    assert transport.fault == 'partial_write'
    with pytest.raises(OSError):
        transport.write(b'M112\n')
    with pytest.raises(OSError):
        writer.write_job(b'M105\n')
    assert calls == [b'M105\n']


def test_write_not_ready_never_retries(terminal, monkeypatch):
    _, _, _, transport = terminal
    monkeypatch.setattr(select, 'select', lambda *args: ([], [], []))
    calls = []
    monkeypatch.setattr(os, 'write', lambda *args: calls.append(args))
    with pytest.raises(TimeoutError):
        transport.write(b'M105\n')
    with pytest.raises(OSError, match='uncertain'):
        transport.write(b'M105\n')
    assert calls == []


@pytest.mark.parametrize('operation', ['read', 'write'])
def test_interruption_latches_uncertainty(terminal, monkeypatch, operation):
    _, _, _, transport = terminal
    monkeypatch.setattr(select, 'select', lambda r,w,*args: (r,w,[]))
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(os, operation, interrupt)
    with pytest.raises(KeyboardInterrupt):
        transport.read() if operation == 'read' else transport.write(b'M105\n')
    assert transport.fault is not None
    with pytest.raises(OSError, match='uncertain'):
        transport.read()


def test_close_cannot_release_descriptor_while_operation_holds_lock(terminal):
    _, _, _, transport = terminal
    transport._lock.acquire()
    try:
        with pytest.raises(TimeoutError):
            transport.close()
        assert not transport._closed
    finally:
        transport._lock.release()
    transport.close()


def test_setup_failure_consumes_and_closes_owner(monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('Linux terminal transport')
    master, slave = pty.openpty(); path = os.ttyname(slave); os.close(slave)
    owner = _acquire(path)
    def fail(*args):
        raise ValueError('setup failed')
    monkeypatch.setattr(termios, 'tcsetattr', fail)
    try:
        with pytest.raises(ValueError, match='setup failed'):
            TTYTransport(owner)
        with pytest.raises(ValueError):
            owner.fileno()
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY); os.close(fd)
    finally:
        os.close(master)


def test_unqualified_platform_rejects_before_consuming_owner(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    with pytest.raises(ValueError, match='Linux-only'):
        TTYTransport(None)


def test_eof_latches_uncertainty_without_reopen(terminal, monkeypatch):
    _, _, _, transport = terminal
    monkeypatch.setattr(select, 'select', lambda r,w,*args: (r,w,[]))
    monkeypatch.setattr(os, 'read', lambda *args: b'')
    def forbidden(*args, **kwargs):
        pytest.fail('transport attempted to reopen a path')
    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(OSError, match='EOF'):
        transport.read()
    with pytest.raises(OSError, match='uncertain'):
        transport.write(b'M105\n')


def test_master_disconnect_is_not_normal_poll_timeout():
    if sys.platform != 'linux':
        pytest.skip('Linux terminal transport')
    master, slave = pty.openpty(); path = os.ttyname(slave); os.close(slave)
    transport = TTYTransport(_acquire(path))
    os.close(master)
    try:
        with pytest.raises(OSError):
            transport.read()
        assert transport.fault is not None
        with pytest.raises(OSError, match='uncertain'):
            transport.write(b'M105\n')
    finally:
        # Linux may report EIO for TIOCNXCL on the disconnected slave; ownership
        # still closes the descriptor in its finally block. Preserve that error.
        try:
            transport.close()
        except OSError:
            assert transport._closed
        with pytest.raises(ValueError):
            transport._owner.fileno()
