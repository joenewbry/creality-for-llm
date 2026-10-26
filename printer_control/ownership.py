"""Process ownership for a controller journal; never locks or opens a USB port."""
import fcntl
import os
from pathlib import Path


class OwnershipError(RuntimeError):
    pass


class ControllerLease:
    """Keep the lock file in place permanently: unlinking allows split ownership.

    Every process using a controller journal must use this lease. This is an
    advisory process lock, not a replacement for exclusive serial ownership.
    """
    def __init__(self, path):
        self.path = Path(path).absolute()
        self._fd = None
        self._pid = os.getpid()

    def acquire(self):
        if self._fd is not None:
            raise OwnershipError('Lease already acquired')
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise OwnershipError('Another controller owns this journal') from exc
        except BaseException:
            os.close(fd)
            raise
        self._pid = os.getpid()
        self._fd = fd
        try:
            self.check()
        except BaseException:
            self.close()
            raise
        return self

    def check(self):
        if self._fd is None or os.getpid() != self._pid:
            raise OwnershipError('Controller lease is not owned by this process')
        try:
            opened = os.fstat(self._fd)
            current = os.stat(self.path, follow_symlinks=False)
        except OSError as exc:
            raise OwnershipError('Controller lock file disappeared') from exc
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise OwnershipError('Controller lock file was replaced')

    def close(self):
        if self._fd is not None:
            # In a forked child do not unlock the parent's shared open-file lock.
            # Closing the child's descriptor leaves the parent's lock intact.
            if os.getpid() == self._pid:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()
