"""Initial operation backend for OctoPrint's software printer only.

No physical device can be attached through this adapter. Resume is qualified
only for the explicitly constrained cold, in-place virtual profile.
"""
import hashlib
import math
from pathlib import Path
import threading
import time
import uuid
import tempfile
import queue
from contextlib import contextmanager

from .journal import RequestRejected
from .service import Context, Outcome
from .checkpoint import PauseCheckpoint, InvalidCheckpoint
from .preflight import cold_virtual_job
from .virtual_setup import VirtualSetup


class _CallbackTap:
    """Callbacks never take backend locks or wait on service operations."""
    EVENTS = {'on_comm_print_job_done': 'completed', 'on_comm_print_job_cancelled': 'cancelled',
              'on_comm_print_job_failed': 'failed', 'on_comm_state_change': 'state',
              'on_comm_error': 'fault'}

    def __init__(self, original, events):
        self.original, self.events = original, events

    def __getattr__(self, name):
        callback = getattr(self.original, name)
        if name not in self.EVENTS:
            return callback
        def forward(*args, **kwargs):
            value = args[0] if name == 'on_comm_state_change' else None
            if name == 'on_comm_error':
                value = {'message': str(args[0] if args else kwargs.get('error', ''))[:2048],
                         'reason': str(args[1] if len(args) > 1 else kwargs.get('reason', ''))[:256]}
            self.events.put((self.EVENTS[name], value))
            return callback(*args, **kwargs)
        return forward


class OctoPrintVirtualBackend:
    supported_operations = ('job.stage', 'job.start', 'job.pause', 'job.resume', 'job.cancel') + ('printer.home','printer.jog')

    def __init__(self, sender, *, job_path, job_id, clock=time.time, witness=None):
        from octoprint.plugins.virtual_printer.virtual import VirtualPrinter
        if sender._port != 'VIRTUAL' or not isinstance(sender._serial, VirtualPrinter):
            raise RequestRejected('virtual_printer_required')
        self.sender = sender
        self.clock = clock
        self.path = Path(job_path).resolve(strict=True)
        self.source_path = self.path
        if not 0 < self.path.stat().st_size <= 16 * 1024 * 1024:
            raise RequestRejected('job_size_out_of_range')
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.job_id = job_id
        self.generation = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._revision = 0
        self._signature = None
        self._pending = {}
        self._closed = False
        self.witness = witness
        self.checkpoint = None
        self._pause_epoch = None
        self._pause_interval_invalid = False
        self.host_session = uuid.uuid4().hex
        self._staging = tempfile.TemporaryDirectory(prefix='llm-virtual-staged-')
        self._stage_version = 0
        self._staged = False
        self._started = False
        self._events = queue.SimpleQueue()
        self._callback = _CallbackTap(sender._callback, self._events)
        sender._callback = self._callback
        self._job_phase = 'available'
        self._fault = None
        self._transport_reset_detected = False
        self._printing_transitions = 0
        self._dispatch_transition = {}
        self.setup = VirtualSetup(self)

    def observe_transport_line(self, line):
        """Receive-hook entry point after bootstrap; never lock the backend here."""
        self.setup.observe(line)
        thermal = getattr(self, "thermal_transport", None)
        if thermal is not None:
            thermal.observe(line)
        if line.strip() == 'start':
            self._events.put(('controller_reset', self.clock()))

    def _drain_events(self):
        while True:
            try:
                event, value = self._events.get_nowait()
            except queue.Empty:
                break
            if event == 'controller_reset':
                self._transport_reset_detected = True
                self.generation = uuid.uuid4().hex
                self.checkpoint = None
                self._fault = self._fault or {'message': 'Controller reset observed; physical state unknown',
                    'reason': 'controller_reset', 'observed_at': value, 'source': 'serial_start_banner'}
            elif event == 'fault':
                self._fault = self._fault or {**value, 'observed_at': self.clock(),
                                             'source': 'octoprint_error_callback'}
                self.checkpoint = None
            elif event == 'state':
                if value == self.sender.STATE_PRINTING:
                    self._printing_transitions += 1
                    self._job_phase = 'running'
                elif value == self.sender.STATE_PAUSED:
                    self._job_phase = 'paused'
            else:
                # OctoPrint's done callback fires when EOF queues M400, before
                # that movement barrier finishes. Its later operational state
                # and the virtual movement queue must both confirm completion.
                self._job_phase = 'completing' if event == 'completed' else event
        if self._fault:
            self._job_phase = 'faulted'
        if self._job_phase == 'completing' and self.sender.getState() == self.sender.STATE_OPERATIONAL:
            device = self.sender._serial
            buffered = getattr(device, 'buffered', None)
            if buffered is not None:
                with buffered.all_tasks_done:
                    if buffered.unfinished_tasks == 0:
                        self._job_phase = 'completed'

    @contextmanager
    def guard(self):
        # Serialize this service's writers without taking sender-private locks:
        # its callbacks/worker threads have their own lock order. External raw
        # command writers must be excluded by the hosting integration.
        with self._lock:
            yield

    def context(self):
        self._drain_events()
        state = self.sender.getState()
        disconnected = state in (self.sender.STATE_CLOSED, self.sender.STATE_CLOSED_WITH_ERROR)
        epoch = self.witness.snapshot()[2] if self.witness and state == self.sender.STATE_PAUSED else None
        if state == self.sender.STATE_PAUSED:
            self._check_pause_interval()
        signature = (state, disconnected, epoch, self._stage_version, self._job_phase,
                     self._pause_interval_invalid, self.setup.revision)
        if signature != self._signature:
            self._revision += 1
            self._signature = signature
        # A lost connection invalidates every receipt from this connection.
        generation = self.generation + (':disconnected' if disconnected else '')
        return Context('virtual-development', generation, self._revision, self.clock())

    def validate(self, operation, arguments):
        if self._closed:
            raise RequestRejected('backend_closed')
        self._drain_events()
        if self._fault and operation != 'job.cancel':
            raise RequestRejected('observed_sender_fault_requires_recovery')
        if operation in VirtualSetup.OPERATIONS:
            if operation not in self.supported_operations:raise RequestRejected('unsupported_operation')
            self.setup.validate(operation, arguments)
            return
        if self.setup.uncertain:
            raise RequestRejected('setup_outcome_requires_recovery')
        expected = {'job_id': self.job_id}
        if operation == 'job.stage':
            expected['file_sha256'] = self.sha256
        if arguments != expected:
            raise RequestRejected('wrong_job_or_unknown_arguments')
        if operation not in self.supported_operations:
            raise RequestRejected('unsupported_operation')
        state = self.sender.getState()
        if operation == 'job.stage':
            if state != self.sender.STATE_OPERATIONAL:
                raise RequestRejected('printer_not_idle')
            data = self.source_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != self.sha256:
                raise RequestRejected('source_file_changed')
            cold_virtual_job(data)
            return
        if operation == 'job.start':
            if state != self.sender.STATE_OPERATIONAL or not self._staged or self._started:
                raise RequestRejected('job_not_staged')
            if not self.witness:
                raise RequestRejected('stream_witness_required')
        elif state not in (self.sender.STATE_PRINTING, self.sender.STATE_PAUSED):
            raise RequestRejected('job_not_running_or_paused')
        cursor = self.sender.getFilePosition()
        if not cursor or cursor.get('origin') != 'local' or Path(cursor.get('filename', '')).resolve() != self.path:
            raise RequestRejected('sender_job_changed')
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.sha256:
            raise RequestRejected('job_file_changed')
        if operation == 'job.pause' and state == self.sender.STATE_PAUSED:
            raise RequestRejected('job_already_paused_checkpoint_cannot_be_renewed')
        if operation == 'job.resume':
            if state != self.sender.STATE_PAUSED:
                raise RequestRejected('job_not_paused')
            blockers = self.resume_blockers()
            if blockers:
                raise RequestRejected(','.join(blockers))

    def dispatch(self, operation_id, operation, arguments, *, deadline):
        self._drain_events()
        self._pending[operation_id] = operation
        self._dispatch_transition[operation_id] = self._printing_transitions
        if operation in VirtualSetup.OPERATIONS:
            self.setup.dispatch(operation_id, operation, arguments, deadline)
            return
        if operation == 'job.stage':
            data = self.source_path.read_bytes()
            if len(data) > 16 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != self.sha256:
                raise RequestRejected('source_file_changed_before_stage')
            destination = (Path(self._staging.name) / (uuid.uuid4().hex + '.gcode')).resolve()
            with destination.open('xb') as output:
                output.write(data)
            destination.chmod(0o400)
            self.sender.selectFile(str(destination), False)
            self.path = destination
            self._staged, self._started = True, False
            self._stage_version += 1
            self.checkpoint = None
            self._job_phase = 'staged'
        elif operation == 'job.start':
            self.witness.start_job()
            self._started = True
            self.sender.startPrint()
        elif operation == 'job.pause':
            self.sender.setPause(True)
        elif operation == 'job.cancel':
            self.checkpoint = None
            self.sender.cancelPrint()
        elif operation == 'job.resume':
            if not self.witness.arm_resume(self._pause_epoch):
                raise RequestRejected('commands_queued_before_resume_dispatch')
            self.sender.setPause(False)
            self.checkpoint = None  # checkpoint cannot authorize another resume
        else:
            raise RequestRejected('unsupported_operation')

    def poll(self, operation_id):
        self._drain_events()
        operation = self._pending[operation_id]
        state = self.sender.getState()
        if self._fault and operation != 'job.cancel':
            return Outcome('outcome_unknown', {'reason': 'observed_sender_fault', 'fault': dict(self._fault)})
        if operation in VirtualSetup.OPERATIONS:
            if state != self.sender.STATE_OPERATIONAL:
                self.setup.uncertain = True
                return Outcome('outcome_unknown', {'reason': 'setup_sender_state_changed'})
            return self.setup.poll(operation_id)
        if operation == 'job.stage':
            cursor = self.sender.getFilePosition()
            if cursor and Path(cursor.get('filename', '')).resolve() == self.path:
                return Outcome('completed', {'evidence_type': 'virtual_file_selected', 'job_id': self.job_id,
                                             'file_sha256': self.sha256, 'bytes': self.path.stat().st_size})
        if operation == 'job.start' and self._printing_transitions > self._dispatch_transition[operation_id]:
            return Outcome('completed', {'evidence_type': 'virtual_sender_started', 'job_id': self.job_id})
        if operation == 'job.pause' and state == self.sender.STATE_PAUSED:
            position = self.sender.pause_position.as_dict()
            valid = all(type(position.get(k)) in (int, float) and math.isfinite(position[k]) for k in ('x', 'y', 'z', 'e'))
            if not valid:
                return Outcome('outcome_unknown', {'reason': 'pause_position_unconfirmed', 'sender_state': 'paused', 'resumable': False})
            cursor = self.sender.getFilePosition()
            result = {'evidence_type': 'virtual_sender_pause_barrier_and_position',
                                         'position': position, 'file_sha256': self.sha256,
                                         'cursor': cursor['pos'], 'job_id': self.job_id,
                                         'resumable': False, 'resume_blockers': ['complete_modal_checkpoint_unavailable']}
            if self.witness:
                try:
                    epoch = self.witness.snapshot()[2]
                    self.checkpoint = self._capture()
                    if not self.witness.freeze(epoch):
                        self.checkpoint = None
                        raise RequestRejected('commands_queued_during_checkpoint')
                    self._pause_epoch = epoch
                    self._pause_interval_invalid = False
                    result.update(self.checkpoint.envelope())
                    result.update(resumable=True, resume_blockers=[])
                except (RequestRejected, InvalidCheckpoint) as exc:
                    result['resume_blockers'] = [str(exc)]
            return Outcome('completed', result)
        if operation == 'job.resume' and self._printing_transitions > self._dispatch_transition[operation_id]:
            return Outcome('completed', {'evidence_type': 'virtual_in_place_resume', 'job_id': self.job_id})
        if operation == 'job.cancel' and state == self.sender.STATE_OPERATIONAL:
            # This initial cold virtual profile has no cancellation scripts or
            # heaters. Do not generalize this criterion to a physical printer.
            device = self.sender._serial
            with device.buffered.all_tasks_done:
                drained = device.buffered.unfinished_tasks == 0
            if not drained or any(device.targetTemp) or device.bedTargetTemp or device.chamberTargetTemp:
                return None
            return Outcome('completed', {'evidence_type': 'virtual_sender_cancelled_and_moves_drained', 'job_id': self.job_id, 'heater_targets_zero': True})
        if state in (self.sender.STATE_ERROR, self.sender.STATE_CLOSED, self.sender.STATE_CLOSED_WITH_ERROR):
            return Outcome('outcome_unknown', {'reason': 'sender_fault_or_disconnect'})
        if state == self.sender.STATE_OPERATIONAL and operation in ('job.start', 'job.pause', 'job.resume'):
            return Outcome('outcome_unknown', {'reason': 'job_ended_before_transition_observed'})
        return None

    def inspect(self):
        context = self.context()
        paused = self.sender.getState() == self.sender.STATE_PAUSED
        blockers = self.resume_blockers() if paused else ['job_not_paused']
        phase = self._job_phase
        if self._transport_reset_detected and self._started:
            phase = 'outcome_unknown'
        elif context.generation.endswith(':disconnected') and self._started and phase not in ('completed', 'cancelled'):
            phase = 'outcome_unknown'
        return dict(printer_id=context.printer_id, connection_generation=context.generation,
                    state_version=context.state_version, observed_at=context.observed_at,
                    evidence_type='virtual_sender_state', sender_state=self.sender.getStateString(),
                    job_state=phase,
                    blocking_fault=None if self._fault is None else dict(self._fault),
                    automatic_fault_recovery_supported=False,
                    job_id=self.job_id, file_sha256=self.sha256, staged=self._staged,
                    resumable=paused and not blockers, resume_blockers=blockers,
                    pause_policy=self.pause_policy_status(), setup=self.setup.inspect(),
                    profile='cold-in-place-virtual', physical_printer=False)

    def capabilities(self):
        return {'profile': 'cold-in-place-virtual', 'physical_printer': False, 'setup': self.setup.inspect(),
                'unsupported': ['physical_control', 'prompt.respond', 'parking', 'thermal_standby', 'power_loss_recovery']}

    def _capture(self):
        if not self.witness:
            raise RequestRejected('stream_witness_unavailable')
        modes, blockers, epoch = self.witness.snapshot()
        if blockers:
            raise RequestRejected('modal_state_unqualified:' + ','.join(blockers))
        device = self.sender._serial
        with device.buffered.all_tasks_done:
            if device.buffered.unfinished_tasks:
                raise RequestRejected('virtual_moves_not_drained')
        # Simulator restrictions are explicit; unsupported effects must not be
        # silently accepted merely because the virtual firmware acknowledges them.
        if modes['units'] != 'mm' or modes['xyz_relative'] != modes['e_relative']:
            raise RequestRejected('virtual_model_mode_limit')
        if modes['fan_pwm'] != 0 or modes['nozzle_target'] != 0 or modes['bed_target'] != 0:
            raise RequestRejected('virtual_cold_in_place_profile_required')
        if device._relative != modes['xyz_relative'] or device._lastF != modes['feedrate']:
            raise RequestRejected('virtual_modal_observation_mismatch')
        if any(device.targetTemp) or device.bedTargetTemp or device.chamberTargetTemp:
            raise RequestRejected('virtual_targets_changed')
        position = dict(x=device._lastX, y=device._lastY, z=device._lastZ, e=device._lastE[0])
        reported = self.sender.pause_position.as_dict()
        if any(reported.get(k) != v for k, v in position.items()):
            raise RequestRejected('position_changed_since_pause')
        cursor = self.sender.getFilePosition()
        now = self.clock()
        modal = {k: modes[k] for k in ('units', 'xyz_relative', 'e_relative', 'feedrate', 'tool', 'fan_pwm', 'nozzle_target', 'bed_target')}
        modal.update(feedrate_percent=device._feedrate_multiplier, flow_percent=device._flowrate_multiplier)
        return PauseCheckpoint(dict(schema_version=1, printer_id='virtual-development', generation=self.generation,
            host_session=self.host_session, job_id=self.job_id, file_sha256=self.sha256,
            firmware_id='OctoPrint-1.11.8-virtual', config_sha256=hashlib.sha256(b'cold-in-place-virtual-v1').hexdigest(),
            cursor=cursor['pos'], next_line_number=self.sender._current_line,
            created_at=now, expires_at=now + 60, position=position, modal=modal,
            offsets=dict(x=0, y=0, z=0, e=0), leveling_active=False,
            evidence={k: True for k in PauseCheckpoint.PROOFS}))

    def _check_pause_interval(self):
        if self.checkpoint is not None:
            saved = self.checkpoint.to_dict()
            now = self.clock()
            if now < saved['created_at'] or now >= saved['expires_at']:
                self._pause_interval_invalid = True

    def pause_policy_status(self):
        saved = self.checkpoint.to_dict() if self.checkpoint else None
        return dict(kind='cold_in_place', short_pause_limit_seconds=60,
                    extended_pause_supported=False, thermal_targets='off',
                    motor_hold_evidence='virtual_model_only',
                    expires_at=saved['expires_at'] if saved else None,
                    remaining_seconds=max(0, saved['expires_at'] - self.clock())
                        if saved and not self._pause_interval_invalid else 0,
                    interval_invalidated=self._pause_interval_invalid,
                    physical_pause_qualified=False)

    def resume_blockers(self):
        if self._fault:
            return ['observed_sender_fault_requires_recovery']
        if self.checkpoint is None:
            return ['complete_resume_checkpoint_unavailable']
        self._check_pause_interval()
        if self._pause_interval_invalid:
            return ['pause_interval_expired_or_clock_reversed']
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.sha256:
            return ['job_file_changed']
        if self.witness.snapshot()[2] != self._pause_epoch:
            return ['commands_queued_since_checkpoint']
        try:
            current = self._capture()
        except (RequestRejected, InvalidCheckpoint) as exc:
            return [str(exc)]
        return self.checkpoint.in_place_resume_blockers(current, now=self.clock(), max_observation_age=1)

    def close(self):
        self.sender.close()
        if self.sender._callback is self._callback:
            self.sender._callback = self._callback.original
        self._closed = True
        self._staging.cleanup()
