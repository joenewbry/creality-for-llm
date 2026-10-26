"""Experimental deterministic extended-pause policy, not a heater controller.

No transport or physical profile is provided. Callers must durably journal each
returned action before dispatch and supply independent, qualified observations.
A reconnect, evidence gap or failed effect invalidates the policy irreversibly.
"""
from dataclasses import dataclass, fields
import math
import uuid

from .checkpoint import PauseCheckpoint


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class ThermalPauseLimits:
    short_seconds: float
    max_pause_seconds: float
    standby_nozzle: float
    max_nozzle: float
    max_bed: float
    effect_timeout_seconds: float
    reheat_timeout_seconds: float
    stable_seconds: float
    tolerance_celsius: float
    max_observation_gap_seconds: float

    def __post_init__(self):
        if any(not finite(getattr(self, f.name)) for f in fields(self)):
            raise ValueError('finite_policy_limits_required')
        if not (0 < self.short_seconds < self.max_pause_seconds and
                0 <= self.standby_nozzle < self.max_nozzle and self.max_bed > 0 and
                self.effect_timeout_seconds > 0 and self.reheat_timeout_seconds > 0 and
                0 < self.stable_seconds < self.reheat_timeout_seconds and
                0 < self.tolerance_celsius < min(self.max_nozzle, self.max_bed) and
                0 < self.max_observation_gap_seconds <= self.effect_timeout_seconds):
            raise ValueError('invalid_pause_policy_limits')


class ThermalPausePolicy:
    """Pure decision engine for a single continuously observed pause interval.

    Observations use monotonic, strictly increasing sample times from one host
    session. A heater-target receipt is separate from temperature stabilization.
    The retained bed target and motor-hold lease must remain valid throughout.
    """
    FIELDS = {'sample_at', 'printer_id', 'generation', 'host_session', 'job_id',
              'file_sha256', 'config_sha256', 'firmware_id', 'checkpoint_id',
              'position_valid', 'part_undisturbed', 'sensors_valid', 'fault_free',
              'motors_held', 'motor_hold_until', 'nozzle_actual', 'bed_actual',
              'nozzle_target', 'bed_target', 'effect_id'}
    IDENTITY = ('printer_id', 'generation', 'host_session', 'job_id', 'file_sha256',
                'config_sha256', 'firmware_id')
    PROOFS = ('position_valid', 'part_undisturbed', 'sensors_valid', 'fault_free', 'motors_held')

    def __init__(self, checkpoint, limits, *, started_at):
        if not isinstance(checkpoint, PauseCheckpoint) or not isinstance(limits, ThermalPauseLimits):
            raise ValueError('checkpoint_and_limits_required')
        if not finite(started_at) or started_at < 0:
            raise ValueError('invalid_monotonic_start')
        self.saved = checkpoint.to_dict()
        if not all(self.saved['evidence'].values()):
            raise ValueError('pause_checkpoint_evidence_required')
        if limits.max_pause_seconds > self.saved['expires_at'] - self.saved['created_at']:
            raise ValueError('policy_exceeds_checkpoint_interval')
        modal = self.saved['modal']
        if not limits.standby_nozzle < modal['nozzle_target'] <= limits.max_nozzle or not 0 <= modal['bed_target'] <= limits.max_bed:
            raise ValueError('checkpoint_targets_outside_policy')
        self.checkpoint_id = checkpoint.id
        self.limits = limits
        self.start = self.last_now = self.last_sample = started_at
        self.state = 'short_pause'
        self.reason = None
        self.pending = None
        self.expected_nozzle = modal['nozzle_target']
        self.expected_bed = modal['bed_target']
        self.stable_since = None
        self.reheat_started = None
        self.consumed = False

    def invalidate(self, reason):
        if self.state != 'invalid':
            self.state, self.reason = 'invalid', reason
            self.stable_since = None

    def _action(self, phase, target, now):
        self.state = phase
        self.pending = {'effect_id': uuid.uuid4().hex, 'kind': 'set_pause_targets',
                        'nozzle_target': target, 'bed_target': self.expected_bed,
                        'deadline': now + self.limits.effect_timeout_seconds}
        return dict(self.pending)

    def status(self):
        return {'state': self.state, 'reason': self.reason,
                'checkpoint_id': self.checkpoint_id,
                'resume_ready': self.state == 'ready' and not self.consumed,
                'physical_pause_qualified': False,
                'pending_effect': dict(self.pending) if self.pending else None,
                'requires_recovery': self.state == 'invalid',
                'stream_resume_consumed': self.consumed}

    def step(self, observation, *, now, request_resume=False):
        """Return at most one new action; repeated polling never reissues it.

        Invalidity and elapsed-time deadlines are latched. A caller missing an
        effect receipt must reconcile rather than generate a fresh heater write.
        """
        if self.state == 'invalid' or self.consumed:
            return {'action': None, **self.status()}
        if type(request_resume) is not bool:
            self.invalidate('invalid_resume_request')
            return {'action': None, **self.status()}
        if not self._observe(observation, now=now):
            return {'action': None, **self.status()}
        if self.pending:
            if now >= self.pending['deadline']:
                self.invalidate('target_effect_unconfirmed')
            elif observation['effect_id'] == self.pending['effect_id']:
                if observation['nozzle_target'] != self.pending['nozzle_target']:
                    self.invalidate('target_receipt_mismatch')
                else:
                    self.expected_nozzle = self.pending['nozzle_target']
                    self.state = 'standby' if self.state == 'entering_standby' else 'reheating'
                    self.pending = None
            elif observation['nozzle_target'] not in (self.expected_nozzle, self.pending['nozzle_target']):
                self.invalidate('unexpected_nozzle_target')
            return {'action': None, **self.status()}
        if observation['nozzle_target'] != self.expected_nozzle:
            self.invalidate('unexpected_nozzle_target')
        if self.state == 'invalid':
            return {'action': None, **self.status()}
        action = None
        if self.state in ('short_pause', 'standby') and request_resume:
            self.reheat_started = now
            action = self._action('requesting_reheat', self.saved['modal']['nozzle_target'], now)
        elif self.state == 'short_pause' and now - self.start >= self.limits.short_seconds:
            action = self._action('entering_standby', self.limits.standby_nozzle, now)
        elif self.state in ('reheating', 'ready'):
            if now - self.reheat_started >= self.limits.reheat_timeout_seconds:
                self.invalidate('reheat_timeout')
            else:
                at_temperature = (abs(observation['nozzle_actual'] - self.expected_nozzle) <= self.limits.tolerance_celsius and
                                  (self.expected_bed == 0 or abs(observation['bed_actual'] - self.expected_bed) <= self.limits.tolerance_celsius))
                if not at_temperature:
                    self.stable_since = None
                    self.state = 'reheating'
                else:
                    if self.stable_since is None:
                        self.stable_since = now
                    if now - self.stable_since >= self.limits.stable_seconds:
                        self.state = 'ready'
        return {'action': action, **self.status()}

    def _observe(self, observation, *, now):
        if not finite(now) or now < self.last_now:
            self.invalidate('clock_reversed_or_invalid')
            return False
        self.last_now = now
        if now - self.start >= self.limits.max_pause_seconds:
            self.invalidate('pause_limit_expired')
        elif type(observation) is not dict or observation.keys() != self.FIELDS:
            self.invalidate('incomplete_pause_observation')
        elif (not finite(observation['sample_at']) or
              not self.last_sample < observation['sample_at'] <= now or
              now - observation['sample_at'] > self.limits.max_observation_gap_seconds or
              observation['sample_at'] - self.last_sample > self.limits.max_observation_gap_seconds):
            self.invalidate('observation_gap_or_replay')
        else:
            self.last_sample = observation['sample_at']
            if any(observation[k] != self.saved[k] for k in self.IDENTITY) or observation['checkpoint_id'] != self.checkpoint_id:
                self.invalidate('pause_identity_changed')
            elif any(observation[k] is not True for k in self.PROOFS):
                self.invalidate('pause_continuity_unconfirmed')
            elif not finite(observation['motor_hold_until']) or observation['motor_hold_until'] <= now:
                self.invalidate('motor_hold_expired')
            elif any(not finite(observation[k]) for k in ('nozzle_actual','bed_actual','nozzle_target','bed_target')):
                self.invalidate('invalid_temperature_observation')
            elif not (0 <= observation['nozzle_actual'] <= self.limits.max_nozzle and
                      0 <= observation['bed_actual'] <= self.limits.max_bed):
                self.invalidate('temperature_outside_policy')
            elif observation['bed_target'] != self.expected_bed:
                self.invalidate('bed_hold_target_changed')
            elif self.expected_bed > 0 and abs(observation['bed_actual'] - self.expected_bed) > self.limits.tolerance_celsius:
                self.invalidate('bed_hold_temperature_lost')
        if self.state == 'invalid':
            return False
        return True

    def recheck_consumed(self, observation, *, now):
        """Fresh thermal/continuity check during restoration, never a new grant.

        Consuming initial eligibility does not freeze temperature or extend the
        motor lease. A gap or drift irreversibly invalidates the consumed policy.
        """
        if self.state != 'consumed' or not self.consumed:
            raise ValueError('thermal_eligibility_not_consumed')
        if self._observe(observation, now=now):
            target = self.saved['modal']['nozzle_target']
            if observation['nozzle_target'] != target:
                self.invalidate('restored_nozzle_target_changed')
            elif abs(observation['nozzle_actual'] - target) > self.limits.tolerance_celsius:
                self.invalidate('restored_nozzle_temperature_lost')
        if self.state == 'invalid':
            raise ValueError('thermal_resume_recheck_failed:' + self.reason)
        return {'checkpoint_id':self.checkpoint_id,'thermal_rechecked':True,
                'sample_at':observation['sample_at'],'physical_pause_qualified':False}

    def consume_resume(self, observation, *, now):
        """One-use eligibility, still requiring a separate mode/position restore.

        This consumes authority only. It neither sends a resume command nor
        asserts that the planner is running or any physical move occurred.
        """
        result = self.step(observation, now=now)
        if not result['resume_ready']:
            raise ValueError('thermal_resume_not_ready')
        self.consumed = True
        self.state = 'consumed'
        return {'checkpoint_id': self.checkpoint_id, 'thermal_policy_satisfied': True,
                'restoration_still_required': True, 'physical_pause_qualified': False}
