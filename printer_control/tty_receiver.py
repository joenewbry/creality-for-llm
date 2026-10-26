"""One bounded descriptor reader feeding job lines and timestamped prompt bytes.

No device opening, background thread, reset or reconnect. Optional job copying
must be chosen before connection construction; this is not a job backend.
"""
import threading
import re
import time
from collections import deque
from .shared_receiver import SharedReceiveBuffer,ObservedBytes


class _OrderedGate:
    """Bounded FIFO admission; polling readers cannot overtake waiting writes."""
    def __init__(self):
        self._condition=threading.Condition()
        self._waiters=deque()
        self._owner=None

    def acquire(self,timeout):
        end=time.monotonic()+timeout
        token=object()
        with self._condition:
            if len(self._waiters)>=64:return False
            self._waiters.append(token)
            try:
                while self._owner is not None or self._waiters[0] is not token:
                    remaining=end-time.monotonic()
                    if remaining<=0:return False
                    self._condition.wait(remaining)
                if time.monotonic()>=end:return False
                self._waiters.popleft()
                self._owner=threading.get_ident()
                return True
            finally:
                if token in self._waiters:
                    self._waiters.remove(token)
                    self._condition.notify_all()

    def release(self):
        with self._condition:
            if self._owner!=threading.get_ident():raise RuntimeError('connection gate not owned')
            self._owner=None
            self._condition.notify_all()


class TTYReceiveHub:
    def __init__(self, read, check_authority, clock, *, copy_jobs=False, capacity=65536):
        if not callable(read) or not callable(check_authority) or not callable(clock):
            raise TypeError('bounded reader and authority checker required')
        if type(copy_jobs) is not bool:
            raise ValueError('explicit job copy mode required')
        self._clock=clock
        self._arrived_at=None
        self._prompt=SharedReceiveBuffer(capacity=capacity,clock=lambda:self._arrived_at)
        self._read,self._authority=read,check_authority
        self._copy_jobs=copy_jobs
        self._capacity=capacity
        self._jobs=bytearray()
        self._job_arrivals=deque()
        self._lock=_OrderedGate()
        self._fault=None
        self._fault_lock=threading.Lock()
        self._job_reader_claimed=False

    @property
    def job_lane_enabled(self):
        return self._copy_jobs

    @property
    def fault(self):
        with self._fault_lock:reason=self._fault
        return reason or self._prompt.fault

    def _latch_fault(self,reason):
        with self._fault_lock:self._fault=self._fault or reason

    def _invalidate(self,reason):
        self._latch_fault(reason)
        self._jobs.clear()
        self._job_arrivals.clear()
        self._prompt.close()

    def _check(self):
        if self.fault:raise OSError('receive connection uncertain')
        try:self._authority()
        except BaseException:
            self._invalidate('authority_lost')
            raise

    def _pump(self):
        try:
            data=self._read()
            self._arrived_at=self._clock()  # Before any later metadata wait.
            if type(data) is not bytes or len(data)>4096:
                raise ValueError('bounded receive bytes required')
            self._check()  # A blocking read cannot outlive assignment authority.
            if not data:return
            if self._copy_jobs:
                if len(self._job_arrivals)>=4096:raise ValueError('job arrival metadata overflow')
                if len(self._jobs)+len(data)>self._capacity:
                    raise ValueError('job receive copy overflow')
                # Validate before copying. No fabricated newline or truncated line.
                if any(len(line)>2048 for line in (self._jobs+data).split(b'\n')):
                    raise ValueError('job receive line too long')
            self._prompt.received_hook(None,data.decode('ascii',errors='strict'))
            if self._prompt.fault:raise OSError('prompt receive copy lost')
            if self._copy_jobs:
                self._jobs.extend(data)
                self._job_arrivals.append((len(data),self._arrived_at))
        except BaseException:
            self._invalidate('receive_stream_lost')
            raise

    def prompt_pump(self):
        if not self._lock.acquire(timeout=.2):raise TimeoutError('receive owner busy')
        try:
            self._check()
            # First drain captured bytes; do not wait on the descriptor when a
            # sender read already supplied evidence. Preserve original arrival age.
            batch=self._prompt.drain_observed()
            if not batch.data and not self._job_reader_claimed:
                self._pump()
                batch=self._prompt.drain_observed()
            self._check()
            return batch
        finally:self._lock.release()

    def claim_job_reader(self):
        """One-way handoff: sender reads the descriptor; prompts drain copies."""
        if not self._copy_jobs:raise ValueError('job receive lane not enabled')
        if not self._lock.acquire(timeout=.2):raise TimeoutError('connection owner busy')
        try:
            self._check()
            if self._job_reader_claimed:raise ValueError('job reader already claimed')
            self._job_reader_claimed=True
        finally:self._lock.release()

    def job_readline(self):
        """Byte compatibility view; evidence consumers use the observed form."""
        return self.job_readline_observed().data

    def job_readline_observed(self):
        """A complete LF line and its oldest constituent byte arrival time."""
        if not self._copy_jobs:raise ValueError('job receive lane not enabled')
        if not self._lock.acquire(timeout=.2):raise TimeoutError('receive owner busy')
        try:
            self._check()
            if b'\n' not in self._jobs:self._pump()
            end=self._jobs.find(b'\n')
            if end<0:return ObservedBytes(b'',None)
            line=bytes(self._jobs[:end+1])
            observed_at=self._job_arrivals[0][1]
            self._check()
            del self._jobs[:end+1]
            remaining=end+1
            while remaining:
                length,stamp=self._job_arrivals.popleft()
                used=min(length,remaining);remaining-=used
                if used<length:self._job_arrivals.appendleft((length-used,stamp))
            return ObservedBytes(line,observed_at)
        finally:self._lock.release()

    def write_frame(self,write,data):
        """SharedFrameWriter calls here so receive-loss and dispatch share a gate."""
        if not self._lock.acquire(timeout=.2):raise TimeoutError('connection owner busy')
        try:
            self._check()
            try:
                written=write(data)
                if type(written) is not int or written!=len(data):
                    raise OSError('partial or unconfirmed frame write')
                self._check()
                return written
            except BaseException:
                self._invalidate('write_interrupted')
                raise
        finally:self._lock.release()

    def retire(self,reason):
        """Latch uncertainty before waiting for an in-flight bounded I/O call.

        No reset, retry or stop command. A write already in progress still has
        an unknown outcome; all later gated reads/writes reject this owner.
        """
        if type(reason) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,127}',reason):
            raise ValueError('bounded retirement reason required')
        self._latch_fault(reason)
        if not self._lock.acquire(timeout=.2):return False
        try:self._invalidate(reason)
        finally:self._lock.release()
        return True

    def close(self):
        if not self._lock.acquire(timeout=.2):raise TimeoutError('receive owner busy')
        try:self._invalidate('receive_connection_closed')
        finally:self._lock.release()
