"""Linux terminal ownership foundation; not a commissioned USB transport.

No baud, modem-control, read or write operations are performed here. Opening a
real USB serial device can itself change hardware signals: software-terminal
qualification is not permission to open hardware. Device permissions/isolation
and an enclosing controller lease are still required by a production adapter.
"""
import fcntl
import os
import stat
import sys
import termios

from .devices import DeviceRejected, guarded_open


class TTYOwnership:
    """Own one descriptor; a future transport must use it without reopening.

    Kernel exclusion blocks later ordinary opens, not pre-existing descriptors
    or privileged processes. Callers must supply a reliable busy-owner check.
    """
    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        if self._fd is None:
            raise ValueError('terminal ownership is closed')
        return self._fd

    def close(self):
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            # Explicitly release terminal exclusion (PTY masters can otherwise
            # keep it alive after this slave descriptor closes).
            fcntl.ioctl(fd, termios.TIOCNXCL)
        finally:
            os.close(fd)

    def __enter__(self):
        self.fileno()
        return self

    def __exit__(self, *_):
        self.close()


def _acquire(path):
    if sys.platform != 'linux':
        raise DeviceRejected('Kernel terminal exclusivity is not qualified on this platform')
    required = ('O_NOFOLLOW', 'O_CLOEXEC', 'O_NOCTTY', 'O_NONBLOCK')
    if not all(hasattr(os, name) for name in required) or not all(
            hasattr(termios, name) for name in ('TIOCEXCL', 'TIOCNXCL')):
        raise DeviceRejected('Kernel terminal exclusivity is unavailable')
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOCTTY | os.O_NONBLOCK
    fd = os.open(path, flags)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode) or not os.isatty(fd):
            raise DeviceRejected('Assigned path is not a terminal')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(fd, termios.TIOCEXCL)
    except BaseException:
        os.close(fd)
        raise
    return TTYOwnership(fd)


def open_assigned_tty(assignment, discover, now, is_busy):
    """Validate assignment before and after acquiring kernel exclusion.

    This is an ownership primitive, not a sender factory: it cannot configure,
    reset or communicate with the printer. No automatic discovery fallback.
    """
    return guarded_open(assignment, discover, _acquire, now, is_busy)
