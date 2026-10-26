"""One bounded descriptor reader feeding job lines and timestamped prompt bytes.

No device opening, background thread, reset or reconnect. Optional job copying
must be chosen before connection construction; this is not a job backend.
"""
import threading
from .shared_receiver import SharedReceiveBuffer


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
        self._lock=threading.Lock()
        self._fault=None

    @property
    def job_lane_enabled(self):
        return self._copy_jobs

    @property
    def fault(self):
        return self._fault or self._prompt.fault

    def _invalidate(self,reason):
        self._fault=self._fault or reason
        self._jobs.clear()
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
                if len(self._jobs)+len(data)>self._capacity:
                    raise ValueError('job receive copy overflow')
                # Validate before copying. No fabricated newline or truncated line.
                if any(len(line)>2048 for line in (self._jobs+data).split(b'\n')):
                    raise ValueError('job receive line too long')
            self._prompt.received_hook(None,data.decode('ascii',errors='strict'))
            if self._prompt.fault:raise OSError('prompt receive copy lost')
            if self._copy_jobs:self._jobs.extend(data)
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
            if not batch.data:
                self._pump()
                batch=self._prompt.drain_observed()
            self._check()
            return batch
        finally:self._lock.release()

    def job_readline(self):
        """One complete LF line, or empty on a bounded poll; never invent an ack."""
        if not self._copy_jobs:raise ValueError('job receive lane not enabled')
        if not self._lock.acquire(timeout=.2):raise TimeoutError('receive owner busy')
        try:
            self._check()
            if b'\n' not in self._jobs:self._pump()
            end=self._jobs.find(b'\n')
            if end<0:return b''
            line=bytes(self._jobs[:end+1])
            self._check()
            del self._jobs[:end+1]
            return line
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

    def close(self):
        if not self._lock.acquire(timeout=.2):raise TimeoutError('receive owner busy')
        try:self._invalidate('receive_connection_closed')
        finally:self._lock.release()
