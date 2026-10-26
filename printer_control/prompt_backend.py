"""Experimental journal adapter for prompt transactions.

No physical transport factory is exposed. Transport ownership,
identity assignment and profile qualification remain the enclosing adapter's
responsibility. Only generic user-wait choice zero is implemented here.
"""
from dataclasses import asdict
import threading

from .journal import RequestRejected
from .service import Context, Outcome


class PromptBackend:
    supported_operations = ('prompt.inspect', 'prompt.bind', 'prompt.respond')
    supported_reads = ('printer.describe', 'printer.status', 'operation.get', 'printer.events', 'printer.observations')

    def capabilities(self):
        return {
            'scope': 'experimental_firmware_prompt_protocol',
            'physical_control_qualified': False,
            'transport_identity_qualification': 'required_from_enclosing_adapter',
            'prompt_profile': 'generic_user_wait_one_button_choice_zero',
            'completion_scope': 'firmware_user_wait_flag_only',
            'unsupported': ['job.stage', 'job.start', 'job.pause', 'job.resume', 'job.cancel',
                            'job.inspect', 'raw_gcode', 'reconnect', 'physical_recovery'],
        }

    def inspect(self):
        context = self.context()
        snapshot, observed = self.channel.snapshot()
        age = None if observed is None else self.clock() - observed
        return {
            'printer_id': context.printer_id,
            'connection_generation': context.generation,
            'state_version': context.state_version,
            'channel_state': self.channel.state,
            'invalidation_reason': self.channel.invalidation_reason,
            'blocking_event': None if self.channel.blocker is None else asdict(self.channel.blocker),
            'automatic_fault_recovery_supported': False,
            'session_bound': self.channel.bound,
            'prompt_snapshot': None if snapshot is None else asdict(snapshot),
            'snapshot_observed_at': observed,
            'snapshot_age_seconds': age,
            'snapshot_fresh': age is not None and 0 <= age <= self.channel.max_age,
            'physical_recovery_qualified': False,
            'prompt_guidance': self._guidance(snapshot, age),
        }

    def __init__(self, channel, *, printer_id, clock, pump, close_transport):
        if type(printer_id) is not str or not printer_id:
            raise ValueError('explicit printer identity required')
        self.channel, self.printer_id, self.clock = channel, printer_id, clock
        self.pump, self.close_transport = pump, close_transport
        self._lock = threading.RLock()
        self._version = 0
        self._last = None
        self._operations = {}
        self._closed = False
        self._physical_required = set()
        self._last_physical_receipt = None

    def guard(self):
        return self._lock

    def context(self):
        now = self.clock()
        self.channel.tick(now)
        lost = False
        try:
            data = self.pump()
        except OSError:
            lost, data = True, b''
        # A bounded receive may wait. Never backdate its bytes to the clock
        # sampled before that wait, including when a receipt deadline expires.
        now = self.clock()
        if lost:
            self.channel.invalidate('receive_stream_unavailable', now=now)
        elif data:
            self.channel.feed(data, now=now)
        else:
            self.channel.tick(now)
        transaction = self.channel.latest
        if (transaction is not None and transaction.status == 'rejected'
                and transaction.command.kind == 'respond' and transaction.receipt is not None
                and transaction.receipt.result == 9):
            self._physical_required.add((transaction.receipt.session, transaction.receipt.prompt))
            self._last_physical_receipt = transaction.receipt
        current = (self.channel.latest, self.channel.state)
        if current != self._last:
            self._last = current
            self._version += 1
        # This is a fresh observation of the channel, not refreshed telemetry.
        # Mutation validation separately requires a fresh firmware snapshot.
        return Context(self.printer_id, self.channel.generation, self._version, now)

    def _guidance(self, snapshot, age):
        # A rejected command is diagnostic evidence, not an accepted actionable
        # snapshot. Retain the intervention reason while a fresh inspect is needed.
        if snapshot is None and self._last_physical_receipt is not None:
            snapshot = self._last_physical_receipt
            age = None
        if snapshot is None:
            return {'category': 'unknown', 'allowed_responses': [],
                    'requires_physical_action': None, 'next_action': 'inspect_prompt'}
        physical = snapshot.active and (
            (snapshot.session, snapshot.prompt) in self._physical_required or snapshot.reason == 1)
        fresh = age is not None and 0 <= age <= self.channel.max_age
        allowed = (fresh and self.channel.state == 'ready' and self.channel.bound
                   and snapshot.session == self.channel.session and snapshot.active
                   and snapshot.shown and snapshot.waiting and snapshot.reason == 2
                   and snapshot.buttons == 1 and not physical)
        category = {0: 'none', 1: 'filament_runout', 2: 'user_wait',
                    3: 'filament_reheat', 4: 'pause_resume', 5: 'information'}[snapshot.reason]
        return {'category': category, 'prompt_id': snapshot.prompt,
                'requires_physical_action': True if physical else None,
                'allowed_responses': [{'choice': 0, 'action': 'continue'}] if allowed else [],
                'next_action': 'physical_intervention_required' if physical else
                    ('respond_to_exact_prompt' if allowed else 'inspect_or_reconcile'),
                'firmware_message_available': False,
                'evidence': 'firmware_reason_and_correlated_receipt'}

    def validate(self, operation, arguments):
        if self._closed or self.channel.state != 'ready':
            raise RequestRejected('prompt_channel_not_ready')
        if operation not in ('prompt.inspect', 'prompt.bind', 'prompt.respond'):
            raise RequestRejected('unsupported_prompt_operation')
        if operation in ('prompt.inspect', 'prompt.bind'):
            if arguments:
                raise RequestRejected('unexpected_prompt_arguments')
        elif (arguments.keys() != {'prompt', 'choice'} or type(arguments['prompt']) is not int
              or type(arguments['choice']) is not int):
            raise RequestRejected('invalid_prompt_arguments')
        if operation == 'prompt.inspect':
            return
        if (operation == 'prompt.respond' and
                (self.channel.session, arguments['prompt']) in self._physical_required):
            raise RequestRejected('physical_intervention_required')
        snapshot, observed = self.channel.snapshot()
        now = self.clock()
        if snapshot is None or not 0 <= now - observed <= self.channel.max_age:
            raise RequestRejected('fresh_prompt_snapshot_required')
        if operation == 'prompt.bind':
            if self.channel.bound or snapshot.session:
                raise RequestRejected('firmware_already_bound')
        elif (snapshot.session, snapshot.prompt) in self._physical_required:
            raise RequestRejected('physical_intervention_required')
        elif (not self.channel.bound or snapshot.session != self.channel.session
              or not snapshot.active or not snapshot.shown or not snapshot.waiting
              or snapshot.reason != 2 or snapshot.buttons != 1
              or arguments != {'prompt': snapshot.prompt, 'choice': 0}):
            raise RequestRejected('unsupported_or_stale_user_wait')

    def dispatch(self, operation_id, operation, arguments, *, deadline):
        transaction = self.channel.dispatch(operation.split('.')[1], now=self.clock(),
                                            deadline=deadline, **arguments)
        self._operations[operation_id] = (operation, transaction.command.request)

    def poll(self, operation_id):
        operation, request = self._operations[operation_id]
        transaction = self.channel.latest
        if transaction is None or transaction.command.request != request:
            return Outcome('outcome_unknown', {'reason': 'prompt_transaction_replaced'})
        if transaction.status == 'running':
            return None
        if transaction.status == 'outcome_unknown' or self.channel.state in ('uncertain', 'disconnected'):
            return Outcome('outcome_unknown', {'reason': self.channel.invalidation_reason or transaction.reason})
        evidence = {'scope': 'firmware_prompt_protocol', 'physical_recovery_qualified': False,
                    'wire_request': request, 'receipt': asdict(transaction.receipt)}
        if transaction.status == 'rejected':
            return Outcome('failed', {**evidence, 'reason': transaction.reason})
        if operation == 'prompt.respond':
            receipt = transaction.receipt
            if receipt.active or receipt.waiting:
                return Outcome('outcome_unknown', {**evidence, 'reason': 'wait_resolution_not_observed'})
            evidence['resolution'] = 'firmware_user_wait_cleared'
        return Outcome('completed', evidence)

    def close(self):
        if not self._closed:
            self.close_transport() # failure must retain the service owner lease
            self.channel.disconnect(self.clock())
            self._closed = True
