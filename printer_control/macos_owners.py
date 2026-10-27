"""Read-only Mac descriptor ownership metadata; never opens a serial device.

This closes the pre-existing-owner inspection gap, not the kernel/USB-driver
qualification gap. A snapshot cannot stop a later open, displace an existing
owner, or establish printer readiness. Keep terminal exclusion, assignment and
the controller lease as independent requirements.
"""
from dataclasses import dataclass
import math
import os
import re
import selectors
import stat
import subprocess
import sys
import time

from .devices import Device, DeviceRejected


class OwnerInspectionError(DeviceRejected):
    pass


@dataclass(frozen=True, order=True)
class OwnerDescriptor:
    pid: int
    fd: int


def parse_owner_descriptors(output):
    """Parse lsof -Fpf. A process without a numeric FD is incomplete evidence."""
    if type(output) is not bytes or len(output) > 16384:
        raise OwnerInspectionError('invalid_owner_snapshot')
    if not output:
        return frozenset()
    if not output.endswith(b'\n'):
        raise OwnerInspectionError('incomplete_owner_snapshot')
    owners = set()
    pid = None
    has_fd = False
    for field in output.splitlines():
        if re.fullmatch(rb'p[1-9][0-9]{0,9}', field):
            if pid is not None and not has_fd:
                raise OwnerInspectionError('owner_without_descriptor')
            pid, has_fd = int(field[1:]), False
        elif pid is not None and re.fullmatch(rb'f(?:0|[1-9][0-9]{0,9})(?:[rwu])?', field):
            fd = int(field[1:].rstrip(b'rwu'))
            owner = OwnerDescriptor(pid, fd)
            if owner in owners:
                raise OwnerInspectionError('duplicate_owner_descriptor')
            owners.add(owner)
            has_fd = True
        else:
            raise OwnerInspectionError('invalid_owner_snapshot')
    if not has_fd:
        raise OwnerInspectionError('owner_without_descriptor')
    return frozenset(owners)


def _run_lsof(arguments, timeout):
    """Bound both output streams while running one fixed, shell-free tool."""
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(['/usr/sbin/lsof', *arguments], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               close_fds=True)
    output = [bytearray(), bytearray()]
    try:
        with selectors.DefaultSelector() as poll:
            for index, stream in enumerate((process.stdout, process.stderr)):
                os.set_blocking(stream.fileno(), False)
                poll.register(stream, selectors.EVENT_READ, index)
            while poll.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise OwnerInspectionError('owner_inspection_timeout')
                for key, _ in poll.select(remaining):
                    data = os.read(key.fd, 4096)
                    if not data:
                        poll.unregister(key.fileobj)
                    else:
                        target = output[key.data]
                        if len(target) + len(data) > 16384:
                            raise OwnerInspectionError('owner_inspection_output_limit')
                        target.extend(data)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OwnerInspectionError('owner_inspection_timeout')
            try:
                status = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                raise OwnerInspectionError('owner_inspection_timeout') from None
        return status, bytes(output[0]), bytes(output[1])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _visibility_check(timeout):
    status, out, err = _run_lsof(['-h'], timeout)
    if status != 0 or b'Anyone can list all files' not in out + err:
        raise OwnerInspectionError('owner_visibility_unavailable')


def _read_owners(paths, timeout):
    status, out, err = _run_lsof(['-nP', '-Fpf', '--', *paths], timeout)
    if err or status not in (0, 1):
        raise OwnerInspectionError('owner_inspection_failed')
    owners = parse_owner_descriptors(out)
    if (status == 0) != bool(owners):
        raise OwnerInspectionError('owner_status_mismatch')
    return owners


class MacOSOwnerInspector:
    """Inspect the assigned callout and dial-in aliases, without opening either.

    is_busy is suitable for guarded_open's pre-open callback. check_owned also
    checks the exact expected descriptor, so another handle in our own process
    is not silently accepted. Both are metadata checks, with a bounded interval
    rather than an atomic claim. The caller must still fence topology and hold
    a driver-qualified exclusion and the controller lease through all I/O.
    """
    def __init__(self, *, timeout=1.):
        if sys.platform != 'darwin':
            raise OwnerInspectionError('Mac owner inspection requires macOS')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 2:
            raise ValueError('bounded positive owner inspection timeout required')
        self.timeout = timeout
        self._pid = os.getpid()

    def _check_process(self):
        if os.getpid() != self._pid:
            raise OwnerInspectionError('owner inspector belongs to another process')

    @staticmethod
    def _paths(device):
        if type(device) is not Device or type(device.path) is not str or not re.fullmatch(
                r'/dev/cu\.[A-Za-z0-9_.-]{1,128}', device.path):
            raise OwnerInspectionError('explicit_Mac_callout_device_required')
        paths = (device.path, '/dev/tty.' + device.path[len('/dev/cu.'):])
        nodes = [os.stat(path, follow_symlinks=False) for path in paths]
        # The aliases have distinct dev_t values on macOS; they must both be
        # real character nodes. Name pairing is not independent USB identity.
        if any(not stat.S_ISCHR(node.st_mode) for node in nodes):
            raise OwnerInspectionError('serial_alias_is_not_character_device')
        return paths, tuple((node.st_dev, node.st_ino, node.st_rdev) for node in nodes)

    def snapshot(self, device):
        self._check_process()
        started = time.monotonic()
        paths, before = self._paths(device)
        _visibility_check(self.timeout)
        remaining = self.timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise OwnerInspectionError('owner_inspection_timeout')
        owners = _read_owners(paths, remaining)
        _, after = self._paths(device)
        self._check_process()
        if time.monotonic() - started >= self.timeout:
            raise OwnerInspectionError('owner_inspection_timeout')
        if after != before:
            raise OwnerInspectionError('serial_alias_changed_during_inspection')
        return owners

    def is_busy(self, device):
        return bool(self.snapshot(device))

    def check_owned(self, device, fd, lease):
        from .ownership import ControllerLease
        self._check_process()
        if type(fd) is not int or fd < 0 or type(lease) is not ControllerLease:
            raise OwnerInspectionError('owned_descriptor_and_controller_lease_required')
        lease.check()
        before = os.fstat(fd)
        paths, nodes = self._paths(device)
        if not stat.S_ISCHR(before.st_mode) or before.st_rdev != nodes[0][2]:
            raise OwnerInspectionError('owned_descriptor_device_mismatch')
        owners = self.snapshot(device)
        lease.check()
        after = os.fstat(fd)
        _, current = self._paths(device)
        if ((before.st_dev, before.st_ino, before.st_rdev) !=
                (after.st_dev, after.st_ino, after.st_rdev) or current != nodes):
            raise OwnerInspectionError('owned_descriptor_changed_during_inspection')
        if owners != frozenset({OwnerDescriptor(self._pid, fd)}):
            raise OwnerInspectionError('descriptor_ownership_not_exclusive')
