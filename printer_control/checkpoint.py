"""Immutable, hash-addressed pause evidence for durable operation receipts.

This is the in-place, continuously powered policy. A checkpoint is evidence to
validate, never an instruction to replay a file after a host or printer reset.
"""
import hashlib
import json
import math
import re
from dataclasses import dataclass


class InvalidCheckpoint(ValueError):
    pass


def _number(value, name, low=None, high=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise InvalidCheckpoint('invalid_' + name)
    if (low is not None and value < low) or (high is not None and value > high):
        raise InvalidCheckpoint('out_of_range_' + name)


@dataclass(frozen=True, init=False)
class PauseCheckpoint:
    _encoded: str
    id: str
    FIELDS = {'schema_version', 'printer_id', 'generation', 'host_session', 'job_id',
              'file_sha256', 'firmware_id', 'config_sha256', 'cursor', 'next_line_number',
              'created_at', 'expires_at', 'position', 'modal', 'offsets', 'leveling_active',
              'evidence'}
    PROOFS = {'queue_drained', 'position_valid', 'motors_held', 'fault_free',
              'sensors_valid', 'part_undisturbed'}
    MODES = {'units', 'xyz_relative', 'e_relative', 'feedrate', 'tool', 'fan_pwm',
             'nozzle_target', 'bed_target', 'feedrate_percent', 'flow_percent'}

    def __init__(self, data):
        if type(data) is not dict or data.keys() != self.FIELDS:
            raise InvalidCheckpoint('missing_or_unknown_checkpoint_fields')
        if type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise InvalidCheckpoint('unsupported_schema_version')
        for key in ('printer_id', 'generation', 'host_session', 'job_id', 'firmware_id'):
            if type(data[key]) is not str or not data[key].strip() or len(data[key]) > 128:
                raise InvalidCheckpoint('invalid_' + key)
        for key in ('file_sha256', 'config_sha256'):
            if type(data[key]) is not str or re.fullmatch('[0-9a-f]{64}', data[key]) is None:
                raise InvalidCheckpoint('invalid_' + key)
        for key in ('cursor', 'next_line_number'):
            if type(data[key]) is not int or data[key] < 0:
                raise InvalidCheckpoint('invalid_' + key)
        _number(data['created_at'], 'created_at', 0)
        _number(data['expires_at'], 'expires_at', 0)
        if data['expires_at'] <= data['created_at']:
            raise InvalidCheckpoint('invalid_pause_interval')
        for vector in ('position', 'offsets'):
            if type(data[vector]) is not dict or data[vector].keys() != {'x', 'y', 'z', 'e'}:
                raise InvalidCheckpoint('incomplete_' + vector)
            for axis, value in data[vector].items():
                _number(value, vector + '_' + axis)
        if type(data['leveling_active']) is not bool:
            raise InvalidCheckpoint('unknown_leveling_state')
        evidence = data['evidence']
        if type(evidence) is not dict or evidence.keys() != self.PROOFS or any(type(v) is not bool for v in evidence.values()):
            raise InvalidCheckpoint('incomplete_evidence')
        modal = data['modal']
        if type(modal) is not dict or modal.keys() != self.MODES:
            raise InvalidCheckpoint('incomplete_modal_state')
        if modal['units'] not in ('mm', 'inch'):
            raise InvalidCheckpoint('unknown_units')
        for mode in ('xyz_relative', 'e_relative'):
            if type(modal[mode]) is not bool:
                raise InvalidCheckpoint('unknown_' + mode)
        if type(modal['tool']) is not int or modal['tool'] != 0:
            raise InvalidCheckpoint('unsupported_tool')
        _number(modal['feedrate'], 'feedrate', 0)
        if modal['feedrate'] == 0:
            raise InvalidCheckpoint('zero_feedrate')
        _number(modal['fan_pwm'], 'fan_pwm', 0, 255)
        for name in ('nozzle_target', 'bed_target', 'feedrate_percent', 'flow_percent'):
            _number(modal[name], name, 0)
        # Thermal limits belong to a qualified hardware/material policy; this
        # schema does not authorize any target merely because it is finite.
        encoded = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
        object.__setattr__(self, '_encoded', encoded)
        object.__setattr__(self, 'id', hashlib.sha256(encoded.encode()).hexdigest())

    def to_dict(self):
        return json.loads(self._encoded)

    def envelope(self):
        return {'checkpoint_id': self.id, 'checkpoint': self.to_dict()}

    @classmethod
    def from_envelope(cls, envelope):
        if type(envelope) is not dict or envelope.keys() != {'checkpoint_id', 'checkpoint'}:
            raise InvalidCheckpoint('invalid_checkpoint_envelope')
        checkpoint = cls(envelope['checkpoint'])
        if checkpoint.id != envelope['checkpoint_id']:
            raise InvalidCheckpoint('checkpoint_hash_mismatch')
        return checkpoint

    def in_place_resume_blockers(self, current, *, now, max_observation_age):
        """Compare two fully specified observations; do not issue commands.

        Current evidence must be acquired again, not copied from this saved
        checkpoint. External adapters are responsible for its provenance.
        Changed settings require a separately qualified restoration plan.
        """
        _number(now, 'now', 0)
        _number(max_observation_age, 'max_observation_age', 0)
        if not isinstance(current, PauseCheckpoint):
            raise InvalidCheckpoint('current_evidence_required')
        saved, live = self.to_dict(), current.to_dict()
        reasons = []
        if now < saved['created_at'] or now >= saved['expires_at']:
            reasons.append('pause_interval_expired_or_clock_reversed')
        if not 0 <= now - live['created_at'] <= max_observation_age or now >= live['expires_at']:
            reasons.append('current_observation_stale')
        for field in ('printer_id', 'generation', 'host_session', 'job_id', 'file_sha256',
                      'firmware_id', 'config_sha256', 'cursor', 'next_line_number',
                      'position', 'modal', 'offsets', 'leveling_active'):
            if saved[field] != live[field]:
                reasons.append(field + '_changed')
        for proof in sorted(self.PROOFS):
            if not saved['evidence'][proof] or not live['evidence'][proof]:
                reasons.append(proof + '_unconfirmed')
        return reasons
