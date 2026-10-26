"""Experimental complete-frame write boundary; no device discovery or opening.

The enclosing sender must route ALL writes here and provide a bounded writer.
This does not establish ownership, profile authorization or machine completion.
"""
import math
import re
import threading

from .prompt_wire import PromptWireCommand


class SharedFrameWriter:
    def __init__(self, writer, *, lock_timeout=1.0):
        if type(lock_timeout) not in (int, float) or not math.isfinite(lock_timeout) or lock_timeout <= 0:
            raise ValueError('positive finite lock timeout required')
        self._writer = writer
        self._lock_timeout = lock_timeout
        self._lock = threading.Lock()
        self._fault = None

    @property
    def fault(self):
        return self._fault

    def write_prompt(self, data):
        PromptWireCommand.decode(data)
        return self._write(data)

    def write_job(self, data):
        if (type(data) is not bytes or not 1 < len(data) <= 2048 or
                not data.endswith(b'\n') or any(b < 32 or b > 126 for b in data[:-1])):
            raise ValueError('one complete ASCII job line required')
        if re.match(rb'(?:N[0-9]+\s+)?M876(?:\s|\*|$)', data[:-1].lstrip(), flags=re.I):
            raise ValueError('prompt commands require the prompt lane')
        return self._write(data)

    def _write(self, data):
        if not self._lock.acquire(timeout=self._lock_timeout):
            # No bytes from this caller were written. The in-flight writer owns
            # its outcome; a caller must not infer that its machine action stopped.
            raise TimeoutError('frame writer busy')
        try:
            if self._fault is not None:
                raise OSError('shared transport is uncertain')
            try:
                written = self._writer(data)
            except BaseException:
                # Even cancellation can interrupt a write after an unknown prefix.
                self._fault = 'write_interrupted'
                raise
            if type(written) is not int or written != len(data):
                self._fault = 'partial_or_unconfirmed_write'
                raise OSError('incomplete frame write')
            return written
        finally:
            self._lock.release()
