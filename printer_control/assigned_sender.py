"""Explicit pinned sender attachment to an already-owned Linux connection.

No port discovery/open, reset, reconnect, physical launcher or job tool backend.
Unqualified resend/timeout handling retires the connection without replay or
manufactured acknowledgment credit. The enclosing controller retains transport
and lease ownership through cleanup.
"""
from dataclasses import dataclass
import os
import threading
import math
import re
from octoprint.util.comm import MachineCom
from .octoprint_sender import PositionWakeMixin
from .tty_prompt import TTYPromptBackend
from .shared_receiver import ObservedBytes


@dataclass(frozen=True)
class AssignedSerialObservation:
    sequence: int
    printer_id: str
    connection_generation: str
    received: ObservedBytes

    def __post_init__(self):
        if type(self.sequence) is not int or self.sequence<=0:raise ValueError('positive source sequence required')
        for value in (self.printer_id,self.connection_generation):
            if type(value) is not str or not value or len(value)>128:raise ValueError('bounded source identity required')
        if (type(self.received) is not ObservedBytes or not self.received.data
                or len(self.received.data)>2049 or not self.received.data.endswith(b'\n') or self.received.data.count(b'\n')!=1):
            raise ValueError('one complete observed line required')


class AssignedSerialEndpoint:
    """Minimal OctoPrint 1.11.8 serial hook contract, not general pySerial.

    Logical sender timeout settings are stored, while each readline performs
    one bounded transport poll. No partial line or invented ack is returned.
    """
    def __init__(self,backend,*,port='LLM_ASSIGNED'):
        if type(backend) is not TTYPromptBackend or not backend.receive_hub.job_lane_enabled:
            raise TypeError('assigned prompt backend with job copy required')
        if type(port) is not str or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}',port) or port in ('AUTO','VIRTUAL'):
            raise ValueError('explicit logical assigned-port identity required')
        self.backend=backend
        self._port=port
        self._timeout=.05
        self._claimed=False
        self._closed=False
        self._pid=os.getpid()
        self._reader_thread=None
        self._read_sequence=0
        self._last_observation=None

    @property
    def port(self):return self._port

    @property
    def baudrate(self):return 115200

    @baudrate.setter
    def baudrate(self,value):
        if type(value) is not int or value!=115200:raise ValueError('fixed assigned baud rate required')

    @property
    def timeout(self):return self._timeout

    @timeout.setter
    def timeout(self,value):
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=60:
            raise ValueError('bounded finite logical sender timeout required')
        self._timeout=value

    @property
    def is_open(self):return not self._closed

    def _check(self):
        if os.getpid()!=self._pid:raise OSError('assigned endpoint belongs to another process')
        if self._closed:raise OSError('assigned endpoint closed')
        if self.backend.receive_hub.fault or self.backend.frame_writer.fault:
            raise OSError('assigned connection uncertain')
        # Authority is checked inside the hub gate on claim/read/write. A
        # separate descriptor check here races the sender's bounded read.

    @property
    def read_sequence(self):return self._read_sequence

    @property
    def last_observation(self):return self._last_observation

    def claim(self):
        self._check()
        if self._claimed:raise ValueError('endpoint already claimed')
        self.backend.receive_hub.claim_job_reader()
        self._claimed=True
        self._reader_thread=threading.current_thread()

    def readline(self):
        self._check()
        if not self._claimed:raise OSError('sender has not claimed endpoint')
        if self._reader_thread is not threading.current_thread():raise OSError('assigned reader belongs to another thread')
        batch=self.backend.receive_hub.job_readline_observed()
        self._last_observation=None
        if batch.data:
            self._read_sequence+=1
            self._last_observation=AssignedSerialObservation(self._read_sequence,self.backend.printer_id,
                self.backend.channel.generation,batch)
        return batch.data

    def write(self,data):
        self._check()
        if not self._claimed:raise OSError('sender has not claimed endpoint')
        # Exceptions remain ordinary OSError/ValueError, not SerialTimeoutException:
        # the pinned sender's special timeout retry must not replay a frame.
        return self.backend.frame_writer.write_job(data)

    def retire(self,reason):
        if os.getpid()!=self._pid:raise OSError('assigned endpoint belongs to another process')
        self.backend.receive_hub.retire(reason)

    def close(self):
        if os.getpid()!=self._pid:raise OSError('assigned endpoint belongs to another process')
        if not self._closed:
            self.backend.receive_hub.close()
            self._closed=True
            self._last_observation=None
        # The controller closes the descriptor and releases its journal lease.


class AssignedSender(PositionWakeMixin,MachineCom):
    def __init__(self,endpoint,*,callbackObject,printerProfileManager):
        self._assigned_initialized=False
        self._assigned_error_lock=threading.RLock()
        self._assigned_error_closing=False
        if type(endpoint) is not AssignedSerialEndpoint:
            raise TypeError('explicit assigned endpoint required')
        self._assigned_endpoint=endpoint
        self._assigned_open_attempted=False
        super().__init__(port=endpoint.port,baudrate=115200,callbackObject=callbackObject,
                         printerProfileManager=printerProfileManager)
        self._assigned_initialized=True

    def __del__(self):
        if getattr(self,'_assigned_initialized',False):super().__del__()

    def _open_serial(self,port,baudrate,trigger_errors=True):
        # Bypass both plugin serial factories and the default pySerial opener.
        # A failed attempt cannot rediscover/reopen a different device.
        if self._assigned_open_attempted or port!=self._assigned_endpoint.port or baudrate!=115200:
            if trigger_errors:self._trigger_error('Assigned reopen/discovery is unqualified','connection')
            return False
        self._assigned_open_attempted=True
        try:self._assigned_endpoint.claim()
        except Exception:
            if trigger_errors:self._trigger_error('Assigned endpoint unavailable','connection')
            return False
        self._serial=self._assigned_endpoint
        self._clear_to_send.reset()
        return True

    def _perform_detection_step(self,init=False):
        self._trigger_error('Assigned autodetection is disabled','connection')

    def close(self,*args,**kwargs):
        # Error handlers and explicit close share ownership of the terminal
        # sender state. A late error may not overwrite an already closed state.
        with self._assigned_error_lock:
            return super().close(*args,**kwargs)

    def _trigger_error(self,text,reason,close=True):
        with self._assigned_error_lock:
            if self._assigned_error_closing or self._connection_closing:
                return
            if close:self._assigned_error_closing=True
            return super()._trigger_error(text,reason,close=close)

    def _retire_replay(self,reason):
        self._assigned_endpoint.retire(reason)
        # The connection reason suppresses the inherited automatic M112 path.
        # Closing does not prove stopped motion or cold heaters.
        self._trigger_error('Assigned transport requires reconciliation: '+reason,'connection')

    def _handle_resend_request(self,line):
        self._retire_replay('assigned_resend_unqualified')

    def _handle_timeout(self):
        # Do not inherit resend, probe, fake acknowledgment or queue-credit
        # behavior on a connection whose acknowledgment profile is unqualified.
        self._retire_replay('assigned_timeout_unqualified')
