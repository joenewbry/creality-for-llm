"""Read-only USB serial discovery through IOKit; never opens a device node.

Registry identifiers are connection identities, not authorization to control a
printer. Assignment, protected exclusions and actual driver qualification remain
required. No third-party serial package or registry text parsing is used.
"""
import ctypes as C
import re
import subprocess
import sys
import uuid

from .devices import Device, DeviceRejected


class DiscoveryError(DeviceRejected):
    pass


def devices_from_records(records, boot_session):
    try:
        boot = str(uuid.UUID(boot_session))
    except (ValueError, TypeError, AttributeError) as exc:
        raise DiscoveryError('valid boot session required') from exc
    result = []
    paths, locations, services = set(), set(), set()
    for record in records:
        if len(result) >= 256:
            raise DiscoveryError('USB serial snapshot exceeds bound')
        try:
            path = record['path']
            numeric = [record[k] for k in ('location_id', 'service_id', 'usb_id', 'vendor', 'product')]
            location, service, usb, vendor, product = numeric
            if (type(path) is not str or not re.fullmatch(r'/dev/cu\.[A-Za-z0-9_.-]+', path)
                    or any(type(v) is not int for v in numeric)
                    or not 0 < location <= 0xFFFFFFFF
                    or not 0 < service <= 0xFFFFFFFFFFFFFFFF or not 0 < usb <= 0xFFFFFFFFFFFFFFFF
                    or not 0 <= vendor <= 0xFFFF or not 0 <= product <= 0xFFFF):
                raise ValueError()
            ports = f'{location & 0xFFFFFF:06x}'.rstrip('0')
            if not ports or '0' in ports:
                raise ValueError()
            topology = f'USB{location >> 24}-' + '.'.join(str(int(c, 16)) for c in ports)
            if path in paths or topology in locations or service in services:
                raise ValueError()
            paths.add(path); locations.add(topology); services.add(service)
            result.append(Device(path, topology, f'{boot}:{service:x}:{usb:x}', vendor, product))
        except (KeyError, TypeError, ValueError) as exc:
            raise DiscoveryError('incomplete or ambiguous USB serial registry snapshot') from exc
    return sorted(result, key=lambda d: d.path)


class _Registry:
    """Small typed IOKit/CoreFoundation binding with owned-reference cleanup."""
    def __init__(self):
        self.io = C.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        def bind(lib, name, result, *arguments):
            fn = getattr(lib, name); fn.restype = result; fn.argtypes = arguments
            return fn
        u32, ptr = C.c_uint32, C.c_void_p
        self.match = bind(self.io, 'IOServiceMatching', ptr, C.c_char_p)
        self.matching = bind(self.io, 'IOServiceGetMatchingServices', C.c_int, u32, ptr, C.POINTER(u32))
        self.next = bind(self.io, 'IOIteratorNext', u32, u32)
        self.valid = bind(self.io, 'IOIteratorIsValid', C.c_bool, u32)
        self.release = bind(self.io, 'IOObjectRelease', C.c_int, u32)
        self.conforms = bind(self.io, 'IOObjectConformsTo', C.c_bool, u32, C.c_char_p)
        self.parent = bind(self.io, 'IORegistryEntryGetParentEntry', C.c_int, u32, C.c_char_p, C.POINTER(u32))
        self.entry_id = bind(self.io, 'IORegistryEntryGetRegistryEntryID', C.c_int, u32, C.POINTER(C.c_uint64))
        self.property = bind(self.io, 'IORegistryEntryCreateCFProperty', ptr, u32, ptr, ptr, u32)
        self.string = bind(self.cf, 'CFStringCreateWithCString', ptr, ptr, C.c_char_p, u32)
        self.free = bind(self.cf, 'CFRelease', None, ptr)
        self.type_id = bind(self.cf, 'CFGetTypeID', C.c_ulong, ptr)
        self.string_type = bind(self.cf, 'CFStringGetTypeID', C.c_ulong)
        self.number_type = bind(self.cf, 'CFNumberGetTypeID', C.c_ulong)
        self.length = bind(self.cf, 'CFStringGetLength', C.c_long, ptr)
        self.maximum = bind(self.cf, 'CFStringGetMaximumSizeForEncoding', C.c_long, C.c_long, u32)
        self.get_string = bind(self.cf, 'CFStringGetCString', C.c_bool, ptr, ptr, C.c_long, u32)
        self.get_number = bind(self.cf, 'CFNumberGetValue', C.c_bool, ptr, C.c_int, ptr)

    def value(self, entry, key, *, text=False):
        key_ref = self.string(None, key.encode(), 0x08000100)
        if not key_ref:
            raise DiscoveryError('registry property key allocation failed')
        try:
            value = self.property(entry, key_ref, None, 0)
        finally:
            self.free(key_ref)
        if not value:
            raise DiscoveryError('required USB serial property missing')
        try:
            if text:
                if self.type_id(value) != self.string_type():
                    raise DiscoveryError('invalid registry string type')
                size = self.maximum(self.length(value), 0x08000100) + 1
                if not 0 < size <= 8192:
                    raise DiscoveryError('registry string exceeds bound')
                buffer = C.create_string_buffer(size)
                if not self.get_string(value, buffer, size, 0x08000100):
                    raise DiscoveryError('registry string conversion failed')
                return buffer.value.decode('utf-8', errors='strict')
            if self.type_id(value) != self.number_type():
                raise DiscoveryError('invalid registry number type')
            number = C.c_int64()
            if not self.get_number(value, 4, C.byref(number)):  # kCFNumberSInt64Type
                raise DiscoveryError('registry number conversion failed')
            return number.value
        finally:
            self.free(value)

    def identity(self, entry):
        value = C.c_uint64()
        if self.entry_id(entry, C.byref(value)) or not value.value:
            raise DiscoveryError('registry connection identity unavailable')
        return value.value

    def usb_parent(self, service):
        current, owned = service, False
        try:
            for _ in range(64):
                if self.conforms(current, b'IOUSBHostDevice') or self.conforms(current, b'IOUSBDevice'):
                    owned = False  # caller assumes responsibility for retained parent
                    return current
                parent = C.c_uint32()
                if self.parent(current, b'IOService', C.byref(parent)):
                    return None  # non-USB serial service
                if owned:
                    self.release(current)
                current, owned = parent.value, True
            raise DiscoveryError('registry ancestry exceeds bound')
        finally:
            if owned:
                self.release(current)

    def records(self):
        iterator = C.c_uint32()
        matching = self.match(b'IOSerialBSDClient')
        if not matching:
            raise DiscoveryError('serial registry match allocation failed')
        # IOKit consumes the matching dictionary even when matching fails.
        status = self.matching(0, matching, C.byref(iterator))
        if status or not iterator.value:
            if iterator.value:
                self.release(iterator.value)
            raise DiscoveryError('serial registry enumeration failed')
        result = []
        count = 0
        try:
            while True:
                if not self.valid(iterator.value):
                    raise DiscoveryError('serial registry changed during enumeration')
                service = self.next(iterator.value)
                if not service:
                    break
                usb = None
                try:
                    count += 1
                    if count > 512:
                        raise DiscoveryError('serial service enumeration exceeds bound')
                    usb = self.usb_parent(service)
                    if usb is None:
                        continue
                    result.append(dict(path=self.value(service, 'IOCalloutDevice', text=True),
                        service_id=self.identity(service), usb_id=self.identity(usb),
                        location_id=self.value(usb, 'locationID'),
                        vendor=self.value(usb, 'idVendor'), product=self.value(usb, 'idProduct')))
                finally:
                    if usb is not None and usb != service:
                        self.release(usb)
                    self.release(service)
                if len(result) > 256:
                    raise DiscoveryError('serial registry exceeds bound')
            return result
        finally:
            self.release(iterator.value)


def discover_macos_usb():
    if sys.platform != 'darwin':
        raise DiscoveryError('Mac registry discovery requires macOS')
    boot = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'],
                          check=True, capture_output=True, text=True, timeout=2).stdout.strip()
    registry = _Registry()
    first = devices_from_records(registry.records(), boot)
    second = devices_from_records(registry.records(), boot)
    if first != second:
        raise DiscoveryError('USB serial snapshot changed during discovery')
    return second
