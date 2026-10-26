"""Target-effect adapter for the pinned virtual printer, never physical USB."""
import re
import threading

from .journal import RequestRejected
from .octoprint_virtual import OctoPrintVirtualBackend
from .thermal_pause import ThermalPauseLimits, finite


class VirtualThermalTransport:
    FIELDS = {'effect_id', 'kind', 'nozzle_target', 'bed_target', 'deadline'}

    def __init__(self, backend, limits, *, clock):
        if not isinstance(backend, OctoPrintVirtualBackend) or not isinstance(limits, ThermalPauseLimits):
            raise ValueError('virtual_backend_and_limits_required')
        if getattr(backend, 'thermal_transport', None) is not None:
            raise ValueError('thermal_transport_already_installed')
        self.backend, self.limits, self.clock = backend, limits, clock
        self.device = backend.sender._serial
        self.lock = threading.RLock()
        self.actions = {}
        self.active = None
        self.received = None
        self.failed = False
        self.retired = False
        witness = backend.witness
        self._witness_epoch = witness.snapshot()[2] if witness and witness.active and witness.frozen else None
        self._check_device()
        backend.thermal_transport = self

    def _check_device(self):
        from octoprint.plugins.virtual_printer.virtual import VirtualPrinter
        sender = self.backend.sender
        if (sender._port != 'VIRTUAL' or sender._serial is not self.device or
                not isinstance(self.device, VirtualPrinter) or self.device._killed):
            raise RequestRejected('virtual_thermal_transport_unavailable')
        witness = self.backend.witness
        paused = (sender.getState() == sender.STATE_PAUSED and witness is not None and
                  witness.active and witness.frozen and not witness.allow_job_commands)
        if sender.getState() != sender.STATE_OPERATIONAL and not paused:
            raise RequestRejected('virtual_thermal_requires_idle_or_guarded_pause')
        if self.backend._fault or self.failed or self.retired:
            raise RequestRejected('virtual_thermal_requires_recovery')

    def retire(self):
        """Detach only a fully receipted target sequence after verified resume."""
        with self.lock:
            if self.retired:
                return
            if self.failed or self.active is None or self.received != self.active:
                raise RequestRejected('thermal_transport_not_ready_to_retire')
            self.retired = True
            if getattr(self.backend,'thermal_transport',None) is self:
                self.backend.thermal_transport = None

    def send_targets(self, action):
        with self.lock:
            self._check_device()
            if type(action) is not dict or action.keys() != self.FIELDS:
                raise RequestRejected('invalid_thermal_effect_fields')
            effect = action['effect_id']
            if type(effect) is not str or re.fullmatch('[0-9a-f]{32}', effect) is None or action['kind'] != 'set_pause_targets':
                raise RequestRejected('invalid_thermal_effect_identity')
            if any(not finite(action[k]) for k in ('nozzle_target','bed_target','deadline')):
                raise RequestRejected('invalid_thermal_effect_values')
            if not 0 <= action['nozzle_target'] <= self.limits.max_nozzle or not 0 <= action['bed_target'] <= self.limits.max_bed:
                raise RequestRejected('thermal_effect_target_out_of_range')
            if any(float(action[k]).is_integer() is False for k in ('nozzle_target', 'bed_target')):
                raise RequestRejected('fractional_virtual_targets_unsupported')
            now = self.clock()
            if not finite(now) or action['deadline'] <= now:
                raise RequestRejected('thermal_effect_expired')
            if effect in self.actions or len(self.actions) >= 2:
                raise RequestRejected('thermal_effect_replay_or_session_limit')
            if self.active is not None and self.received != self.active:
                raise RequestRejected('thermal_effect_still_pending')
            self.actions[effect] = dict(action)
            self.active, self.received = effect, None
            witness = self.backend.witness
            commands = (f"M104 S{action['nozzle_target']}", f"M140 S{action['bed_target']}",
                        'M118 E1 LLM_THERM_' + effect)
            tags = {'source:llm-thermal-pause'}
            try:
                if witness is not None and witness.active and witness.frozen:
                    tags.add(witness.authorize_thermal(effect, commands, self._witness_epoch))
                    # M104 and M140 each advance queued and sent epochs; the
                    # marker is read-only. Any extra mutation invalidates continuity.
                    self._witness_epoch += 4
                for command in commands:
                    if not self.backend.sender.sendCommand(command, tags=tags):
                        raise OSError('thermal target command not queued')
            except Exception:
                if witness is not None:
                    witness.revoke_thermal()
                self.failed = True
                raise

    def observe(self, line):
        with self.lock:
            if self.retired:
                return
            if self.active and line.strip() == 'echo:LLM_THERM_' + self.active:
                self.received = self.active

    def snapshot(self):
        with self.lock:
            self._check_device()
            device = self.device
            action = self.actions.get(self.active)
            if self.received == self.active and self.active is not None and self._witness_epoch is not None:
                if self.backend.witness.snapshot()[2] != self._witness_epoch:
                    self.failed = True
                    raise RequestRejected('virtual_thermal_witness_changed')
            matches = (action and self.received == self.active and
                       device.targetTemp[0] == action['nozzle_target'] and
                       device.bedTargetTemp == action['bed_target'])
            return {'effect_id': self.active if matches else None,
                    'nozzle_target':device.targetTemp[0], 'bed_target':device.bedTargetTemp,
                    'nozzle_actual':device.temp[0], 'bed_actual':device.bedTemp,
                    'evidence_type':'virtual_marker_and_internal_targets',
                    'physical_thermal_qualified':False}
