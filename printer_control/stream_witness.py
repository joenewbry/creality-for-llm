"""OctoPrint hooks recording transmitted modes and queued state changes."""
import threading
import re
import uuid
from .modal import ModalTracker
from .pause_restore import InPlaceRestorePlan


class StreamWitness:
    READ_ONLY = {'M105', 'M114', 'M115', 'M27', 'M503', 'M400', 'M117', 'M118'}
    TRANSPORT = {'M110', 'M155', 'M154'}

    def __init__(self):
        self.lock = threading.RLock()
        self.tracker = ModalTracker()
        self.mutation_epoch = 0
        self.active = False
        self.frozen = False
        self.allow_job_commands = True
        self._pause_grant = None
        self._shutdown_grant = None

    def start_job(self):
        with self.lock:
            self.tracker = ModalTracker()
            self.mutation_epoch += 1
            self.active = True
            self.frozen = False
            self.allow_job_commands = True
            self._pause_grant = None
            self._shutdown_grant = None

    def authorize_shutdown(self, token):
        """One heater-off sequence, independent of revoked pause authority."""
        with self.lock:
            if not self.active or self._shutdown_grant is not None:
                raise ValueError('shutdown_grant_unavailable')
            if type(token) is not str or re.fullmatch('[0-9a-f]{32}',token) is None:
                raise ValueError('invalid_shutdown_token')
            self.invalidate_resume()
            tag='shutdown-grant:'+uuid.uuid4().hex
            self._shutdown_grant={'tag':tag,'commands':('M104 S0','M140 S0','M400','M118 E1 LLM_OFF_'+token),
                'indices':{phase:0 for phase in ('queuing','queued','sending','sent')}}
            return tag,self._shutdown_grant['commands']

    def authorize_thermal(self, effect_id, commands, expected_epoch):
        """Grant one exact target/marker sequence; never preserve old resume authority.

        This internal grant does not qualify temperatures. The policy/adapter
        must validate them first. Any sent target still advances the modal epoch.
        """
        with self.lock:
            if (not self.active or not self.frozen or self.allow_job_commands or
                    self._pause_grant is not None or self._shutdown_grant is not None or self.mutation_epoch != expected_epoch):
                raise ValueError('thermal_witness_not_frozen_at_expected_epoch')
            if (type(effect_id) is not str or re.fullmatch('[0-9a-f]{32}', effect_id) is None or
                    type(commands) not in (list, tuple) or len(commands) != 3 or
                    any(type(c) is not str for c in commands) or
                    re.fullmatch(r'M104 S[0-9]+(?:\.[0-9]+)?', commands[0]) is None or
                    re.fullmatch(r'M140 S[0-9]+(?:\.[0-9]+)?', commands[1]) is None or
                    commands[2] != 'M118 E1 LLM_THERM_' + effect_id):
                raise ValueError('invalid_thermal_witness_sequence')
            return self._grant('thermal', commands)

    def _grant(self, kind, commands):
        tag = kind + '-grant:' + uuid.uuid4().hex
        self._pause_grant = {'tag': tag, 'commands': tuple(commands),
            'indices': {phase: 0 for phase in ('queuing', 'queued', 'sending', 'sent')}}
        return tag

    def authorize_restore(self, plan, expected_epoch, *, now):
        """Permit only the validated plan plus barrier/marker while job stays frozen.

        The adapter must persist dispatch intent first, bind the plan to its
        current checkpoint/epoch, and independently qualify execution afterward.
        A grant is not resume authority and does not renew the pause deadline.
        """
        with self.lock:
            if (not self.active or not self.frozen or self.allow_job_commands or
                    self._pause_grant is not None or self._shutdown_grant is not None or self.mutation_epoch != expected_epoch):
                raise ValueError('restore_witness_not_frozen_at_expected_epoch')
            if not isinstance(plan, InPlaceRestorePlan):
                raise ValueError('restore_plan_required')
            if now < plan.planned_at:
                raise ValueError('restore_clock_reversed')
            # Re-run admission against the original evidence at dispatch time.
            InPlaceRestorePlan(plan.saved, plan.before, now=now,
                               max_observation_age=plan.max_observation_age)
            commands = plan.commands + ('M400', 'M118 E1 ' + plan.marker)
            return self._grant('restore', commands)

    def revoke_pause(self):
        with self.lock:
            if self._pause_grant is not None:
                self._pause_grant = None
                self.mutation_epoch += 1

    def invalidate_resume(self):
        """Close host dispatch authority; already transmitted moves may continue."""
        with self.lock:
            self._pause_grant = None
            self.frozen = True
            self.allow_job_commands = False
            self.mutation_epoch += 1

    def revoke_thermal(self):
        # Compatibility for thermal coordinator interruption: revoke either kind.
        self.revoke_pause()

    def _pause_phase(self, phase, command, tags):
        shutdown=any(str(tag).startswith('shutdown-grant:') for tag in tags)
        tagged = shutdown or any(str(tag).startswith(('thermal-grant:', 'restore-grant:')) for tag in tags)
        if not tagged:
            return False
        grant = self._shutdown_grant if shutdown else self._pause_grant
        if phase not in ('queuing','queued','sending','sent'):
            return False
        phases = ('queuing','queued','sending','sent')
        valid = grant is not None and grant['tag'] in tags
        if valid:
            index = grant['indices'][phase]
            valid = index < len(grant['commands']) and command == grant['commands'][index]
            if valid and phase != 'queuing':
                valid = grant['indices'][phases[phases.index(phase)-1]] > index
        if not valid:
            if shutdown:self._shutdown_grant=None
            self.revoke_pause()
            self.mutation_epoch += 1
            return None
        grant['indices'][phase] += 1
        if phase == 'sent' and grant['indices'][phase] == len(grant['commands']):
            if shutdown:self._shutdown_grant=None
            else:self._pause_grant = None
        return True

    def hook(self, sender, phase, command, command_type, gcode, **kwargs):
        with self.lock:
            if not self.active:
                return
            tags = kwargs.get('tags') or set()
            if gcode in {'M112', 'M410'} and phase in ('queuing', 'sending'):
                self._shutdown_grant=None
                self.revoke_pause()
            permitted_effect = self._pause_phase(phase, command, tags)
            if permitted_effect is None:
                return (None,)
            if phase in ('queuing', 'sending') and self.frozen and gcode not in self.READ_ONLY:
                permitted = permitted_effect or (self.allow_job_commands and bool({'source:job', 'source:file'} & tags)) or gcode in {'M112', 'M410'}
                if not permitted:
                    self.revoke_pause()
                    self.mutation_epoch += 1
                    return (None,)
            if phase == 'queued' and gcode not in self.READ_ONLY:
                self.mutation_epoch += 1
            if phase == 'sent' and gcode not in self.TRANSPORT:
                self.tracker.observe(command)
                if gcode not in self.READ_ONLY:
                    self.mutation_epoch += 1

    def snapshot(self):
        with self.lock:
            return self.tracker.snapshot(), self.tracker.blockers(), self.mutation_epoch

    def freeze(self, expected_epoch):
        with self.lock:
            if self.mutation_epoch != expected_epoch or self._pause_grant is not None or self._shutdown_grant is not None:
                return False
            self.frozen = True
            self.allow_job_commands = False
            return True

    def arm_resume(self, expected_epoch):
        with self.lock:
            if self.mutation_epoch != expected_epoch or not self.frozen or self._pause_grant is not None or self._shutdown_grant is not None:
                return False
            # Keep rejecting out-of-band mutations after resume is armed.
            self.allow_job_commands = True
            return True
