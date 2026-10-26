"""Explicit pinned sender attachment to an already-owned Linux connection.

No port discovery/open, reset, reconnect, physical launcher or job tool backend.
The enclosing controller retains transport and lease ownership through cleanup.
"""
import math
import re
from octoprint.util.comm import MachineCom
from .octoprint_sender import PositionWakeMixin
from .tty_prompt import TTYPromptBackend


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
        if self._closed:raise OSError('assigned endpoint closed')
        if self.backend.receive_hub.fault or self.backend.frame_writer.fault:
            raise OSError('assigned connection uncertain')
        # Authority is checked inside the hub gate on claim/read/write. A
        # separate descriptor check here races the sender's bounded read.

    def claim(self):
        self._check()
        if self._claimed:raise ValueError('endpoint already claimed')
        self.backend.receive_hub.claim_job_reader()
        self._claimed=True

    def readline(self):
        self._check()
        if not self._claimed:raise OSError('sender has not claimed endpoint')
        return self.backend.receive_hub.job_readline()

    def write(self,data):
        self._check()
        if not self._claimed:raise OSError('sender has not claimed endpoint')
        # Exceptions remain ordinary OSError/ValueError, not SerialTimeoutException:
        # the pinned sender's special timeout retry must not replay a frame.
        return self.backend.frame_writer.write_job(data)

    def close(self):
        if not self._closed:
            self.backend.receive_hub.close()
            self._closed=True
        # The controller closes the descriptor and releases its journal lease.


class AssignedSender(PositionWakeMixin,MachineCom):
    def __init__(self,endpoint,*,callbackObject,printerProfileManager):
        self._assigned_initialized=False
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
