"""In-process capture-owner API core. Does not acquire cameras or printer USB.

The existing acquisition loop publishes atomically with its generation/context.
Profile, frame timestamps and physical assignment must be qualified separately.
"""
import threading
import os
import time
import uuid
from copy import deepcopy
from .camera import identifier, number
from .journal import Journal, RequestRejected


class CaptureOwner:
    def __init__(self, *, camera_id, printer_id, capture_owner, profile,
                 mount_revision, view_calibration_id=None, controls=None,
                 read_controls=None, write_controls=None, clock=time.time):
        if not all(identifier(v) for v in (camera_id, printer_id, capture_owner)):
            raise RequestRejected('invalid_owner_assignment')
        self.camera_id, self.printer_id, self.capture_owner = camera_id, printer_id, capture_owner
        self.clock, self.lock = clock, threading.RLock()
        self._pid=os.getpid()
        if controls is not None and type(controls) is not dict:
            raise RequestRejected('invalid_owner_controls')
        self.controls = deepcopy({} if controls is None else controls)
        self.reader, self.writer = read_controls, write_controls
        if self.controls and (not callable(self.reader) or not callable(self.writer)):
            raise RequestRejected('owner_control_callbacks_required')
        if len(self.controls)>32:
            raise RequestRejected('invalid_owner_controls')
        for name,bounds in self.controls.items():
            if (not identifier(name) or type(bounds) is not dict or set(bounds)!={'minimum','maximum'}
                    or not all(number(v) for v in bounds.values()) or bounds['minimum']>bounds['maximum']):
                raise RequestRejected('invalid_owner_controls')
        self.values, self.uncertain = None, False
        self.profile, self.job_id, self.mount, self.calibration = None, None, None, None
        self.generation, self.version, self.sequence, self.frame = uuid.uuid4().hex, 0, 0, None
        self.configure_view(profile=profile,job_id=None,mount_revision=mount_revision,
                            view_calibration_id=view_calibration_id)

    def _check_process(self):
        if self._pid!=os.getpid(): raise RequestRejected('capture_owner_process_changed')

    def _now(self):
        value=self.clock()
        if not number(value): raise RequestRejected('invalid_owner_clock')
        return value

    def _invalidate(self):
        self.version+=1
        self.frame=None

    def _values(self, values):
        if type(values) is not dict or set(values)!=set(self.controls):
            raise RequestRejected('owner_readback_incomplete')
        for name,value in values.items():
            bounds=self.controls[name]
            if not number(value) or not bounds['minimum']<=value<=bounds['maximum']:
                raise RequestRejected('owner_readback_invalid')

    def _refresh(self):
        if self.uncertain: raise RequestRejected('owner_controls_require_reconciliation')
        values=deepcopy(self.reader()) if self.controls else {}
        self._values(values)
        if values!=self.values:
            self.values=values
            self._invalidate()

    def inspect(self):
        self._check_process()
        with self.lock:
            self._refresh()
            return dict(camera_id=self.camera_id,assigned_printer_id=self.printer_id,
                capture_owner=self.capture_owner,connection_generation=self.generation,
                state_version=self.version,controls=deepcopy(self.controls),values=deepcopy(self.values))

    def configure_view(self, *, profile, job_id, mount_revision, view_calibration_id):
        """Trusted owner configuration, deliberately not an HTTP/LLM operation."""
        self._check_process()
        if (type(profile) is not dict or set(profile)!={'width','height','media_type','crop'}
                or any(type(profile[k]) is not int or not 1<=profile[k]<=4096 for k in ('width','height'))
                or profile['media_type'] not in ('image/png','image/jpeg')
                or profile['crop']!='full_encoded_frame'
                or job_id is not None and not identifier(job_id)
                or not identifier(mount_revision)
                or view_calibration_id is not None and not identifier(view_calibration_id)):
            raise RequestRejected('invalid_owner_view')
        with self.lock:
            view=(deepcopy(profile),job_id,mount_revision,view_calibration_id)
            if view!=(self.profile,self.job_id,self.mount,self.calibration):
                self.profile,self.job_id,self.mount,self.calibration=view
                self._invalidate()

    def begin_capture(self):
        """Call for each acquisition-process/session start or reconnect."""
        self._check_process()
        with self.lock:
            self.generation=uuid.uuid4().hex
            self.sequence=0
            self._invalidate()
            return self.inspect()

    def publish(self, *, connection_generation, expected_state_version, data,
                captured_at, received_at, width, height):
        """The acquisition owner supplies qualified capture/profile evidence."""
        self._check_process()
        with self.lock:
            self._refresh()
            if connection_generation!=self.generation or type(expected_state_version) is not int or expected_state_version!=self.version:
                raise RequestRejected('stale_owner_capture_context')
            now=self._now()
            if (not number(captured_at) or not number(received_at)
                    or not now-30<=captured_at<=received_at<=now):
                raise RequestRejected('invalid_owner_frame_time')
            if (type(width) is not int or type(height) is not int
                    or (width,height)!=(self.profile['width'],self.profile['height'])
                    or type(data) is not bytes or not 1<=len(data)<=65536):
                raise RequestRejected('invalid_owner_frame')
            signature=b'\x89PNG\r\n\x1a\n' if self.profile['media_type']=='image/png' else b'\xff\xd8\xff'
            if not data.startswith(signature): raise RequestRejected('invalid_owner_frame_signature')
            if self.frame and captured_at<=self.frame['captured_at']:
                raise RequestRejected('out_of_order_owner_frame')
            self.sequence+=1
            self.frame=dict(camera_id=self.camera_id,assigned_printer_id=self.printer_id,
                connection_generation=self.generation,job_id=self.job_id,
                frame_id=self.generation+':'+str(self.sequence),captured_at=captured_at,received_at=received_at,
                **deepcopy(self.profile),mount_revision=self.mount,view_calibration_id=self.calibration,data=data)

    def snapshot(self, *, deadline):
        self._check_process()
        with self.lock:
            self._refresh()
            now=self._now()
            if not number(deadline) or deadline<=now: raise RequestRejected('owner_deadline_expired')
            if not self.frame or not 0<=now-self.frame['captured_at']<=5:
                raise RequestRejected('owner_frame_unavailable')
            return deepcopy(self.frame)

    def apply_controls(self, request):
        """Atomic context fence. Advance version before the sole write attempt."""
        self._check_process()
        required={'camera_id','connection_generation','expected_state_version','deadline','controls'}
        if type(request) is not dict or set(request)!=required:
            raise RequestRejected('invalid_owner_control_request')
        import json
        request=json.loads(Journal._encode(request))
        with self.lock:
            self._refresh()
            if (request['camera_id']!=self.camera_id or request['connection_generation']!=self.generation
                    or type(request['expected_state_version']) is not int or request['expected_state_version']!=self.version):
                raise RequestRejected('stale_owner_control_context')
            if not number(request['deadline']) or request['deadline']<=self._now():
                raise RequestRejected('owner_deadline_expired')
            values=request['controls']
            if type(values) is not dict or not values or len(values)>32:
                raise RequestRejected('invalid_owner_control_values')
            for name,value in values.items():
                if name not in self.controls or not number(value) or not self.controls[name]['minimum']<=value<=self.controls[name]['maximum']:
                    raise RequestRejected('unsupported_owner_control_value')
            self._invalidate()
            try:
                self.writer(deepcopy(values),deadline=request['deadline'])
                self._refresh()
                if self._now()>=request['deadline'] or any(self.values[k]!=v for k,v in values.items()):
                    raise RequestRejected('owner_control_readback_unconfirmed')
            except BaseException:
                self.uncertain=True
                self.frame=None
                raise
            return {'protocol_version':1,'accepted':True}
