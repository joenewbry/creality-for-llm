"""Bounded copy of the sender's receive stream; never consumes its replies.

The hook does not call back into a backend or take its lock. One prompt consumer
drains the copy. Any loss requires a new connection/receiver, not a silent reset.
"""
from collections import deque
import threading


class SharedReceiveBuffer:
    def __init__(self, *, capacity=65536):
        if type(capacity) is not int or not 128 <= capacity <= 1048576:
            raise ValueError('bounded receive capacity required')
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
                            self._chunks.append(data)
                            self._size += len(data)
        return line  # preserve OctoPrint's exact input and acknowledgment accounting

    def drain(self):
        with self._lock:
            if self._fault is not None:
                raise OSError(self._fault)
            data = b''.join(self._chunks)
            self._chunks.clear()
            self._size = 0
            return data

    def close(self):
        with self._lock:
            self._invalidate('receive_connection_closed')
