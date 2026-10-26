"""In-place modal restoration plans; no transport or resume authorization.

An adapter must keep exclusive ownership and the job frozen through dispatch,
barrier completion and independent observation. Hashes identify evidence, not
authority. This component cannot establish physical continuity.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json

from .checkpoint import PauseCheckpoint, InvalidCheckpoint, _number


def _commands(modal):
    # Deliberately narrower than firmware parser ranges. These are software
    # eligibility bounds, not qualified material or machine operating limits.
    for name, maximum in (('fan_pwm', 255), ('feedrate_percent', 300), ('flow_percent', 300)):
        value = modal[name]
        if value != int(value) or not (0 if name == 'fan_pwm' else 1) <= value <= maximum:
            raise InvalidCheckpoint('unsupported_restore_' + name)
    if not 0 < modal['feedrate'] <= 60000:
        raise InvalidCheckpoint('unsupported_restore_feedrate')
    feed = format(Decimal(str(modal['feedrate'])), 'f')
    if len(feed) > 32:
        raise InvalidCheckpoint('unsupported_restore_feedrate_precision')
    return (
        'G91' if modal['xyz_relative'] else 'G90',
        'M83' if modal['e_relative'] else 'M82',
        'M106 P0 S' + str(int(modal['fan_pwm'])),
        'M220 S' + str(int(modal['feedrate_percent'])),
        'M221 T0 S' + str(int(modal['flow_percent'])),
        'G1 F' + feed,
    )


@dataclass(frozen=True, init=False)
class InPlaceRestorePlan:
    saved: PauseCheckpoint
    before: PauseCheckpoint
    planned_at: float
    max_observation_age: float
    commands: tuple[str, ...]
    id: str

    def __init__(self, saved, current, *, now, max_observation_age):
        if not isinstance(saved, PauseCheckpoint):
            raise InvalidCheckpoint('saved_checkpoint_required')
        reasons = saved.in_place_resume_blockers(current, now=now,
                                                max_observation_age=max_observation_age)
        reasons = [reason for reason in reasons if reason != 'modal_changed']
        original, live = saved.to_dict(), current.to_dict()
        if live['created_at'] <= original['created_at']:
            reasons.append('independent_current_observation_required')
        for field in ('units', 'tool', 'nozzle_target', 'bed_target'):
            if original['modal'][field] != live['modal'][field]:
                reasons.append(field + '_changed')
        if original['modal']['units'] != 'mm':
            reasons.append('millimeter_units_required')
        if reasons:
            raise InvalidCheckpoint(','.join(reasons))
        commands = _commands(original['modal'])
        payload = dict(saved=saved.id, before=current.id, planned_at=now,
                       max_observation_age=max_observation_age, commands=commands)
        for key, value in dict(saved=saved, before=current, planned_at=now,
                               max_observation_age=max_observation_age,
                               commands=commands).items():
            object.__setattr__(self, key, value)
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        object.__setattr__(self, 'id', hashlib.sha256(encoded.encode()).hexdigest())

    @property
    def marker(self):
        # 128-bit correlation token; the full hash remains in durable evidence.
        # Even with a uint32 line number/checksum/newline, the frame fits 64 B.
        return 'LLM_RS_' + self.id[:32]

    def verification_blockers(self, current, *, now, barrier_completed_at):
        """Compare fresh evidence after an adapter's completed execution barrier.

        A caller-supplied barrier timestamp is a contract, not proof this module
        observed execution. Adapters must bind it to this exact plan, account
        for partial/unknown dispatch durably, and never infer it from an ack.
        """
        _number(barrier_completed_at, 'barrier_completed_at', 0)
        reasons = self.saved.in_place_resume_blockers(current, now=now,
                                max_observation_age=self.max_observation_age)
        if not self.planned_at <= barrier_completed_at <= now:
            reasons.append('invalid_restore_barrier_time')
        if current.to_dict()['created_at'] <= barrier_completed_at:
            reasons.append('post_barrier_observation_required')
        return reasons
