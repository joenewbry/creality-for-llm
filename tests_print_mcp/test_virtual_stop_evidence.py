from types import SimpleNamespace
import threading
import pytest
from printer_control.combined_virtual import CombinedVirtualBackend


@pytest.mark.parametrize('change', ['not_requested', 'not_killed', 'queued_moves', 'hot_nozzle', 'hot_bed', 'hot_chamber', 'missing_buffer'])
def test_stop_requires_all_virtual_quiescence_evidence(change):
    backend = object.__new__(CombinedVirtualBackend)
    device = SimpleNamespace(_killed=True,
        buffered=SimpleNamespace(all_tasks_done=threading.Condition(), unfinished_tasks=0),
        targetTemp=[0], bedTargetTemp=0, chamberTargetTemp=0)
    backend.job = SimpleNamespace(sender=SimpleNamespace(_serial=device), clock=lambda: 10)
    backend._stop_requested = True
    backend._routes = {'stop': 'printer.stop'}
    backend._stop_deadlines = {'stop': 11}
    assert backend._virtual_stop_confirmed()
    if change == 'not_requested': backend._stop_requested = False
    elif change == 'not_killed': device._killed = False
    elif change == 'queued_moves': device.buffered.unfinished_tasks = 1
    elif change == 'hot_nozzle': device.targetTemp = [200]
    elif change == 'hot_bed': device.bedTargetTemp = 60
    elif change == 'hot_chamber': device.chamberTargetTemp = 40
    elif change == 'missing_buffer': device.buffered = None
    assert not backend._virtual_stop_confirmed()
    assert backend.poll('stop') is None
    backend.job.clock = lambda: 11
    result = backend.poll('stop')
    assert result.status == 'outcome_unknown'
    assert result.detail['physical_stop_qualified'] is False
