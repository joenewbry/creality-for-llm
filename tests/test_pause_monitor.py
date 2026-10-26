import threading
import time
from dataclasses import replace

import pytest

from printer_control.checkpoint import PauseCheckpoint
from printer_control.ownership import OwnershipError
from printer_control.pause_effects import DurablePauseEffects
from printer_control.pause_monitor import ThermalPauseMonitor
from printer_control.thermal_pause import ThermalPausePolicy
from test_pause_effects import owner
from test_thermal_pause import fixture, observation


def driver_for(service):
    base=fixture()
    policy=ThermalPausePolicy(PauseCheckpoint(base.saved),base.limits,started_at=time.monotonic())
    writes=[]
    return DurablePauseEffects(service,policy,writes.append),writes


def wait_for(predicate):
    deadline=time.monotonic()+2
    while not predicate():
        assert time.monotonic()<deadline
        time.sleep(.005)


def test_failed_sampler_invalidates_without_writes_or_restart(tmp_path):
    with owner(tmp_path/'journal') as service:
        driver,writes=driver_for(service)
        def fail(at):raise OSError('sensor unavailable')
        monitor=ThermalPauseMonitor(driver,fail,interval=.01).start()
        wait_for(monitor.stop_event.is_set)
        monitor.close()
        assert not writes and driver.policy.state=='invalid'
        assert monitor.error=='thermal_monitor_observation_or_effect_failed'
        with pytest.raises(ValueError):monitor.start()
        with pytest.raises(ValueError):monitor.request_resume()
        assert not monitor.thread.is_alive()


def test_stalled_observer_retains_service_ownership_and_cannot_dispatch_after_close(tmp_path):
    path=tmp_path/'journal'
    service=owner(path)
    driver,writes=driver_for(service)
    entered,release=threading.Event(),threading.Event()
    def blocked(at):
        entered.set()
        release.wait(3)
        return observation(driver.policy,at,motor_hold_until=at+30)
    monitor=ThermalPauseMonitor(driver,blocked,interval=.01).start()
    try:
        assert entered.wait(1)
        with pytest.raises(RuntimeError,match='shutdown_incomplete'):monitor.close(timeout=.01)
        service.lease.check()
        with pytest.raises(OwnershipError):owner(path)
        assert not writes and monitor.thread.is_alive()
    finally:
        release.set()
        service.close()
    assert not writes and not monitor.thread.is_alive()
    assert driver.policy.state=='invalid'


def test_slow_sample_is_not_treated_as_fresh_or_replayed(tmp_path):
    with owner(tmp_path/'journal') as service:
        base=fixture()
        limits=replace(base.limits,max_observation_gap_seconds=.04)
        policy=ThermalPausePolicy(PauseCheckpoint(base.saved),limits,started_at=time.monotonic())
        writes=[]
        driver=DurablePauseEffects(service,policy,writes.append)
        def slow(at):
            time.sleep(.08)
            return observation(policy,at,motor_hold_until=at+30)
        with ThermalPauseMonitor(driver,slow,interval=.01) as monitor:
            wait_for(monitor.stop_event.is_set)
        assert policy.reason=='observation_gap_or_replay' and not writes


def test_second_monitor_cannot_compete_for_same_pause_session(tmp_path):
    with owner(tmp_path/'journal') as service:
        driver,writes=driver_for(service)
        monitor=ThermalPauseMonitor(driver,lambda at:None,interval=.01)
        with pytest.raises(ValueError,match='already_installed'):
            ThermalPauseMonitor(driver,lambda at:None,interval=.01)
        monitor.close()
        assert not writes
