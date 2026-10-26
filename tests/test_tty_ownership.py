"""Actual OS ownership checks using PTYs only; never physical serial ports."""
from dataclasses import replace
import errno
import fcntl
import os
import pty
import sys
import pytest
from printer_control.devices import Assignment, Device, DeviceRejected
from printer_control.tty_ownership import open_assigned_tty


@pytest.fixture
def terminals():
    if sys.platform != 'linux':
        pytest.skip('kernel exclusivity qualification is Linux-only')
    pairs = [pty.openpty(), pty.openpty()]
    devices = [Device(os.ttyname(slave), f'virtual-hub.{i}', f'pty-{i}', 0, 0)
               for i, (_, slave) in enumerate(pairs)]
    # Keep masters for fixture lifetime; no pre-existing slave owners.
    for _, slave in pairs:
        os.close(slave)
    assignment = Assignment('development', devices[0], 100, 'PTY fixture assignment',
                            frozenset([devices[1].location]), frozenset([devices[1].path]))
    yield assignment, devices
    for master, _ in pairs:
        os.close(master)


def acquire(assignment, devices):
    return open_assigned_tty(assignment, lambda: devices, lambda: 1, lambda _: False)


def test_kernel_rejects_noncooperating_open_and_close_releases(terminals):
    assignment, devices = terminals
    with acquire(assignment, devices) as owner:
        assert not os.get_inheritable(owner.fileno())
        try:
            unexpected = os.open(devices[0].path, os.O_RDWR | os.O_NOCTTY)
        except OSError as denied:
            assert denied.errno == errno.EBUSY
        else:
            os.close(unexpected)
            pytest.fail('kernel permitted competing open')
        # Protected fixture remains independently usable; no global hub lock.
        other = os.open(devices[1].path, os.O_RDWR | os.O_NOCTTY)
        os.close(other)
    with pytest.raises(ValueError):
        owner.fileno()
    owner.close()  # idempotent, cannot close an unrelated reused descriptor
    reopened = os.open(devices[0].path, os.O_RDWR | os.O_NOCTTY)
    os.close(reopened)


def test_existing_cooperating_owner_is_not_displaced(terminals):
    assignment, devices = terminals
    fd = os.open(devices[0].path, os.O_RDWR | os.O_NOCTTY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OSError):
            acquire(assignment, devices)
        assert os.isatty(fd)
    finally:
        os.close(fd)


def test_assignment_rejected_before_any_open(terminals, monkeypatch):
    assignment, devices = terminals
    calls = []
    monkeypatch.setattr(os, 'open', lambda *a, **k: calls.append(a))
    with pytest.raises(DeviceRejected):
        acquire(replace(assignment, device=devices[1]), devices)
    assert calls == []


def test_topology_race_releases_descriptor_and_kernel_exclusion(terminals):
    assignment, devices = terminals
    snapshots = iter([devices, [replace(devices[0], instance_id='replugged'), devices[1]]])
    with pytest.raises(DeviceRejected):
        open_assigned_tty(assignment, lambda: next(snapshots), lambda: 1, lambda _: False)
    reopened = os.open(devices[0].path, os.O_RDWR | os.O_NOCTTY)
    os.close(reopened)


def test_symlink_is_rejected_without_following_it(terminals, tmp_path):
    assignment, devices = terminals
    alias = tmp_path/'alias'; alias.symlink_to(devices[0].path)
    device = replace(devices[0], path=str(alias))
    with pytest.raises(OSError):
        acquire(replace(assignment, device=device), [device, devices[1]])


def test_regular_file_is_not_treated_as_terminal(terminals, tmp_path):
    assignment, devices = terminals
    path = tmp_path/'ordinary'; path.write_bytes(b'unchanged')
    device = replace(devices[0], path=str(path))
    with pytest.raises(DeviceRejected):
        acquire(replace(assignment, device=device), [device, devices[1]])
    assert path.read_bytes() == b'unchanged'


def test_unqualified_platform_rejected_before_open(monkeypatch):
    from printer_control.tty_ownership import _acquire
    calls = []
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(os, 'open', lambda *a, **k: calls.append(a))
    with pytest.raises(DeviceRejected, match='not qualified'):
        _acquire('/virtual/not-opened')
    assert calls == []
