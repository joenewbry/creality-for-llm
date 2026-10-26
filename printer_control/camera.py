"""Assigned camera broker. Backends are injected capture owners, never device paths.

This module does not open USB, fetch arbitrary URLs, infer print health or command
printer motion. Wall timestamps need a qualified shared clock from the backend.
"""
import base64
import hashlib
import math
import threading
import time
from copy import deepcopy
from pathlib import Path
from .journal import Journal, RequestRejected
from .ownership import ControllerLease


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def identifier(value):
    return type(value) is str and 1 <= len(value) <= 128 and bool(value.strip())


class CameraBroker:
    """Separate journal/lease for one assigned camera and capture-owner adapter.

    Backend inspect() returns identity, generation, state_version, control ranges
    and current values. snapshot(deadline=...) returns an encoded image frame;
    set_controls(values, deadline=...) performs one attempt, without retries.
    Backends must bound their own calls: Python cannot cancel blocking native I/O.
    """
    CONTEXT = {'camera_id', 'connection_generation', 'expected_state_version', 'deadline'}
    FRAME_FIELDS = {'camera_id', 'connection_generation', 'assigned_printer_id', 'job_id',
                    'frame_id', 'captured_at', 'received_at', 'width', 'height', 'crop',
                    'view_calibration_id', 'mount_revision', 'media_type', 'data'}

    def __init__(self, journal_path, backend, *, camera_id, printer_id, capture_owner,
                 clock=time.time):
        if not all(identifier(v) for v in (camera_id, printer_id, capture_owner)):
            raise RequestRejected('invalid_camera_assignment')
        self.backend, self.camera_id, self.printer_id = backend, camera_id, printer_id
        self.capture_owner, self.clock = capture_owner, clock
        self.lock = threading.RLock()
        self.latest_frame = None
        self.closed = False
        journal_path = Path(journal_path).resolve()
        self.lease = ControllerLease(str(journal_path) + '.owner').acquire()
        try:
            self.journal = Journal(journal_path)
            with self.journal._db() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backend_startups'").fetchone():
                    raise RequestRejected('printer_startup_journal_cannot_control_camera')
                binding = {'camera_id': camera_id, 'printer_id': printer_id, 'capture_owner': capture_owner}
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='camera_binding'").fetchone()
                if not exists:
                    if db.execute('SELECT 1 FROM operations LIMIT 1').fetchone():
                        raise RequestRejected('camera_journal_not_empty_or_bound')
                    db.execute('CREATE TABLE camera_binding (binding TEXT NOT NULL)')
                    db.execute('INSERT INTO camera_binding VALUES (?)', (Journal._encode(binding),))
                elif db.execute('SELECT binding FROM camera_binding').fetchone()[0] != Journal._encode(binding):
                    raise RequestRejected('camera_journal_assignment_mismatch')
            self.journal.recover_after_restart(now=self._now())
        except BaseException:
            self.lease.close()
            raise

    def _now(self):
        now = self.clock()
        if not number(now):
            raise RequestRejected('invalid_camera_clock')
        return now

    def _state(self):
        if self.closed:
            raise RequestRejected('camera_broker_closed')
        self.lease.check()
        state = deepcopy(self.backend.inspect())
        required = {'camera_id', 'assigned_printer_id', 'capture_owner',
                    'connection_generation', 'state_version', 'controls', 'values'}
        if type(state) is not dict or set(state) != required:
            raise RequestRejected('invalid_camera_state')
        if (state['camera_id'] != self.camera_id or state['assigned_printer_id'] != self.printer_id
                or state['capture_owner'] != self.capture_owner):
            raise RequestRejected('camera_assignment_changed')
        if (not identifier(state['connection_generation']) or type(state['state_version']) is not int
                or state['state_version'] < 0 or type(state['controls']) is not dict
                or type(state['values']) is not dict or set(state['controls']) != set(state['values'])):
            raise RequestRejected('invalid_camera_state')
        for name, bounds in state['controls'].items():
            if (not identifier(name) or type(bounds) is not dict or set(bounds) != {'minimum', 'maximum'}
                    or not all(number(bounds[k]) for k in bounds)
                    or bounds['minimum'] > bounds['maximum']):
                raise RequestRejected('invalid_camera_capabilities')
        self._controls(state['values'], state)
        Journal._encode(state)
        return state

    @staticmethod
    def _controls(values, state):
        if type(values) is not dict or not values or len(values) > 32:
            if values == {} and state['controls'] == {}:
                return
            raise RequestRejected('invalid_camera_controls')
        for name, value in values.items():
            if name not in state['controls']:
                raise RequestRejected('unsupported_camera_control')
            bounds = state['controls'][name]
            if not number(value) or not bounds['minimum'] <= value <= bounds['maximum']:
                raise RequestRejected('camera_control_out_of_range')

    def _context(self, request, state):
        if (request['camera_id'] != self.camera_id
                or request['connection_generation'] != state['connection_generation']):
            raise RequestRejected('stale_camera_identity')
        if type(request['expected_state_version']) is not int or request['expected_state_version'] != state['state_version']:
            raise RequestRejected('stale_camera_state')
        if not number(request['deadline']) or request['deadline'] <= self._now():
            raise RequestRejected('camera_deadline_expired')

    def inspect(self):
        with self.lock:
            state = self._state()
            frame = self.latest_frame
            if frame and (frame['connection_generation'] != state['connection_generation']
                          or frame['state_version'] != state['state_version']):
                self.latest_frame = frame = None
            return {**state, 'latest_frame': deepcopy(frame), 'physical_qualification': False,
                    'scope': 'injected_capture_owner_adapter', 'automatic_printer_actions': False}

    def snapshot(self, request):
        if type(request) is not dict or set(request) != self.CONTEXT | {'max_age_seconds', 'job_id'}:
            raise RequestRejected('invalid_camera_snapshot_request')
        request = deepcopy(request)
        age = request['max_age_seconds']
        if (not number(age) or not 0 < age <= 30 or
                request['job_id'] is not None and not identifier(request['job_id'])):
            raise RequestRejected('invalid_frame_constraints')
        with self.lock:
            state = self._state()
            self._context(request, state)
            self.latest_frame = None  # failed acquisition cannot leave fresh-looking evidence
            frame = self.backend.snapshot(deadline=request['deadline'])
            after = self._state()
            self._context(request, after)
            if after != state:
                raise RequestRejected('camera_changed_during_capture')
            if type(frame) is not dict or set(frame) != self.FRAME_FIELDS:
                raise RequestRejected('invalid_frame')
            frame = deepcopy(frame)
            now = self._now()
            if (frame['camera_id'] != self.camera_id or frame['assigned_printer_id'] != self.printer_id
                    or frame['connection_generation'] != state['connection_generation']
                    or frame['job_id'] != request['job_id']):
                raise RequestRejected('frame_association_mismatch')
            if (not all(number(frame[k]) for k in ('captured_at', 'received_at'))
                    or not now - age <= frame['captured_at'] <= frame['received_at'] <= now):
                raise RequestRejected('stale_or_invalid_frame_time')
            if (not identifier(frame['frame_id']) or not identifier(frame['mount_revision'])
                    or frame['view_calibration_id'] is not None and not identifier(frame['view_calibration_id'])
                    or any(type(frame[k]) is not int or not 1 <= frame[k] <= 4096 for k in ('width', 'height'))
                    or frame['crop'] != 'full_encoded_frame'):
                raise RequestRejected('invalid_frame_geometry')
            data = frame.pop('data')
            if (type(data) is not bytes or not 1 <= len(data) <= 65536
                    or frame['media_type'] not in ('image/jpeg', 'image/png')):
                raise RequestRejected('invalid_frame_encoding')
            # This is an encoded-byte envelope, not an image decoder or optical validator.
            if not data.startswith(b'\x89PNG\r\n\x1a\n' if frame['media_type'] == 'image/png' else b'\xff\xd8\xff'):
                raise RequestRejected('invalid_frame_signature')
            frame.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                         state_version=state['state_version'])
            self.latest_frame = frame
            return {**deepcopy(frame), 'data_base64': base64.b64encode(data).decode('ascii'),
                    'optical_quality_verified': False}

    def set_controls(self, request):
        required = self.CONTEXT | {'request_id', 'controls'}
        if type(request) is not dict or set(request) != required:
            raise RequestRejected('invalid_camera_control_request')
        request = deepcopy(request)
        internal = {'request_id': request['request_id'], 'printer_id': request['camera_id'],
                    'connection_generation': request['connection_generation'],
                    'expected_state_version': request['expected_state_version'],
                    'deadline': request['deadline'], 'operation': 'camera.set_controls',
                    'arguments': {'controls': request['controls']}}
        Journal.validate(internal)
        with self.lock:
            state = self._state()
            # Journal deduplication precedes live-state checks, preserving historical receipts.
            receipt = self.journal.accept(internal, now=self._now(), printer_id=self.camera_id,
                generation=state['connection_generation'], state_version=state['state_version'])
            if receipt['status'] != 'accepted':
                return receipt
            op = receipt['operation_id']
            try:
                with self.journal._db() as db:
                    if db.execute("SELECT 1 FROM operations WHERE status='outcome_unknown' LIMIT 1").fetchone():
                        raise RequestRejected('camera_reconciliation_required')
                self._context(request, state)
                if not request['controls']:
                    raise RequestRejected('empty_camera_controls')
                self._controls(request['controls'], state)
            except RequestRejected as exc:
                return self.journal.finish(op, 'rejected', {'reason': str(exc)}, now=self._now())
            if not self.journal.claim(op, now=self._now()):
                return self.journal.get(op)
            self.latest_frame = None
            try:
                self.lease.check()
                self._context(request, self._state())
                self.backend.set_controls(deepcopy(request['controls']), deadline=request['deadline'])
                after = self._state()
                if (after['connection_generation'] != state['connection_generation']
                        or after['state_version'] <= state['state_version']
                        or self._now() >= request['deadline']
                        or any(after['values'].get(k) != v for k, v in request['controls'].items())):
                    raise RequestRejected('camera_control_readback_unconfirmed')
                status, result = 'completed', {'readback': after, 'optical_quality_verified': False}
            except Exception:
                status, result = 'outcome_unknown', {'reason': 'camera_control_outcome_unconfirmed',
                                                    'requires_reconciliation': True}
            return self.journal.finish(op, status, result, now=self._now())

    def monitor(self):
        """Frame provenance only; deliberately no fabricated visual classification."""
        state = self.inspect()
        frame = state['latest_frame']
        fresh = frame is not None and 0 <= self._now() - frame['captured_at'] <= 5
        return {'available': fresh, 'frame': frame if fresh else None,
                'observations': [], 'visual_detector_implemented': False,
                'unknown': ['print_health', 'occlusions', 'heater_state', 'motion_state'],
                'automatic_printer_actions': False}

    def close(self):
        with self.lock:
            self.closed = True
            self.latest_frame = None
            self.lease.close()


class CameraTools:
    """Compose with the normal printer router for the same JSON/MCP transport."""
    def __init__(self, broker, printer_router=None):
        self.broker, self.printer_router = broker, printer_router

    def definitions(self):
        context = {'camera_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
                   'connection_generation': {'type': 'string', 'minLength': 1, 'maxLength': 128},
                   'expected_state_version': {'type': 'integer', 'minimum': 0},
                   'deadline': {'type': 'number'}}
        schemas = {'camera.inspect': {}, 'monitor.inspect': {},
                   'camera.operation_get': {'operation_id': {'type': 'string', 'minLength': 1, 'maxLength': 128}},
                   'camera.snapshot': {**context, 'max_age_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 30},
                                       'job_id': {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 128}},
                   'camera.set_controls': {**context, 'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
                       'controls': {'type': 'object', 'minProperties': 1, 'maxProperties': 32,
                                    'additionalProperties': {'type': 'number'}}}}
        own = [{'name': name, 'description': 'Assigned capture-owner adapter; no optical or physical qualification. ' + name,
                'inputSchema': {'type': 'object', 'properties': fields, 'required': list(fields),
                                'additionalProperties': False}} for name, fields in schemas.items()]
        other = self.printer_router.definitions() if self.printer_router else []
        if {x['name'] for x in own} & {x['name'] for x in other}:
            raise RequestRejected('duplicate_camera_tool')
        return other + own

    def call(self, name, arguments):
        import json
        if type(name) is not str or type(arguments) is not dict:
            raise RequestRejected('invalid_camera_tool_call')
        arguments = json.loads(Journal._encode(arguments))
        if name in ('camera.inspect', 'monitor.inspect'):
            if arguments:
                raise RequestRejected('unexpected_camera_read_arguments')
            return self.broker.inspect() if name == 'camera.inspect' else self.broker.monitor()
        if name == 'camera.snapshot':
            return self.broker.snapshot(arguments)
        if name == 'camera.set_controls':
            return self.broker.set_controls(arguments)
        if name == 'camera.operation_get':
            if set(arguments) != {'operation_id'} or not identifier(arguments['operation_id']):
                raise RequestRejected('invalid_camera_operation_id')
            with self.broker.lock:
                self.broker._state()
                return self.broker.journal.get(arguments['operation_id'])
        if self.printer_router and not name.startswith(('camera.', 'monitor.')):
            return self.printer_router.call(name, arguments)
        raise RequestRejected('unknown_camera_tool')
