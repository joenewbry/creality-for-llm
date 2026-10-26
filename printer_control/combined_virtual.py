"""Experimental one-journal job/prompt backend, restricted to the virtual sender."""
from .octoprint_virtual import OctoPrintVirtualBackend
from .prompt_backend import PromptBackend
from .prompt_channel import PromptChannel
from .journal import RequestRejected
from .service import Context, Outcome


class CombinedVirtualBackend:
    supported_operations = OctoPrintVirtualBackend.supported_operations + PromptBackend.supported_operations + ('printer.stop',)

    def __init__(self, job, receiver):
        if not isinstance(job, OctoPrintVirtualBackend):
            raise ValueError('virtual job backend required')
        self.job, self.receiver = job, receiver
        self.writer = job.sender._serial.shared_frame_writer
        self.generation = job.generation
        channel = PromptChannel(self.writer.write_prompt, self.generation)
        self.prompt = PromptBackend(channel, printer_id='virtual-development', clock=job.clock,
                                    pump=receiver.drain, close_transport=lambda: None)
        self._version = 0
        self._signature = None
        self._routes = {}
        self._closed = False
        self._stop_requested = False
        self._stop_deadlines = {}

    @property
    def witness(self):
        """Expose the sole sender witness to service-level interruption guards."""
        return self.job.witness

    @property
    def host_session(self):
        return self.job.host_session

    @property
    def checkpoint(self):
        return self.job.checkpoint

    def guard(self):
        return self.job.guard()

    def context(self):
        job = self.job.context()
        if self.job._fault or job.generation != self.generation or self.writer.fault:
            self.prompt.channel.invalidate('shared_connection_unqualified', now=self.job.clock())
        prompt = self.prompt.context()
        signature = (job.generation, job.state_version, prompt.state_version, self.writer.fault,
                     self._stop_requested, self._virtual_stop_confirmed())
        if signature != self._signature:
            self._version += 1
            self._signature = signature
        if self.prompt.channel.state in ('uncertain', 'disconnected'):
            self.job.checkpoint = None
        return Context(job.printer_id, job.generation, self._version, self.job.clock())

    def _target(self, operation):
        if operation in self.prompt.supported_operations:
            return self.prompt
        if operation in self.job.supported_operations:
            return self.job
        raise RequestRejected('unsupported_combined_operation')

    def can_overlap(self, operation, pending_ids):
        # A generic wait may block a start/pause/resume receipt. Only the
        # separate single-flight prompt lane may run alongside those operations.
        return (operation in self.prompt.supported_operations and bool(pending_ids) and
                all(self._routes.get(op) in ('job.start', 'job.pause', 'job.resume') for op in pending_ids))

    def validate(self, operation, arguments):
        if self._closed:
            raise RequestRejected('backend_closed')
        if operation == 'printer.stop':
            if arguments:
                raise RequestRejected('unexpected_stop_arguments')
            if self._stop_requested:
                raise RequestRejected('stop_already_requested')
            device = self.job.sender._serial
            if not getattr(device, '_supportM112', False) or self.writer.fault:
                raise RequestRejected('virtual_stop_transport_unavailable')
            return
        if self._stop_requested:
            raise RequestRejected('stop_requires_recovery')
        if self.prompt.channel.state in ('uncertain', 'disconnected') and operation != 'job.cancel':
            raise RequestRejected('shared_connection_unqualified')
        if operation in self.prompt.supported_operations:
            device = self.job.sender._serial
            active = getattr(device, 'wait_active', None)
            if active is None or not active.is_set():
                raise RequestRejected('no_observed_fixture_prompt')
        self._target(operation).validate(operation, arguments)

    def dispatch(self, operation_id, operation, arguments, *, deadline):
        self._routes[operation_id] = operation
        if operation == 'printer.stop':
            self._stop_requested = True
            self._stop_deadlines[operation_id] = deadline
            self.job.checkpoint = None
            self.prompt.channel.invalidate('stop_requested', now=self.job.clock())
            # Bypass the ordinary queue but retain the sole complete-frame writer.
            # This adapter only exists around the explicitly verified VIRTUAL device.
            self.writer.write_job(b'M112\n')
            return
        if operation == 'job.cancel':
            self.prompt.channel.invalidate('job_cancel_requested', now=self.job.clock())
        self._target(operation).dispatch(operation_id, operation, arguments, deadline=deadline)

    def poll(self, operation_id):
        operation = self._routes[operation_id]
        if operation == 'printer.stop':
            if self._virtual_stop_confirmed():
                return Outcome('completed', {'evidence_type': 'virtual_killed_flag_and_drained_moves',
                    'scope': 'software_virtual_device_only', 'physical_stop_qualified': False,
                    'resume_allowed': False, 'requires_recovery': True})
            if self.job.clock() >= self._stop_deadlines[operation_id]:
                return Outcome('outcome_unknown', {'reason': 'virtual_stop_unconfirmed',
                    'physical_stop_qualified': False, 'requires_recovery': True})
            return None
        if self._stop_requested:
            return Outcome('outcome_unknown', {'reason': 'interrupted_by_stop'})
        if operation == 'job.cancel' and self._fixture_waiting():
            return Outcome('outcome_unknown', {
                'reason': 'cancel_blocked_by_native_wait',
                'stop_confirmed': False,
                'requires_recovery': True,
                'automatic_resume_allowed': False,
            })
        if self.prompt.channel.state in ('uncertain', 'disconnected') and operation != 'job.cancel':
            return Outcome('outcome_unknown', {'reason': 'shared_connection_unqualified'})
        return self._target(operation).poll(operation_id)

    def _virtual_stop_confirmed(self):
        device = self.job.sender._serial
        buffered = getattr(device, 'buffered', None)
        if not self._stop_requested or not getattr(device, '_killed', False) or buffered is None:
            return False
        with buffered.all_tasks_done:
            return (buffered.unfinished_tasks == 0 and not any(device.targetTemp)
                    and not device.bedTargetTemp and not device.chamberTargetTemp)

    def _fixture_waiting(self):
        device = self.job.sender._serial
        active = getattr(device, 'wait_active', None)
        released = getattr(device, 'wait_released', None)
        return bool(active and active.is_set() and released and not released.is_set())

    def inspect(self):
        context = self.context()
        job = self.job.inspect()
        prompt = self.prompt.inspect()
        # Use the versions of these exact snapshots; do not pump again and
        # attach a newer context version to older prompt data.
        signature = (job['connection_generation'], job['state_version'], prompt['state_version'], self.writer.fault,
                     self._stop_requested, self._virtual_stop_confirmed())
        if signature != self._signature:
            self._version += 1
            self._signature = signature
        context = Context(job['printer_id'], job['connection_generation'], self._version, self.job.clock())
        if self.prompt.channel.state in ('uncertain', 'disconnected'):
            job.update(resumable=False, resume_blockers=['shared_connection_unqualified'])
        if self._stop_requested:
            job.update(job_state='stopped' if self._virtual_stop_confirmed() else 'stopping',
                       resumable=False, resume_blockers=['stop_requires_recovery'])
        return {**job, 'prompt': prompt, 'state_version': context.state_version,
                'fixture_prompt_waiting': self._fixture_waiting(),
                'connection_generation': context.generation, 'observed_at': context.observed_at,
                'profile': 'combined-cold-virtual-native-prompt'}

    def capabilities(self):
        return {'profile': 'combined-cold-virtual-native-prompt', 'physical_printer': False,
                'one_operation_journal': True, 'setup': self.job.setup.inspect(), 'stop_scope': 'software_virtual_device_only', 'prompt_overlap': 'job.start/job.pause/job.resume only',
                'unsupported': ['physical_control', 'reconnect', 'extended_hot_pause', 'raw_gcode',
                                'confirmed_cancel_during_native_wait']}

    def close(self):
        if not self._closed:
            self.job.close()  # Owns and stops the sole virtual transport first.
            self.prompt.close()
            self.receiver.close()
            self._closed = True
