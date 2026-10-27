"""Pre-open cold readiness validation of trusted unit-specific observations.

No discovery, port access, recovery, qualification creation or LLM approval tool.
"""
from dataclasses import asdict
import uuid
import json
from .cold_provider import ColdMachineQualification,digest,encoded,finite,label
from .journal import RequestRejected
from .service import StartupAdmission,StartupState


class ColdStartupReadiness:
    FIELDS={'request_sha256','observed_at','expires_at','evidence_id','unit','assignment_sha256',
        'protected_exclusion_sha256','driver_sha256','recovery_sha256','verified','temperatures','prior_state_reconciled'}
    FACTS={'development_assignment','protected_device_excluded','driver_qualified','recovery_qualified',
        'sensors_valid','workspace_clear','hardware_identity_verified','configuration_qualified'}

    def __init__(self,qualification,*,observe,clock):
        if type(qualification) is not ColdMachineQualification or not callable(observe) or not callable(clock):
            raise TypeError('unit qualification, pre-open physical producer and clock required')
        self.qualification=qualification;self.observe=observe;self.clock=clock

    def __call__(self,state):
        if type(state) is not StartupState:raise TypeError('actual startup state required')
        q=self.qualification.to_dict();started=self.clock()
        if not finite(started) or started<0:raise RequestRejected('invalid_startup_clock')
        request=dict(qualification_sha256=self.qualification.id,printer_id=q['printer_id'],startup_state=asdict(state),
            requested_at=started,request_nonce=uuid.uuid4().hex)
        expected=digest(request)
        try:data=json.loads(encoded(self.observe(json.loads(encoded(request)))))
        except (ValueError,TypeError):raise RequestRejected('invalid_preopen_observation') from None
        now=self.clock()
        if (type(data) is not dict or data.keys()!=self.FIELDS or data['request_sha256']!=expected
                or not label(data['evidence_id']) or not all(finite(x) for x in (now,data['observed_at'],data['expires_at']))
                or not started<=data['observed_at']<=now<data['expires_at']<=data['observed_at']+1
                or now-data['observed_at']>1 or type(data['prior_state_reconciled']) is not bool):
            raise RequestRejected('fresh_bound_preopen_observation_required')
        # Compare against the retained digest, not the callback's mutable request.
        if (type(data['unit']) is not dict or data['unit']!={k:q[k] for k in ('board','processor','bootloader','screen','firmware_sha256')}
                or any(data[k]!=q[k] for k in ('assignment_sha256','protected_exclusion_sha256','driver_sha256','recovery_sha256'))
                or type(data['verified']) is not dict or data['verified'].keys()!=self.FACTS
                or any(v is not True for v in data['verified'].values())):
            raise RequestRejected('preopen_unit_or_qualification_unconfirmed')
        if (state.has_history or state.thermal_recovery_required or state.restoration_recovery_required) and not data['prior_state_reconciled']:
            raise RequestRejected('startup_requires_reconciliation')
        temps=data['temperatures'];limits=self.qualification.policy.to_dict()['temperature_bounds']
        if type(temps) is not dict or temps.keys()!={'T','B'}:raise RequestRejected('preopen_cold_sensors_required')
        for name,bounds in limits.items():
            values=temps[name]
            if (type(values) is not dict or values.keys()!={'actual','target'} or not all(finite(v) for v in values.values())
                    or values['target']!=0 or not bounds[0]<=values['actual']<=bounds[1]):
                raise RequestRejected('preopen_cold_sensor_or_target_unqualified')
        # All checks are local, but their time still cannot extend the observation.
        now=self.clock()
        if not finite(now) or not data['observed_at']<=now<data['expires_at'] or now-data['observed_at']>1:
            raise RequestRejected('preopen_observation_expired_during_validation')
        return StartupAdmission(data['evidence_id'],data['prior_state_reconciled'])
