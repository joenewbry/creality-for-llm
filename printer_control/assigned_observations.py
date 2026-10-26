"""Durable source-bound assigned serial reports. Never readiness authority."""
import copy
import hashlib
import math
import os
from pathlib import Path
import threading
from .assigned_sender import AssignedSerialEndpoint,AssignedSerialObservation
from .machine_events import MachineEventRecorder
from .ownership import ControllerLease


class AssignedMachineEventRecorder(MachineEventRecorder):
    def __init__(self,journal,*,endpoint,lease,clock,retention=10000,capacity=262144):
        if type(endpoint) is not AssignedSerialEndpoint:raise TypeError('assigned endpoint required')
        if type(lease) is not ControllerLease:raise TypeError('controller lease required')
        lease.check()
        if getattr(endpoint.backend,'controller_lease',None) is not lease:
            raise ValueError('capture must use the connection owner lease')
        if Path(lease.path).absolute()!=Path(str(Path(journal.path).resolve())+'.owner'):
            raise ValueError('capture must use the owned controller journal')
        if endpoint._claimed:raise ValueError('attach capture before sender reader starts')
        endpoint._check()
        with journal._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='camera_binding'").fetchone():
                raise ValueError('camera journal cannot receive printer observations')
        self.endpoint,self.lease=endpoint,lease
        self._pid=os.getpid()
        self._printer_id=endpoint.backend.printer_id
        self._connection_generation=endpoint.backend.channel.generation
        self._source_sequence=endpoint.read_sequence
        self._source_lock=threading.Lock()
        self._reports={};self._resets=0
        super().__init__(journal,generation=self._connection_generation,clock=clock,retention=retention,capacity=capacity)

    def _persist(self,events):
        self.lease.check()  # Capture may not outlive journal ownership.
        super()._persist(events)
        with self._condition:
            for event in events:
                if event['kind']=='connection_invalidated':self._reports.clear();self._resets+=1
                if event['kind'] in ('temperature','reported_position','firmware_identity','fault','sensor_suspect'):
                    self._reports[event['kind']]=copy.deepcopy(event)

    def _check_process(self):
        if os.getpid()!=self._pid:raise OSError('assigned capture belongs to another process')

    def received_hook(self,sender,line,*args,**kwargs):
        self._check_process()
        # This hook never calls a backend or takes its control lock. Keep sender
        # replies byte-for-byte equivalent; loss retires capture, not machine facts.
        with self._source_lock:
            with self._condition:
                if self._closing or self._fault is not None:return line
            if line=='':return line
            try:
                self.lease.check()
                packet=self.endpoint.last_observation
                now=self.clock()
                if (getattr(sender,'_serial',None) is not self.endpoint
                        or self.endpoint._reader_thread is not threading.current_thread()
                        or type(packet) is not AssignedSerialObservation
                        or packet.sequence!=self._source_sequence+1
                        or packet.printer_id!=self._printer_id
                        or packet.connection_generation!=self._connection_generation
                        or type(line) is not str or line.encode('ascii')!=packet.received.data
                        or type(now) not in (int,float) or not math.isfinite(now)
                        or packet.received.observed_at>now):
                    raise ValueError('source line mismatch or invalid clock')
                provenance=dict(capture_id=self.capture_id,printer_id=self._printer_id,
                    connection_generation=self._connection_generation,source_sequence=packet.sequence,
                    bytes=len(packet.received.data),sha256=hashlib.sha256(packet.received.data).hexdigest(),
                    timestamp_kind='oldest_byte_host_read_return')
                if self._enqueue(packet.received.data,packet.received.observed_at,provenance):
                    self._source_sequence=packet.sequence
            except Exception:
                with self._condition:self._invalidate('assigned_receive_source_invalid')
        return line

    def _source_usable(self):
        try:
            self.lease.check();self.endpoint._check()
        except Exception:return False
        return self.endpoint.is_open and not self.endpoint.backend.receive_hub.fault and not self.endpoint.backend.frame_writer.fault

    def snapshot(self,*,max_age=1.):
        self._check_process()
        if type(max_age) not in (int,float) or not math.isfinite(max_age) or max_age<=0:
            raise ValueError('positive finite report age required')
        now=self.clock();usable=self._source_usable()
        clock_valid=type(now) in (int,float) and math.isfinite(now)
        with self._condition:
            active=usable and not self._closing and not self._closed and self._fault is None
            reports=copy.deepcopy(self._reports)
            for report in reports.values():
                age=now-report['observed_at'] if clock_valid else None
                report['arrival_age_seconds']=age
                report['arrival_fresh']=bool(active and clock_valid and 0<=age<=max_age)
            return dict(scope='source_bound_serial_reports',physical_readiness_qualified=False,
                capture_id=self.capture_id,printer_id=self._printer_id,source_connection_generation=self._connection_generation,
                source_usable=bool(usable),capturing=bool(active),fault=self._fault,controller_resets_observed=self._resets,
                sensor_validity=None,physical_position_verified=False,reports=reports)

    def read(self,after=0,limit=100):
        self._check_process()
        page=super().read(after,limit)
        page.update(coverage='assigned_endpoint_line_delivery_since_capture_start',
                    source_connection_generation=self._connection_generation,physical_readiness_qualified=False)
        page['health']['source_usable']=bool(self._source_usable())
        page['health']['capturing']=page['health']['capturing'] and page['health']['source_usable']
        return page

    def close(self,timeout=2):
        self._check_process()
        reason=self.endpoint.backend.receive_hub.fault or self.endpoint.backend.frame_writer.fault
        if reason and reason!='receive_connection_closed':
            with self._condition:self._invalidate('assigned_connection_lost')
        super().close(timeout)
