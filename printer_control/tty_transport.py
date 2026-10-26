"""Bounded Linux descriptor I/O foundation, not a production printer adapter.

Consumes TTYOwnership; never opens another descriptor, flushes buffers, toggles
modem lines, reconnects or retries writes. Software-terminal tests cannot
qualify USB drivers, hardware reset behavior or operating-system device policy.
"""
import math
import os
import select
import sys
import termios
import threading

from .tty_ownership import TTYOwnership, open_assigned_tty
from .ownership import ControllerLease


def _validate_timeout(io_timeout):
    if type(io_timeout) not in (int, float) or not math.isfinite(io_timeout) or not 0 < io_timeout <= 0.1:
        raise ValueError('I/O timeout must be positive and at most 0.1 seconds')


class TTYTransport:
    """One 115200/8N1 connection; attach all writes through SharedFrameWriter.

    Reads return available bytes (not lines), or b'' on an ordinary poll timeout.
    A readiness EOF, I/O exception or incomplete write latches uncertainty.
    A descriptor write count proves only kernel acceptance, never execution.
    Caller must retain its controller lease and revalidate device generation.
    """
    def __init__(self, owner, *, io_timeout=0.1, authority_check=None):
        if sys.platform != 'linux':
            raise ValueError('terminal transport is Linux-only')
        if type(owner) is not TTYOwnership:
            raise TypeError('exclusive terminal ownership required')
        _validate_timeout(io_timeout)
        if authority_check is not None and not callable(authority_check):
            raise TypeError('authority check must be callable')
        self._authority_check = authority_check
        # Explicit transfer prevents the original handle from later closing a
        # descriptor recycled by the OS. No external code may use a cached fd.
        self._owner = owner.transfer()
        self._lock = threading.Lock()
        self._timeout = io_timeout
        self._fault = None
        self._closed = False
        try:
            fd = self._owner.fileno()
            if os.get_blocking(fd):
                raise ValueError('nonblocking descriptor required')
            attrs = termios.tcgetattr(fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
            attrs[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
            attrs[4] = attrs[5] = termios.B115200
            attrs[6][termios.VMIN] = 1
            attrs[6][termios.VTIME] = 0
            # TCSANOW preserves buffered input; no implicit reset/flush.
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except BaseException:
            self._closed = True
            self._owner.close()
            raise

    @property
    def fault(self):
        return self._fault

    def _check_authority(self):
        if self._authority_check is not None:
            try:
                self._authority_check()
            except BaseException:
                self._fault = 'authority_lost'
                raise

    def _enter(self):
        if not self._lock.acquire(timeout=self._timeout):
            raise TimeoutError('terminal operation busy; no I/O attempted')
        try:
            if self._closed:
                raise OSError('terminal transport closed')
            if self._fault is not None:
                raise OSError('terminal transport uncertain')
            self._check_authority()
            return self._owner.fileno()
        except BaseException:
            self._lock.release()
            raise

    def validate_authority(self):
        """Revalidate cached evidence without reading or exposing the descriptor."""
        self._enter()
        self._lock.release()

    def read(self, size=4096):
        if type(size) is not int or not 1 <= size <= 65536:
            raise ValueError('bounded positive read size required')
        fd = self._enter()
        try:
            ready = select.select([fd], [], [], self._timeout)[0]
            self._check_authority()
            if not ready:
                return b''
            data = os.read(fd, size)
            if not data:
                raise OSError('terminal EOF')
            return data
        except BaseException:
            self._fault = self._fault or 'read_interrupted_or_disconnected'
            raise
        finally:
            self._lock.release()

    def write(self, data):
        if type(data) is not bytes or not 1 <= len(data) <= 2048:
            raise ValueError('bounded bytes required')
        fd = self._enter()
        try:
            ready = select.select([], [fd], [], self._timeout)[1]
            self._check_authority()
            if not ready:
                raise TimeoutError('terminal write readiness deadline exceeded')
            written = os.write(fd, data)  # One attempt only, including partial writes.
            if written != len(data):
                self._fault = 'partial_write'
            return written
        except BaseException:
            self._fault = self._fault or 'write_interrupted_or_disconnected'
            raise
        finally:
            self._lock.release()

    def close(self):
        # Close cannot race I/O and accidentally operate on a reused fd. The
        # lock wait is bounded too; a caller can retry close, never replay I/O.
        if not self._lock.acquire(timeout=self._timeout):
            raise TimeoutError('terminal operation busy; close not completed')
        try:
            if not self._closed:
                self._closed = True
                self._owner.close()
        finally:
            self._lock.release()


def open_assigned_transport(assignment, discover, now, is_busy, lease, *, io_timeout=0.1):
    """Claim and configure only an assigned terminal under a caller-owned lease.

    Discovery and busy-owner inspection must be bounded, read-only OS metadata
    operations. They must never probe serial ports. Keep the lease until after
    transport close; all I/O must use the returned transport, never a cached fd.
    This factory does not establish deployment isolation or hardware readiness.
    """
    if type(lease) is not ControllerLease:
        raise TypeError('controller lease required')
    _validate_timeout(io_timeout)  # Reject configuration before opening hardware.

    def check():
        lease.check()
        assignment.validate(discover(), now=now())

    check()  # Missing lease or invalid assignment must fail before any open.
    owner = open_assigned_tty(assignment, discover, now, is_busy)
    transport = None
    try:
        lease.check()
        transport = TTYTransport(owner, io_timeout=io_timeout, authority_check=check)
        check()  # Setup must not silently outlive assignment or connection identity.
        return transport
    except BaseException:
        if transport is not None:
            transport.close()
        else:
            owner.close()
        raise
