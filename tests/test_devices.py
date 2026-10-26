from dataclasses import replace
import pytest
from printer_control.devices import Device, Assignment, DeviceRejected, guarded_open


DEV = Device("/virtual/development", "hub.port4", "instance-2", 0x1A86, 0x7523)
PROD = Device("/virtual/protected", "hub.port3", "instance-1", 0x1A86, 0x7523)
ASSIGN = Assignment("development", DEV, 100, "Owner identified new connection", frozenset([PROD.location]), frozenset([PROD.path]))


@pytest.mark.parametrize("devices", [[], [PROD], [DEV, DEV], [replace(DEV, instance_id="new")], [replace(DEV, location=PROD.location)]])
def test_missing_ambiguous_reconnected_or_moved_device_never_opens(devices):
    opened = []
    with pytest.raises(DeviceRejected):
        guarded_open(ASSIGN, lambda: devices, opened.append, lambda: 1, lambda _: False)
    assert opened == []


def test_protected_assignment_never_opens():
    opened = []
    with pytest.raises(DeviceRejected):
        guarded_open(replace(ASSIGN, device=PROD), lambda: [DEV, PROD], opened.append, lambda: 1, lambda _: False)
    assert opened == []


@pytest.mark.parametrize("now", [100, 101, float('nan'), float('inf')])
def test_expired_or_invalid_time_never_opens(now):
    with pytest.raises(DeviceRejected):
        ASSIGN.validate([DEV], now=now)


def test_busy_device_never_opens():
    opened = []
    with pytest.raises(DeviceRejected):
        guarded_open(ASSIGN, lambda: [DEV, PROD], opened.append, lambda: 1, lambda _: True)
    assert opened == []


def test_topology_change_during_open_closes_without_write():
    class Handle:
        closed = False
        def close(self): self.closed = True
    handle = Handle()
    snapshots = iter([[DEV, PROD], [replace(DEV, instance_id="changed"), PROD]])
    with pytest.raises(DeviceRejected):
        guarded_open(ASSIGN, lambda: next(snapshots), lambda _: handle, lambda: 1, lambda _: False)
    assert handle.closed


def test_explicit_assignment_only_opens_development_path():
    opened = []
    guarded_open(ASSIGN, lambda: [PROD, DEV], opened.append, lambda: 1, lambda _: False)
    assert opened == [DEV.path]


def test_interrupted_post_open_validation_closes_handle():
    class Handle:
        closed = False
        def close(self): self.closed = True
    handle = Handle()
    count = 0
    def discover():
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt()
        return [DEV, PROD]
    with pytest.raises(KeyboardInterrupt):
        guarded_open(ASSIGN, discover, lambda _: handle, lambda: 1, lambda _: False)
    assert handle.closed
