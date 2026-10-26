"""Metadata conversion and IOKit lifetime tests; no physical device access."""
from dataclasses import replace
import ctypes as C
import sys
from types import SimpleNamespace
import pytest
from printer_control.devices import Assignment, DeviceRejected
from printer_control.macos_discovery import DiscoveryError, devices_from_records, _Registry, discover_macos_usb

BOOT = '12345678-1234-5678-1234-567812345678'
PATH = '/dev/' + 'cu.fixture'
RECORD = dict(path=PATH, location_id=0x00144400, service_id=100, usb_id=90, vendor=0x1a86, product=0x7523)


def test_stable_connection_and_topology():
    first = devices_from_records([RECORD], BOOT)
    assert first == devices_from_records([RECORD], BOOT.upper())
    assert first[0].location == 'USB0-1.4.4.4'
    assert first[0].path == PATH and first[0].vendor == 0x1a86


@pytest.mark.parametrize('change', [dict(service_id=101), dict(usb_id=91)])
def test_replug_with_same_path_and_topology_cannot_reuse_assignment(change):
    old = devices_from_records([RECORD], BOOT)[0]
    new = devices_from_records([{**RECORD, **change}], BOOT)[0]
    assignment = Assignment('dev', old, 100, 'fixture', frozenset(['protected-port']), frozenset(['/virtual/protected']))
    with pytest.raises(DeviceRejected):
        assignment.validate([new], now=1)


def test_boot_change_invalidates_connection_even_if_registry_ids_repeat():
    old = devices_from_records([RECORD], BOOT)[0]
    new = devices_from_records([RECORD], '22345678-1234-5678-1234-567812345678')[0]
    assert old.instance_id != new.instance_id


@pytest.mark.parametrize('changes', [dict(location_id=0), dict(location_id=0x00104000),
    dict(service_id=0), dict(usb_id=-1), dict(vendor=65536), dict(product=True),
    dict(path='/virtual/wrong'), dict(path=PATH+'\n'), dict(service_id=1<<64)])
def test_incomplete_or_invalid_metadata_is_rejected(changes):
    with pytest.raises(DiscoveryError):
        devices_from_records([{**RECORD, **changes}], BOOT)


@pytest.mark.parametrize('changes', [dict(path=PATH+'2'), dict(location_id=0x00144300), dict(service_id=101)])
def test_ambiguous_path_topology_or_service_is_not_silently_deduplicated(changes):
    with pytest.raises(DiscoveryError):
        devices_from_records([RECORD,{**RECORD, **changes}],BOOT)


def test_missing_property_and_invalid_boot_fail_closed():
    with pytest.raises(DiscoveryError):devices_from_records([{}],BOOT)
    with pytest.raises(DiscoveryError):devices_from_records([RECORD],'unknown')


def test_platform_check_happens_before_subprocess_or_framework_load(monkeypatch):
    import printer_control.macos_discovery as module
    monkeypatch.setattr(sys,'platform','linux')
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:pytest.fail('unexpected subprocess'))
    monkeypatch.setattr(module.C,'CDLL',lambda *a:pytest.fail('unexpected framework load'))
    with pytest.raises(DiscoveryError):discover_macos_usb()


@pytest.mark.parametrize('changed', [False, True])
def test_discovery_requires_two_matching_snapshots(monkeypatch, changed):
    import printer_control.macos_discovery as module
    monkeypatch.setattr(sys,'platform','darwin')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        assert kwargs['timeout'] == 2
        return SimpleNamespace(stdout=BOOT+'\n')
    monkeypatch.setattr(module.subprocess,'run',run)
    snapshots=iter([[RECORD],[{**RECORD,'service_id':101} if changed else RECORD]])
    monkeypatch.setattr(module,'_Registry',lambda:SimpleNamespace(records=lambda:next(snapshots)))
    if changed:
        with pytest.raises(DiscoveryError,match='changed'):discover_macos_usb()
    else:
        assert discover_macos_usb() == devices_from_records([RECORD],BOOT)
    assert calls == [['/usr/sbin/sysctl','-n','kern.bootsessionuuid']]


def fake_registry(*, error=False, nonusb=False, invalid_iterator=False):
    registry=_Registry.__new__(_Registry)
    released=[]
    def matching(master, query, pointer):pointer._obj.value=99;return 0
    registry.match=lambda _:1
    registry.matching=matching
    services=iter([10,0])
    registry.valid=lambda _:not invalid_iterator
    registry.next=lambda _:next(services)
    registry.release=released.append
    registry.usb_parent=lambda _:None if nonusb else 20
    registry.identity=lambda entry:entry
    def value(entry,key,**kwargs):
        if error:raise DiscoveryError('property disappeared')
        return {'IOCalloutDevice':PATH,'locationID':0x00144400,'idVendor':1,'idProduct':2}[key]
    registry.value=value
    return registry,released


@pytest.mark.parametrize('case', ['success','property_error','nonusb','iterator_invalid'])
def test_enumeration_releases_owned_references_on_success_and_failure(case):
    registry,released=fake_registry(error=case=='property_error',nonusb=case=='nonusb',invalid_iterator=case=='iterator_invalid')
    if case in ('property_error','iterator_invalid'):
        with pytest.raises(DiscoveryError):registry.records()
    else:
        assert len(registry.records()) == (0 if case=='nonusb' else 1)
    assert released == ([99] if case=='iterator_invalid' else [10,99] if case=='nonusb' else [20,10,99])


def test_parent_walk_releases_intermediate_references():
    registry=_Registry.__new__(_Registry);released=[]
    registry.conforms=lambda entry,kind:entry==30 and kind==b'IOUSBHostDevice'
    def parent(entry,plane,pointer):pointer._obj.value={10:20,20:30}[entry];return 0
    registry.parent=parent;registry.release=released.append
    usb=registry.usb_parent(10)
    assert usb==30 and released==[20]  # caller owns 10 and returned 30


def test_parent_walk_releases_retained_root_for_nonusb_service():
    registry=_Registry.__new__(_Registry);released=[]
    registry.conforms=lambda *a:False
    def parent(entry,plane,pointer):
        if entry==20:return 1
        pointer._obj.value=20;return 0
    registry.parent=parent;registry.release=released.append
    assert registry.usb_parent(10) is None and released==[20]


@pytest.mark.parametrize('stage', ['property_query','type_check'])
def test_property_references_released_on_failure(stage):
    registry=_Registry.__new__(_Registry);released=[]
    registry.string=lambda *a:1
    registry.free=released.append
    def property(*args):
        if stage=='property_query':raise DiscoveryError('query failed')
        return 2
    registry.property=property
    registry.type_id=lambda _:0;registry.string_type=lambda:1
    with pytest.raises(DiscoveryError):registry.value(10,'key',text=True)
    assert released == ([1] if stage=='property_query' else [1,2])
