"""Device assignment checks. Discovery itself must never open a serial port."""
from dataclasses import dataclass
from typing import Callable, Iterable, TypeVar


class DeviceRejected(ValueError):
    pass


@dataclass(frozen=True)
class Device:
    path: str
    location: str
    instance_id: str  # OS connection instance; changes on unplug, unlike a port path
    vendor: int
    product: int


@dataclass(frozen=True)
class Assignment:
    printer_id: str
    device: Device
    expires_at: float
    physical_evidence: str
    protected_locations: frozenset[str]
    protected_paths: frozenset[str]

    def validate(self, devices: Iterable[Device], *, now: float) -> Device:
        import math
        if not math.isfinite(now) or not math.isfinite(self.expires_at) or now >= self.expires_at:
            raise DeviceRejected("Assignment expired; identify the development printer again")
        if not self.printer_id or not self.physical_evidence.strip() or not self.device.instance_id:
            raise DeviceRejected("Physical assignment evidence and connection instance required")
        if not self.protected_paths or not self.protected_locations:
            raise DeviceRejected("Protected printer exclusions are required")
        if self.device.path in self.protected_paths or self.device.location in self.protected_locations:
            raise DeviceRejected("Protected device")
        candidates = [d for d in devices if d.path == self.device.path or d.location == self.device.location]
        if len(candidates) != 1 or candidates[0] != self.device:
            raise DeviceRejected("Device missing, ambiguous, moved or reconnected")
        return candidates[0]


T = TypeVar("T")


def guarded_open(assignment: Assignment, discover: Callable[[], list[Device]],
                 opener: Callable[[str], T], now: Callable[[], float],
                 is_busy: Callable[[Device], bool]) -> T:
    """Opener must obtain an OS exclusive lock. No writes before final revalidation.

    The caller owns closing a successful connection. On a topology race, close it
    immediately and reject. OS-level device isolation is also required for hardware.
    """
    device = assignment.validate(discover(), now=now())
    if is_busy(device):
        raise DeviceRejected("Device already owned")
    handle = opener(device.path)
    try:
        assignment.validate(discover(), now=now())
    except BaseException:
        handle.close()
        raise
    return handle

