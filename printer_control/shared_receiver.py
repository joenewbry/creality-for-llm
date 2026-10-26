"""Bounded copy of the sender's receive stream; never consumes its replies.

The hook does not call back into a backend or take its lock. One prompt consumer
drains the copy. Any loss requires a new connection/receiver, not a silent reset.
"""
from collections import deque
import threading
import math
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ObservedBytes:
    data: bytes
    observed_at: float | None

    def __post_init__(self):
        if type(self.data) is not bytes:
            raise ValueError("received bytes required")
        if not self.data:
            if self.observed_at is not None:raise ValueError("empty receive has no timestamp")
        elif (type(self.observed_at) not in (int,float) or
              not math.isfinite(self.observed_at)):
            raise ValueError("finite arrival timestamp required")


class SharedReceiveBuffer:
    def __init__(self, *, capacity=65536, clock=time.time):
        if type(capacity) is not int or not 128 <= capacity <= 1048576:
            raise ValueError('bounded receive capacity required')
        if not callable(clock):raise TypeError("receive clock required")
        self._clock = clock
        self._observed_at = None
        self._last_arrival = None
        self._capacity = capacity
        self._chunks = deque()
        self._size = 0
        self._fault = None
        self._lock = threading.Lock()

    @property
    def fault(self):
        with self._lock:
            return self._fault

    def _invalidate(self, reason):
        self._fault = self._fault or reason
        self._chunks.clear()
        self._size = 0
        self._observed_at = None

    def received_hook(self, sender, line, *args, **kwargs):
        with self._lock:
            if self._fault is None:
                if type(line) is not str or len(line) > self._capacity - self._size:
                    self._invalidate('receive_copy_overflow_or_invalid_type')
                else:
                    try:
                        data = line.encode('ascii', errors='strict')
                    except UnicodeEncodeError:
                        self._invalidate('receive_copy_non_ascii')
                    else:
                        if data:
                            try:
                                observed = self._clock()
                                if (type(observed) not in (int,float) or not math.isfinite(observed)
                                        or self._last_arrival is not None and observed < self._last_arrival):
                                    raise ValueError('invalid arrival clock')
                            except Exception:
                                self._invalidate('receive_copy_clock_invalid')
                            else:
                                self._last_arrival = observed
                                if self._observed_at is None:self._observed_at = observed
                                self._chunks.append(data)
                                self._size += len(data)
        return line  # preserve OctoPrint's exact input and acknowledgment accounting

    def drain(self):
        """Byte-only compatibility copy; do not use for prompt freshness."""
        return self.drain_observed().data

    def drain_observed(self):
        """Conservative oldest-byte arrival time; draining cannot renew age."""
        with self._lock:
            if self._fault is not None:
                raise OSError(self._fault)
            data = ObservedBytes(b''.join(self._chunks), self._observed_at)
            self._chunks.clear()
            self._size = 0
            self._observed_at = None
            return data

    def close(self):
        with self._lock:
            self._invalidate('receive_connection_closed')
