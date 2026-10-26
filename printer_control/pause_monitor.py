"""Continuous experimental pause sampling, independent of LLM status polling.

Not a real-time heater watchdog: firmware remains responsible for protection.
A slow/missing sample cannot authorize reheating or continuation.
"""
import threading
import time

from .thermal_pause import finite


class ThermalPauseMonitor:
    def __init__(self, driver, observe, *, interval, clock=time.monotonic):
        if not callable(observe) or not finite(interval) or not 0 < interval <= driver.policy.limits.max_observation_gap_seconds / 2:
            raise ValueError('invalid_pause_monitor_sampling')
        self.driver, self.observe, self.interval, self.clock = driver, observe, interval, clock
        self.stop_event = threading.Event()
        self.resume_event = threading.Event()
        self.cycle_lock = threading.RLock()
        self.thread = None
        self.samples = 0
        self.error = None
        self.closed = False
        service = driver.service
        with service.backend.guard():
            service.lease.check()
            if getattr(service, '_thermal_monitor', None) is not None:
                raise ValueError('pause_monitor_already_installed')
            service._thermal_monitor = self

    def start(self):
        with self.cycle_lock:
            if self.closed or self.thread is not None:
                raise ValueError('pause_monitor_cannot_restart')
            self.thread = threading.Thread(target=self._run, name='thermal-pause-monitor', daemon=True)
            self.thread.start()
        return self

    def request_resume(self):
        with self.cycle_lock:
            if self.closed or self.stop_event.is_set() or self.thread is None:
                raise ValueError('pause_monitor_not_running')
            self.resume_event.set()

    def _run(self):
        while not self.stop_event.wait(self.interval):
            with self.cycle_lock:
                if self.stop_event.is_set():
                    break
                try:
                    observation = self.observe(self.clock())
                    if self.stop_event.is_set():
                        break
                    result = self.driver.advance(observation, now=self.clock(),
                                                 request_resume=self.resume_event.is_set())
                    self.samples += 1
                    if result['state'] in ('invalid', 'consumed'):
                        self.stop_event.set()
                except Exception:
                    self.error = 'thermal_monitor_observation_or_effect_failed'
                    self.stop_event.set()
                    with self.driver.service.backend.guard():
                        self.driver.interrupt(self.error)

    def consume_resume(self):
        with self.cycle_lock:
            if self.closed or self.stop_event.is_set():
                raise ValueError('pause_monitor_not_running')
            observation = self.observe(self.clock())
            result = self.driver.consume_resume(observation, now=self.clock())
            self.stop_event.set()
            return result

    def close(self, timeout=2.):
        # Do not hold the backend guard while joining a worker that may need it.
        # Failure to join is an error, allowing service.close to retain ownership.
        if not finite(timeout) or timeout <= 0:
            raise ValueError('invalid_monitor_shutdown_timeout')
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise RuntimeError('pause_monitor_shutdown_incomplete')
        with self.cycle_lock:
            if not self.closed:
                if not self.driver.policy.consumed:
                    with self.driver.service.backend.guard():
                        self.driver.interrupt('thermal_monitor_closed_without_resume')
                self.closed = True

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
