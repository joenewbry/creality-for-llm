"""Fixed software-only hot job fixture; not a general hardware profile."""
import json
import uuid
import hashlib
import time

from printer_control.checkpoint import PauseCheckpoint
from printer_control.journal import RequestRejected
from printer_control.service import Outcome
from printer_control.virtual_calibration import VirtualCalibration
from printer_control.tools import ToolRouter
from printer_control.octoprint_virtual import OctoPrintVirtualBackend
from marlin_modal_virtual import MarlinModalMixin, f32

HOT_JOB = ('G21\nG90\nM83\nT0\nM106 S128\nM104 S200\nM140 S60\n'
           'G92 X0 Y0 Z0 E0\nM220 S100\nM221 T0 S100\nG1 F6000\n' +
           ''.join(f'G1 X{i} Y10 E1 F6000\nM117 HOT_{i:03d}\n' for i in range(1,81)) +
           'M104 S0\nM140 S0\nM106 S0\n')


class HotVirtualBackend(OctoPrintVirtualBackend):
    supported_operations=OctoPrintVirtualBackend.supported_operations+('printer.stop','printer.set_fan','printer.set_temperature')+VirtualCalibration.OPERATIONS
    supported_reads=ToolRouter.READS|{'calibration.inspect'}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        if not isinstance(self.sender._serial,MarlinModalMixin):
            raise RequestRejected('extended_modal_simulator_required')
        self.restore_markers = {}
        self.thermal_policy = None
        self.cancellations = {}
        self.shutdown_markers = set()
        self.stop_deadlines = {}
        self.stop_requested = False
        self.calibration=VirtualCalibration(self)

    def validate(self,operation,arguments):
        if self.stop_requested:raise RequestRejected('stop_requires_recovery')
        if operation=='printer.stop':
            device=self.sender._serial
            if arguments or self._closed or not getattr(device,'_supportM112',False):
                raise RequestRejected('virtual_stop_unavailable')
            if device.shared_frame_writer.fault:raise RequestRejected('virtual_stop_writer_failed')
            return
        if self.setup.uncertain and operation!='job.cancel':raise RequestRejected('setup_outcome_requires_recovery')
        if operation!='job.cancel':self.calibration.check_integrity()
        if operation in VirtualCalibration.OPERATIONS:
            self.calibration.validate(operation,arguments)
            return
        if operation == 'job.stage':
            if self._closed or self._fault or self.sender.getState()!=self.sender.STATE_OPERATIONAL:
                raise RequestRejected('hot_fixture_not_idle')
            if arguments != {'job_id':self.job_id,'file_sha256':self.sha256}:
                raise RequestRejected('wrong_hot_fixture_job')
            content=self.source_path.read_bytes()
            if content != HOT_JOB.encode() or hashlib.sha256(content).hexdigest()!=self.sha256:
                raise RequestRejected('only_fixed_hot_simulation_job_supported')
            return
        super().validate(operation,arguments)

    def observe_transport_line(self,line):
        super().observe_transport_line(line)
        if hasattr(self,'calibration'):self.calibration.observe(line)
        if line.startswith('echo:LLM_OFF_') and not getattr(self,'drop_shutdown_marker',False):
            self.shutdown_markers.add(line.strip()[5:])
        if line.startswith('echo:LLM_RS_'):
            self.restore_markers[line.strip()[5:]]=self.clock()

    def _capture(self):
        device=self.sender._serial
        if self.sender.getState()!=self.sender.STATE_PAUSED or self._fault or device._killed:
            raise RequestRejected('hot_fixture_not_paused')
        modes,blockers,_=self.witness.snapshot()
        if blockers or modes['units']!='mm' or modes['tool']!=0:
            raise RequestRejected('hot_fixture_modes_unqualified')
        with device.buffered.all_tasks_done:
            if device.buffered.unfinished_tasks:
                raise RequestRejected('hot_fixture_moves_not_drained')
        observed=dict(units='mm',tool=device.currentExtruder,xyz_relative=device._relative,
            e_relative=device.e_relative,fan_pwm=device._fan_pwm,nozzle_target=device.targetTemp[0],
            bed_target=device.bedTargetTemp,feedrate=device._lastF,
            feedrate_percent=device._feedrate_multiplier,flow_percent=device._flowrate_multiplier)
        for key in ('xyz_relative','e_relative','fan_pwm','nozzle_target','bed_target','feedrate_percent','flow_percent'):
            if observed[key]!=modes[key]:raise RequestRejected('hot_fixture_modal_mismatch:'+key)
        if device._executed_feedrate_mm_s!=f32(f32(modes['feedrate'])/60):
            raise RequestRejected('hot_fixture_feedrate_mismatch')
        position=dict(x=device._lastX,y=device._lastY,z=device._lastZ,e=device._lastE[0])
        reported=self.sender.pause_position.as_dict()
        if any(reported[k]!=v for k,v in position.items()):
            raise RequestRejected('position_changed_since_pause')
        cursor=self.sender.getFilePosition()
        now=self.clock()
        return PauseCheckpoint(dict(schema_version=1,printer_id='virtual-development',generation=self.generation,
            host_session=self.host_session,job_id=self.job_id,file_sha256=self.sha256,
            firmware_id='OctoPrint-1.11.8-with-MarlinModalMixin',
            config_sha256=hashlib.sha256(b'fixed-hot-simulation-v1'+
                json.dumps(self.calibration.current(),sort_keys=True).encode()).hexdigest(),
            cursor=cursor['pos'],next_line_number=self.sender._current_line,created_at=now,expires_at=now+(120 if getattr(self,'model_fixture',False) else 30),
            position=position,modal=observed,offsets=dict(x=0,y=0,z=0,e=0),leveling_active=False,
            evidence={k:True for k in PauseCheckpoint.PROOFS}))

    def resume_blockers(self):
        return ['hot_fixture_requires_thermal_and_restoration_handoff']

    def capabilities(self):
        return dict(profile='fixed-hot-simulation-v1',physical_printer=False,
            scripted_temperatures=True,virtual_continuity_proofs=True,
            unsupported=['physical_control','arbitrary_job_files','parking','power_loss_recovery'])

    def pause_policy_status(self):
        return dict(kind='accelerated_hot_simulation',physical_pause_qualified=False,
            thermal_state=self.thermal_policy.state if self.thermal_policy else None,
            motor_hold_evidence='virtual_fixture_lease',
            expires_at=self.checkpoint.to_dict()['expires_at'] if self.checkpoint else None)

    def inspect(self):
        moves=list(self.sender._serial.completed_moves) if self.sender._serial else []
        expected=[line for line in HOT_JOB.splitlines() if line.startswith('G1 X')]
        actual=[line for line in moves if line.startswith('G1 X')]
        state=super().inspect()
        if self.stop_requested:
            state.update(job_state='stopped' if self.stop_confirmed() else 'stopping',
                resumable=False,resume_blockers=['stop_requires_recovery'])
        return {**state,**self.capabilities(),
            'simulation_evidence':{'job_moves':len(actual),'expected_job_moves':len(expected),
                'all_job_moves_once_in_order':actual==expected,'model_moves':len(moves),
                'physical_serial_open_attempts':len(self.physical_serial_attempts) if hasattr(self,'physical_serial_attempts') else None}}

    def thermal_observation(self,at,effect_id=None):
        policy=self.thermal_policy
        device=self.sender._serial
        return {**{k:policy.saved[k] for k in policy.IDENTITY},'checkpoint_id':policy.checkpoint_id,
            'sample_at':at,**{k:True for k in policy.PROOFS},'motor_hold_until':policy.start+(130 if getattr(self,'model_fixture',False) else 30),
            'nozzle_actual':device.temp[0],'bed_actual':device.bedTemp,
            'nozzle_target':device.targetTemp[0],'bed_target':device.bedTargetTemp,'effect_id':effect_id}

    def restored_resume_evidence(self,plan):
        if self.checkpoint is None or self.checkpoint.id!=plan.saved.id:
            raise RequestRejected('hot_fixture_checkpoint_changed')
        if self._pause_interval_invalid or hashlib.sha256(self.path.read_bytes()).hexdigest()!=self.sha256:
            raise RequestRejected('hot_fixture_interval_or_file_changed')
        cursor=self.sender.getFilePosition()
        if not cursor or cursor.get('filename')!=str(self.path) or cursor.get('origin')!='local':
            raise RequestRejected('hot_fixture_sender_job_changed')
        current=self._capture()
        at=time.monotonic()
        return dict(checkpoint=current,thermal=self.thermal_observation(at),monotonic_now=at)

    def dispatch_restored_resume(self,operation_id,plan,*,expected_epoch,deadline):
        if self.checkpoint is None or self.checkpoint.id!=plan.saved.id:
            raise RequestRejected('hot_fixture_checkpoint_changed')
        self._pause_epoch=expected_epoch
        super().dispatch(operation_id,'job.resume',{'job_id':self.job_id},deadline=deadline)

    def retire_restored_pause(self,checkpoint_id):
        if self.thermal_policy is None or self.thermal_policy.checkpoint_id!=checkpoint_id:
            raise RequestRejected('retired_pause_checkpoint_mismatch')
        transport=getattr(self,'thermal_transport',None)
        if transport is not None:
            transport.retire()

    def dispatch(self,operation_id,operation,arguments,*,deadline):
        if operation in VirtualCalibration.OPERATIONS:
            self.calibration.dispatch(operation_id,operation,arguments,deadline)
            return
        if operation=='printer.stop':
            self.stop_requested=True
            self.stop_deadlines[operation_id]=deadline
            self.checkpoint=None
            self.witness.invalidate_resume()
            self.sender._serial.shared_frame_writer.write_job(b'M112\n')
            return
        if operation!='job.cancel':
            return super().dispatch(operation_id,operation,arguments,deadline=deadline)
        token=uuid.uuid4().hex
        self.cancellations[operation_id]={'marker':'LLM_OFF_'+token,'deadline':deadline}
        self.witness.invalidate_resume()
        super().dispatch(operation_id,operation,arguments,deadline=deadline)
        tag,commands=self.witness.authorize_shutdown(token)
        for command in commands:
            if not self.sender.sendCommand(command,tags={tag,'source:hot-cancel'}):
                raise OSError('shutdown_command_not_queued')

    def stop_confirmed(self):
        device=self.sender._serial
        if not self.stop_requested or not getattr(device,'_killed',False):return False
        with device.buffered.all_tasks_done:
            return (device.buffered.unfinished_tasks==0 and not any(device.targetTemp)
                and not device.bedTargetTemp and not device.chamberTargetTemp)

    def poll(self,operation_id):
        if operation_id in self.stop_deadlines:
            if self.stop_confirmed():
                return Outcome('completed',{'evidence_type':'virtual_killed_and_drained_zero_targets',
                    'physical_stop_qualified':False,'requires_recovery':True,'resume_allowed':False})
            if self.clock()>=self.stop_deadlines[operation_id]:
                return Outcome('outcome_unknown',{'reason':'hot_virtual_stop_unconfirmed','requires_recovery':True})
            return None
        if self.stop_requested:return Outcome('outcome_unknown',{'reason':'interrupted_by_stop'})
        if operation_id in self.calibration.pending:return self.calibration.poll(operation_id)
        cancel=self.cancellations.get(operation_id)
        if cancel is None:return super().poll(operation_id)
        device=self.sender._serial
        if device is not None:
            with device.buffered.all_tasks_done:
                drained=device.buffered.unfinished_tasks==0
            if (cancel['marker'] in self.shutdown_markers and drained and
                    self.sender.getState()==self.sender.STATE_OPERATIONAL and
                    not any(device.targetTemp) and device.bedTargetTemp==0 and device.chamberTargetTemp==0):
                return Outcome('completed',{'evidence_type':'virtual_cancel_barrier_and_zero_targets',
                    'heater_targets_zero':True,'moves_drained':True,'physical_qualified':False})
        if self.clock()>=cancel['deadline']:
            return Outcome('outcome_unknown',{'reason':'hot_cancel_shutdown_unconfirmed',
                'heater_shutdown_confirmed':False,'requires_recovery':True})
        return None

    def validate_managed_resume(self,arguments):
        self.calibration.check_integrity()
        if self.stop_requested:raise RequestRejected('stop_requires_recovery')
        if arguments!={'job_id':self.job_id} or self._closed or self._fault:
            raise RequestRejected('invalid_managed_resume_request')
        if self.checkpoint is None or self.thermal_policy is None or self.checkpoint.id!=self.thermal_policy.checkpoint_id:
            raise RequestRejected('managed_resume_checkpoint_missing')
        if self.thermal_policy.state not in ('short_pause','standby'):
            raise RequestRejected('managed_resume_thermal_state_invalid')
        if hashlib.sha256(self.path.read_bytes()).hexdigest()!=self.sha256:
            raise RequestRejected('managed_resume_file_changed')
        self._capture()

    def make_resume_workflow(self,service,operation_id,deadline):
        from printer_control.resume_workflow import ResumeWorkflow
        return ResumeWorkflow(service,operation_id,deadline)

    def restoration_checkpoint(self):
        return self._capture()

    def send_restore_plan(self,plan,expected_epoch):
        tag=self.witness.authorize_restore(plan,expected_epoch,now=self.clock())
        for command in plan.commands+('M400','M118 E1 '+plan.marker):
            if not self.sender.sendCommand(command,tags={tag,'source:managed-resume'}):
                raise OSError('restoration command not queued')

    def restore_marker_time(self,plan):
        return self.restore_markers.get(plan.marker)
