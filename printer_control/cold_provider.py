"""Cold assigned-job provider. Trusted physical inputs are never supplied by LLM tools.

This module implements evidence admission; it neither invents nor acquires the
unit's missing qualification or live physical observations, and opens no device.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import uuid

from .assigned_job import AssignedJobBackend, AssignedJobEvidence
from .checkpoint import PauseCheckpoint
from .cold_preflight import ColdJobPolicy
from .journal import RequestRejected


def encoded(value):
    result=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(result.encode())>262144:raise ValueError('bounded cold evidence required')
    return result


def stable(value):
    if type(value) is dict:return {k:stable(v) for k,v in value.items() if k!='arrival_age_seconds'}
    if type(value) is list:return [stable(v) for v in value]
    return value


def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
def finite(v):return type(v) in (int,float) and math.isfinite(v)
def sha(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None
def label(v):return type(v) is str and bool(v.strip()) and len(v)<=128


@dataclass(frozen=True,init=False)
class ColdMachineQualification:
    _encoded: str
    id: str
    policy: ColdJobPolicy
    FIELDS={'schema_version','qualification_id','printer_id','board','processor','bootloader','screen',
        'firmware_sha256','configuration_sha256','reported_settings_sha256','coverage_sha256',
        'assignment_sha256','protected_exclusion_sha256','driver_sha256','recovery_sha256',
        'coordinate_frame','timer_policy','position_tolerance_mm','max_pause_seconds','cold_policy_sha256'}

    def __init__(self,data,*,policy):
        if type(data) is not dict or data.keys()!=self.FIELDS or type(policy) is not ColdJobPolicy:
            raise ValueError('complete cold qualification and policy required')
        if type(data['schema_version']) is not int or data['schema_version']!=1:
            raise ValueError('unsupported cold qualification')
        for name in ('qualification_id','printer_id','board','processor','bootloader','screen'):
            if not label(data[name]):raise ValueError('explicit unit qualification required')
        for name in self.FIELDS:
            if name.endswith('_sha256') and not sha(data[name]):raise ValueError('qualification digest required')
        if (data['coordinate_frame']!='native_zero_offsets_no_leveling' or data['timer_policy']!='inactivity_shutdown_disabled'
                or data['cold_policy_sha256']!=policy.id
                or not finite(data['position_tolerance_mm']) or not 0<data['position_tolerance_mm']<=.01
                or not finite(data['max_pause_seconds']) or not 0<data['max_pause_seconds']<=3600):
            raise ValueError('unsupported cold qualification policy')
        text=encoded(data)
        object.__setattr__(self,'_encoded',text);object.__setattr__(self,'id',hashlib.sha256(text.encode()).hexdigest())
        object.__setattr__(self,'policy',policy)

    def to_dict(self):return json.loads(self._encoded)


@dataclass(frozen=True,init=False)
class ColdPhysicalObservation:
    _encoded: str
    FIELDS={'identity','qualification_sha256','operation_id','phase','source_sha256','evidence_id',
        'request_nonce','observed_at','expires_at','continuous_since','position','temperatures','motor_axes',
        'installed_artifact_verified','configuration_verified','sensors_verified','fault_free',
        'workspace_clear','part_undisturbed','transition_verified'}
    BOOLS={'installed_artifact_verified','configuration_verified','sensors_verified','fault_free',
        'workspace_clear','part_undisturbed','transition_verified'}

    def __init__(self,data):
        if type(data) is not dict or data.keys()!=self.FIELDS:raise ValueError('complete physical observation required')
        if (not sha(data['qualification_sha256']) or not sha(data['source_sha256'])
                or not label(data['operation_id']) or not label(data['evidence_id'])
                or type(data['request_nonce']) is not str or re.fullmatch('[0-9a-f]{32}',data['request_nonce']) is None
                or data['phase'] not in ('preflight','start','paused','resuming','started','resumed','completed','cancelled')):
            raise ValueError('bound physical observation required')
        if any(type(data[k]) is not bool for k in self.BOOLS):raise ValueError('explicit physical facts required')
        if (not all(finite(data[k]) for k in ('observed_at','expires_at','continuous_since'))
                or not 0<=data['continuous_since']<=data['observed_at']<data['expires_at']<=data['observed_at']+1):
            raise ValueError('bounded physical observation interval required')
        if (type(data['identity']) is not dict or data['identity'].keys()!= {'printer_id','generation','host_session','job_id','file_sha256'}
                or any(not label(v) for v in data['identity'].values()) or not sha(data['identity']['file_sha256'])):
            raise ValueError('physical job identity required')
        if (type(data['position']) is not dict or data['position'].keys()!={'x','y','z'}
                or not all(finite(v) for v in data['position'].values())
                or type(data['motor_axes']) is not dict or data['motor_axes'].keys()!={'x','y','z'}
                or any(type(v) is not bool for v in data['motor_axes'].values())):
            raise ValueError('explicit physical XYZ observations required')
        temps=data['temperatures']
        if type(temps) is not dict or temps.keys()!={'T','B'}:raise ValueError('cold physical temperatures required')
        for values in temps.values():
            if type(values) is not dict or values.keys()!={'actual','target'} or not all(finite(v) for v in values.values()):
                raise ValueError('explicit physical temperature values required')
        object.__setattr__(self,'_encoded',encoded(data))

    def to_dict(self):return json.loads(self._encoded)


class ColdAssignedEvidence:
    """Use real owned reports/cursors, plus explicitly trusted physical producers.

    Construct and bind from operator-side startup code. No tool can install a
    qualification, submit an observation or switch evidence providers.
    """
    kind='cold_sources_and_trusted_physical_observations'

    def __init__(self,qualification,*,observe):
        if type(qualification) is not ColdMachineQualification or not callable(observe):
            raise TypeError('qualified cold profile and physical producer required')
        self.qualification=qualification;self.observe=observe;self.backend=None;self.pid=os.getpid()
        self._qualification_id=qualification.id;self._start_admission=None;self._stage_admission=None;self._start_effect=None

    def bind(self,backend):
        if (type(backend) is not AssignedJobBackend or self.backend is not None or backend.evidence is not self
                or backend._pump_started or backend.state_reader is None or backend.cold_preflight is None
                or backend.completion_barrier is None or not backend.checkpoint_configuration
                or backend.cold_preflight.policy.id!=self.qualification.policy.id
                or backend.printer_id!=self.qualification.to_dict()['printer_id']):
            raise ValueError('one owned cold backend with compound checkpoints required')
        self.backend=backend

    def _check(self,identity,*,require_file=True):
        b=self.backend
        if (os.getpid()!=self.pid or b is None or b.evidence is not self or b._closed or b._fault
                or self.qualification.id!=self._qualification_id or identity!=b.identity() or require_file and not b._file_valid()):raise RequestRejected('cold_provider_identity_or_source_changed')
        b.machine_events.lease.check()
        snapshot=b.machine_events.snapshot()
        if not snapshot['source_usable'] or not snapshot['capturing'] or snapshot['fault'] or snapshot['dispatch_inhibit']:
            raise RequestRejected('cold_provider_source_unavailable')
        return b,self.qualification.to_dict()

    def _configuration(self,identity,configuration):
        b,q=self._check(identity)
        if (type(configuration) is not dict or configuration.get('identity')!=identity
                or configuration.get('capture_id')!=b.machine_events.capture_id
                or configuration.get('firmware_session')!=b.prompt.channel.session
                or configuration.get('reported_settings_sha256')!=q['reported_settings_sha256']):
            raise RequestRejected('cold_qualified_configuration_mismatch')

    def _state(self,identity,report):
        b,q=self._check(identity)
        if stable(b.state_reader.read(report['operation_id']))!=stable(report):raise RequestRejected('cold_controller_source_changed')
        s=report['state'];t=s['inactivity']
        if (s['state_schema_version']!=2 or t is None or t['stepper_interval_ms']!=0 or t['max_interval_ms']!=0
                or t['max_predicate'] or not s['firmware_running'] or s['waiting_for_user'] or s['waiting_for_heatup']
                or s['planner_blocks']!=0 or s['linear_unit_factor']!=1 or any(s['relative'].values())
                or s['tool']!=0 or s['leveling_active'] or any(s['workspace_offset'].values()) or any(s['home_offset'].values())
                or not all(s['homed'].values()) or not all(s['trusted'].values())
                or not all(s['enabled_logical'][a] for a in ('x','y','z'))
                or s['feedrate_percent']!=100 or s['flow_percent']!=100 or s['fan_pwm']!=0):
            raise RequestRejected('cold_controller_profile_or_timer_policy_unqualified')
        self._temperatures(s['temperatures'])
        if any(not low<=s['position'][a]<=high for a,(low,high) in self.qualification.policy.to_dict()['bounds'].items()):
            raise RequestRejected('cold_controller_position_outside_qualified_bounds')

    def _temperatures(self,temperatures):
        for heater,bounds in self.qualification.policy.to_dict()['temperature_bounds'].items():
            if temperatures[heater]['target']!=0 or not bounds[0]<=temperatures[heater]['actual']<=bounds[1]:
                raise RequestRejected('cold_physical_or_reported_temperature_unqualified')

    def _physical(self,identity,operation_id,phase,sources,*,since):
        b,q=self._check(identity,require_file=phase!='cancelled')
        source_sha=digest(sources);requested_at=b.clock();nonce=uuid.uuid4().hex
        request=dict(identity=identity,qualification_sha256=self.qualification.id,operation_id=operation_id,
            phase=phase,source_sha256=source_sha,sources=sources,continuous_since_required=since,
            request_nonce=nonce,requested_at=requested_at)
        observation=self.observe(json.loads(encoded(request)))
        if type(observation) is not ColdPhysicalObservation:raise RequestRejected('current_physical_observation_required')
        data=observation.to_dict();self._check(identity,require_file=phase!='cancelled');now=b.clock()
        if (not finite(now) or data['identity']!=identity or data['qualification_sha256']!=self.qualification.id
                or data['operation_id']!=operation_id or data['phase']!=phase or data['source_sha256']!=source_sha or data['request_nonce']!=nonce
                or not requested_at<=data['observed_at'] or data['observed_at']<since
                or not 0<=now-data['observed_at']<=1 or now>=data['expires_at'] or data['continuous_since']>since
                or not all(data[k] for k in ColdPhysicalObservation.BOOLS-{'transition_verified'})
                or phase in ('started','resumed','completed','cancelled') and not data['transition_verified']
                or not all(data['motor_axes'].values())):
            raise RequestRejected('cold_physical_facts_stale_missing_or_wrong_scope')
        self._temperatures(data['temperatures'])
        if any(not low<=data['position'][a]<=high for a,(low,high) in self.qualification.policy.to_dict()['bounds'].items()):
            raise RequestRejected('cold_physical_position_outside_qualified_bounds')
        report=sources.get('controller')
        if report is not None:
            if data['observed_at']<report['observed_at']:raise RequestRejected('physical_observation_predates_controller')
            if any(abs(data['position'][a]-report['state']['position'][a])>q['position_tolerance_mm'] for a in ('x','y','z')):
                raise RequestRejected('physical_position_disagrees_with_controller')
            self._state(identity,report)  # The callback cannot refresh source time.
        b.machine_events.journal.record_observation(dict(kind='cold_physical_observation_accepted',
            qualification_sha256=self.qualification.id,observation=data,physical_control_qualified=False),source='job.inspect',now=b.clock())
        self._physical_current(identity,data)
        if report is not None:self._state(identity,report)
        self._physical_current(identity,data)
        return data

    def _physical_current(self,identity,data):
        b,_=self._check(identity,require_file=data['phase']!='cancelled');now=b.clock()
        if (not finite(now) or not 0<=now-data['observed_at']<=1 or now>=data['expires_at']
                or data['identity']!=identity or data['qualification_sha256']!=self.qualification.id):
            raise RequestRejected('physical_observation_expired_or_rebound')

    def _cold_inputs(self,identity):
        b,q=self._check(identity)
        cached=b.cold_preflight._cached
        if cached is None:raise RequestRejected('fresh_cold_inspection_required')
        report=b.state_reader.read(cached[0]);self._state(identity,report)
        # Historical baseline is not refreshed: current physical configuration
        # verification remains required, and no settings commands are permitted.
        with b.machine_events.journal._db() as db:
            rows=db.execute("SELECT request,result FROM operations WHERE status='completed' ORDER BY rowid DESC LIMIT 128").fetchall()
        configuration=None
        for row in rows:
            request=json.loads(row['request'])
            if request['operation']=='printer.configuration.inspect':
                configuration=json.loads(row['result']);break
        if configuration is None:raise RequestRejected('owned_configuration_baseline_required')
        self._configuration(identity,configuration)
        return dict(controller=report,configuration=configuration)

    def preflight(self,data,identity):
        self._stage_admission=None
        b,q=self._check(identity)
        b.cold_preflight.check(data,identity)
        sources=self._cold_inputs(identity)
        physical=self._physical(identity,sources['controller']['operation_id'],'preflight',sources,since=sources['controller']['observed_at'])
        b.cold_preflight.check(data,identity)
        self._stage_admission=dict(identity=dict(identity),sources=json.loads(encoded(sources)),physical=physical)
        self._physical_current(identity,physical)
        return True

    def start_blockers(self,identity):
        self._start_admission=None
        try:
            sources=self._cold_inputs(identity)
            physical=self._physical(identity,sources['controller']['operation_id'],'start',sources,since=sources['controller']['observed_at'])
            self._start_admission=dict(identity=dict(identity),sources=json.loads(encoded(sources)),physical=physical,observed_at=physical['observed_at'])
            self._physical_current(identity,physical)
            return []
        except (RequestRejected,ValueError,KeyError):
            self._start_admission=None
            return ['cold_start_physical_or_source_evidence_unqualified']

    def before_effect(self,identity,operation_id,operation):
        b,q=self._check(identity)
        admission=self._stage_admission if operation=='job.stage' else self._start_admission if operation=='job.start' else None
        with b.machine_events.journal._db() as db:
            row=db.execute('SELECT request,status FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
        if admission is None or admission['identity']!=identity or row is None or row['status']!='running':
            raise RequestRejected('committed_current_cold_admission_required')
        request=json.loads(row['request'])
        if (request['operation']!=operation or request['printer_id']!=identity['printer_id']
                or request['connection_generation']!=identity['generation'] or request['arguments'].get('job_id')!=identity['job_id']):
            raise RequestRejected('cold_effect_admission_identity_mismatch')
        self._state(identity,admission['sources']['controller'])
        self._configuration(identity,admission['sources']['configuration'])
        self._physical_current(identity,admission['physical'])
        b.machine_events.journal.record_observation(dict(kind='cold_effect_admission',operation_id=operation_id,
            operation=operation,identity=identity,qualification_sha256=self.qualification.id,
            physical=admission['physical'],source_sha256=digest(admission['sources']),
            physical_control_qualified=False),source='job.inspect',now=b.clock())
        self._state(identity,admission['sources']['controller'])
        self._physical_current(identity,admission['physical'])
        if operation=='job.start':
            self._start_effect=dict(admission,operation_id=operation_id)
            self._start_admission=None
        else:self._stage_admission=None

    def checkpoint(self,identity):raise RequestRejected('compound_source_checkpoint_required')

    def checkpoint_from_sources(self,identity,report,configuration):
        b,q=self._check(identity);self._configuration(identity,configuration);self._state(identity,report)
        proof=b.checkpoint_inspection.read()
        if (proof is None or stable(proof['controller_state'])!=stable(report) or proof['configuration']!=configuration
                or proof['phase'] not in ('paused','resuming') or proof['identity']!=identity):
            raise RequestRejected('current_compound_checkpoint_required')
        saved=None if b.checkpoint is None else b.checkpoint.to_dict()
        since=proof['receipt']['observed_at'] if saved is None else saved['created_at']
        physical=self._physical(identity,proof['operation_id'],proof['phase'],dict(controller=report,configuration=configuration,drain=proof),since=since)
        if stable(b.checkpoint_inspection.read())!=stable(proof):raise RequestRejected('cold_checkpoint_changed_during_physical_observation')
        cursor=b.sender.getFilePosition();position=b.sender.pause_position.as_dict();s=report['state']
        if (type(position) is not dict or any(not finite(position.get(a)) or abs(position[a]-s['position'][a])>q['position_tolerance_mm'] for a in ('x','y','z','e'))):
            raise RequestRejected('sender_and_controller_checkpoint_frame_disagree')
        modes,blockers,_=b.witness.snapshot()
        if blockers:raise RequestRejected('transmitted_modes_unqualified')
        modal={k:modes[k] for k in ('units','xyz_relative','e_relative','feedrate','tool','fan_pwm','nozzle_target','bed_target')}
        if (modal['units']!='mm' or modal['xyz_relative'] or modal['e_relative'] or modal['tool']!=0
                or modal['fan_pwm']!=0 or modal['nozzle_target']!=0 or modal['bed_target']!=0
                or not finite(modal['feedrate']) or not math.isclose(modal['feedrate']/60,s['feedrate_mm_s'],rel_tol=2e-7,abs_tol=1e-6)):
            raise RequestRejected('transmitted_and_controller_modes_disagree')
        now=b.clock();expires=report['observed_at']+q['max_pause_seconds'] if saved is None else saved['expires_at']
        if now>=expires:raise RequestRejected('qualified_pause_interval_expired')
        self._physical_current(identity,physical)
        return PauseCheckpoint(dict(identity,schema_version=1,firmware_id=q['firmware_sha256'],config_sha256=q['configuration_sha256'],
            cursor=cursor['pos'],next_line_number=b.sender._current_line,created_at=report['observed_at'],expires_at=expires,
            position={a:position[a] for a in ('x','y','z','e')},modal=dict(modal,feedrate_percent=s['feedrate_percent'],flow_percent=s['flow_percent']),
            offsets=dict(x=0.,y=0.,z=0.,e=0.),leveling_active=False,
            evidence=dict(queue_drained=True,position_valid=True,motors_held=all(physical['motor_axes'].values()),
                fault_free=physical['fault_free'],sensors_valid=physical['sensors_verified'],part_undisturbed=physical['part_undisturbed'])))

    def _transition(self,identity,operation_id,phase):
        b,q=self._check(identity,require_file=phase!='cancelled')
        expected={'started':'job.start','resumed':'job.resume','completed':'job.start','cancelled':'job.cancel'}[phase]
        with b.machine_events.journal._db() as db:
            row=db.execute('SELECT request,status FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
        if (row is None or row['status']!=('completed' if phase=='completed' else 'running')
                or operation_id not in b._dispatched_at or b._operations.get(operation_id)!=expected):
            raise RequestRejected('committed_physical_transition_required')
        request=json.loads(row['request'])
        if (request['operation']!=expected or request['printer_id']!=identity['printer_id']
                or request['connection_generation']!=identity['generation'] or request['arguments'].get('job_id')!=identity['job_id']):
            raise RequestRejected('physical_transition_job_mismatch')
        if phase in ('completed','cancelled'):
            proof=b.completion_barrier.poll()
            if proof is None or proof['phase']!=phase or proof['operation_id']!=operation_id or proof['identity']!=identity:
                raise RequestRejected('current_transition_drain_required')
            sources=dict(drain=proof);since=proof['receipt']['observed_at']
        elif phase=='resumed':
            proof=b._resume_admission.get(operation_id)
            if proof is None:raise RequestRejected('current_resume_admission_required')
            sources=dict(admission=proof);since=proof['receipt']['observed_at']
        else:
            admission=self._start_effect
            if (admission is None or admission['identity']!=identity or admission['operation_id']!=operation_id
                    or not 0<=b._dispatched_at[operation_id]-admission['observed_at']<=1):
                raise RequestRejected('current_cold_start_admission_required')
            # The machine may now move. Retain admission as historical context,
            # never compare the current physical position with its old position.
            sources=dict(admission=admission);since=admission['observed_at']
        physical=self._physical(identity,operation_id,phase,sources,since=since)
        if phase in ('completed','cancelled') and b.completion_barrier.poll()!=proof:
            raise RequestRejected('transition_drain_changed_during_physical_observation')
        self._physical_current(identity,physical)
        return AssignedJobEvidence.from_observation(identity,operation_id,phase,physical['observed_at'],physical['evidence_id'],self.kind)

    def started(self,identity,operation_id):return self._transition(identity,operation_id,'started')
    def resumed(self,identity,operation_id):return self._transition(identity,operation_id,'resumed')
    def completed(self,identity,operation_id):return self._transition(identity,operation_id,'completed')
    def cancelled(self,identity,operation_id):return self._transition(identity,operation_id,'cancelled')
