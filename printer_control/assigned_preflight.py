"""Source-backed cold command screening; external hardware proofs stay required."""
import hashlib
import json
from .assigned_state import AssignedStateReader
from .cold_preflight import ColdJobPolicy,cold_job_plan
from .journal import RequestRejected


class AssignedColdPreflight:
    def __init__(self,reader,policy):
        if type(reader) is not AssignedStateReader or type(policy) is not ColdJobPolicy:
            raise TypeError('owned state reader and explicit cold policy required')
        self.reader,self.policy=reader,policy
        self._cached=None

    def _assess(self,data,identity,operation_id):
        if (type(identity) is not dict or identity.keys()!={'printer_id','generation','host_session','job_id','file_sha256'}
                or any(type(v) is not str or not 1<=len(v)<=128 for v in identity.values())
                or type(data) is not bytes or hashlib.sha256(data).hexdigest()!=identity['file_sha256']):
            raise RequestRejected('cold_job_identity_or_hash_mismatch')
        report=self.reader.read(operation_id)
        if report['printer_id']!=identity['printer_id'] or report['connection_generation']!=identity['generation']:
            raise RequestRejected('cold_source_identity_mismatch')
        state=report['state'];receipt,_=self.reader.recorder.endpoint.backend.channel.snapshot()
        if receipt is None or receipt.active or receipt.waiting or not state['firmware_running'] or state['waiting_for_user'] or state['waiting_for_heatup']:
            raise RequestRejected('cold_controller_not_idle')
        if (state['linear_unit_factor']!=1 or any(state['relative'].values()) or state['tool']!=0
                or state['leveling_active'] or state['planner_blocks']!=0
                or any(state['workspace_offset'].values()) or any(state['home_offset'].values())):
            raise RequestRejected('cold_controller_coordinate_state_unqualified')
        if (not all(state['homed'].values()) or not all(state['trusted'].values())
                or not all(state['enabled_logical'][a] for a in ('x','y','z'))):
            raise RequestRejected('cold_controller_axis_flags_unqualified')
        if state['feedrate_percent']!=100 or state['flow_percent']!=100 or state['fan_pwm']!=0:
            raise RequestRejected('cold_controller_override_or_fan_unqualified')
        for heater,limits in self.policy.to_dict()['temperature_bounds'].items():
            values=state['temperatures'][heater]
            if values['target']!=0 or not limits[0]<=values['actual']<=limits[1]:
                raise RequestRejected('cold_reported_temperature_unqualified')
        position={a:state['position'][a] for a in ('x','y','z')}
        plan=cold_job_plan(data,policy=self.policy,initial_position=position)
        final=self.reader.read(operation_id)  # Parsing cannot refresh the source clock.
        stable=('operation_id','printer_id','connection_generation','capture_id','observed_at','state')
        if any(final[k]!=report[k] for k in stable):raise RequestRejected('cold_state_changed_during_preflight')
        return dict(identity=dict(identity),inspect_operation_id=operation_id,source_observed_at=report['observed_at'],
            source_capture_id=report['capture_id'],source_session=state['session'],source_sequence=state['sequence'],
            plan=plan,scope='source_bound_cold_command_screening',software_checks_passed=True,
            physical_readiness_qualified=False,dispatch_authorized=False,
            remaining_qualification=['physical_board_processor_screen_and_firmware','configuration_identity_and_coordinate_frame',
                'physical_position_and_motor_hold','sensor_health','clearance_and_part_continuity','actual_completion_barrier'])

    def evaluate(self,data,identity,operation_id):
        self._cached=None
        result=self._assess(data,identity,operation_id)
        self._cached=(operation_id,json.dumps(identity,sort_keys=True,separators=(',',':')))
        return result

    def check(self,data,identity):
        if self._cached is None or self._cached[1]!=json.dumps(identity,sort_keys=True,separators=(',',':')):
            raise RequestRejected('current_cold_preflight_required')
        try:return self._assess(data,identity,self._cached[0])
        except BaseException:
            self._cached=None
            raise
