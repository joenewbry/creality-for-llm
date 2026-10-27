"""Journal adapter for an assigned sender with an explicit evidence provider.

No physical provider, deployment launcher or inferred mechanical qualification.
Sender state alone never proves completion, clearance or resume continuity.
"""
from dataclasses import dataclass
import json
import re
import hashlib
import math
from pathlib import Path
import queue
import os
import tempfile
import threading
import uuid
from .assigned_sender import AssignedSender
from .checkpoint import PauseCheckpoint,InvalidCheckpoint
from .journal import RequestRejected
from .octoprint_virtual import _CallbackTap
from .service import Context,Outcome
from .prompt_backend import PromptBackend


@dataclass(frozen=True)
class AssignedJobEvidence:
    identity_sha256: str
    operation_id: str
    phase: str
    observed_at: float
    source: str
    scope: str

    def __post_init__(self):
        if type(self.identity_sha256) is not str or re.fullmatch('[0-9a-f]{64}',self.identity_sha256) is None:
            raise ValueError('job-bound evidence identity required')
        for value in (self.operation_id,self.source,self.scope):
            if type(value) is not str or not value.strip() or len(value)>128:raise ValueError('bounded evidence reference required')
        if self.phase not in ('started','resumed','completed','cancelled'):raise ValueError('evidence phase required')
        if type(self.observed_at) not in (int,float) or not math.isfinite(self.observed_at):raise ValueError('finite evidence time required')

    @classmethod
    def from_observation(cls,identity,operation_id,phase,observed_at,source,scope):
        digest=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return cls(digest,operation_id,phase,observed_at,source,scope)


class AssignedJobBackend:
    supported_operations=('job.stage','job.start','job.pause','job.resume','job.cancel')+PromptBackend.supported_operations
    supported_reads=('printer.describe','printer.status','job.inspect','operation.get','printer.events','printer.observations')

    def __init__(self,sender,*,job_path,job_id,witness,evidence,clock):
        if type(sender) is not AssignedSender:raise TypeError('assigned sender required')
        if type(job_id) is not str or not job_id.strip() or len(job_id)>128:raise ValueError('explicit job identity required')
        if witness is None or not callable(clock):raise TypeError('stream witness and clock required')
        for name in ('preflight','start_blockers','checkpoint','started','resumed','completed','cancelled'):
            if not callable(getattr(evidence,name,None)):raise TypeError('explicit evidence provider required')
        self.sender,self.witness,self.evidence,self.clock=sender,witness,evidence,clock
        self.evidence_kind=getattr(evidence,'kind','external_unqualified')
        if type(self.evidence_kind) is not str or not 1<=len(self.evidence_kind)<=128:raise ValueError('bounded evidence scope required')
        self.prompt=sender._assigned_endpoint.backend
        self.generation=self.prompt.channel.generation
        self.printer_id=self.prompt.printer_id
        self.job_id=job_id;self.host_session=uuid.uuid4().hex
        self.source_path=Path(job_path).resolve(strict=True)
        data=self._read_bounded(self.source_path)
        if not 0<len(data)<=16*1024*1024:raise RequestRejected('job_size_out_of_range')
        self.sha256=hashlib.sha256(data).hexdigest()
        self.path=self.source_path
        self._staging=tempfile.TemporaryDirectory(prefix='assigned-staged-job-')
        self.machine_events=None
        self._owner_pid=os.getpid()
        self._pump_started=False;self._pump_thread=None;self._pump_stop=threading.Event()
        self._pump_error=None;self._pump_samples=0
        self._lock=threading.RLock();self._closed=False;self._fault=None
        self._phase='available';self._revision=0;self._signature=None
        self._staged=False;self._stage_revision=0;self._started=False;self._printing=0
        self._operations={};self._transitions={};self._deadlines={};self._dispatched_at={};self._start_operation_id=None;self.checkpoint=None;self._pause_epoch=None
        self._events=queue.SimpleQueue()
        self._callback=_CallbackTap(sender._callback,self._events)
        sender._callback=self._callback

    def start(self):
        """Start the pinned sender and passive prompt-copy consumer once.

        Requires source capture attached under the same lease. This does not
        open a port, establish readiness or supply external physical evidence.
        """
        if os.getpid()!=self._owner_pid:raise OSError('assigned lifecycle belongs to another process')
        from .assigned_observations import AssignedMachineEventRecorder
        with self._lock:
            if self._closed or self._pump_started:raise ValueError('assigned lifecycle is closed or already started')
            if (type(self.machine_events) is not AssignedMachineEventRecorder
                    or self.machine_events.endpoint is not self.sender._assigned_endpoint
                    or not self.machine_events.capturing):
                raise ValueError('attach active source capture before start')
            self.prompt.controller_lease.check()
            if self.sender._assigned_endpoint._claimed:raise ValueError('assigned sender already claimed')
            self._pump_started=True
            self._pump_thread=threading.Thread(target=self._pump_prompts,name='assigned-prompt-copy',daemon=False)
            try:
                self.sender.start()
                self._pump_thread.start()
            except BaseException:
                self._pump_stop.set();self._pump_error='assigned_start_interrupted'
                self._fault=self._fault or {'reason':self._pump_error}
                self.sender._assigned_endpoint.retire(self._pump_error)
                raise

    def _pump_prompts(self):
        while not self._pump_stop.wait(.005):
            # Service.close holds this same guard. Bounded acquisition lets the
            # consumer observe stop without deadlocking that close/join path.
            if not self._lock.acquire(timeout=.05):continue
            try:
                if self._pump_stop.is_set():return
                try:
                    if not self.machine_events.capturing:raise OSError('capture lost')
                    self.prompt.context()  # No external evidence-provider callback.
                    self._pump_samples+=1
                    if (self.prompt.channel.state in ('uncertain','disconnected')
                            or self.prompt.receive_hub.fault or self.prompt.frame_writer.fault):
                        raise OSError('prompt connection uncertain')
                except BaseException:
                    self._pump_error='assigned_prompt_pump_unavailable'
                    self._fault=self._fault or {'reason':self._pump_error}
                    self._phase='outcome_unknown';self.checkpoint=None;self.witness.invalidate_resume()
                    self.sender._assigned_endpoint.retire(self._pump_error)
                    return
            finally:self._lock.release()

    def attach_machine_events(self,recorder):
        from .assigned_observations import AssignedMachineEventRecorder
        with self._lock:
            if (type(recorder) is not AssignedMachineEventRecorder
                    or recorder.endpoint is not self.sender._assigned_endpoint):
                raise TypeError('capture must belong to this assigned endpoint')
            if self.machine_events is not None or self._closed or recorder.endpoint._claimed or not recorder.capturing:
                raise ValueError('attach one capture before sender starts')
            self.machine_events=recorder
            self.supported_reads=type(self).supported_reads+('printer.machine_events',)

    def guard(self):return self._lock

    def _drain(self):
        if self.machine_events is not None and not self.machine_events.capturing:
            self._fault=self._fault or {'reason':'assigned_receive_capture_lost'}
        while True:
            try:event,value=self._events.get_nowait()
            except queue.Empty:break
            if event=='fault':self._fault=self._fault or value
            elif event=='state':
                if value==self.sender.STATE_PRINTING:self._printing+=1;self._phase='running'
                elif value==self.sender.STATE_PAUSED:self._phase='paused'
            elif event=='completed':self._phase='completing'
            elif event=='cancelled':self._phase='cancelling'
            else:self._fault=self._fault or {'reason':'sender_job_failed'}
        if self.prompt.channel.state in ('uncertain','disconnected') or self.prompt.receive_hub.fault or self.prompt.frame_writer.fault:
            self._fault=self._fault or {'reason':'shared_connection_uncertain'}
        state=self.sender.getState()
        if state in (self.sender.STATE_ERROR,self.sender.STATE_CLOSED,self.sender.STATE_CLOSED_WITH_ERROR):
            self._fault=self._fault or {'reason':'sender_fault_or_disconnect'}
        if self._fault:
            self._phase='outcome_unknown';self.checkpoint=None;self.witness.invalidate_resume()
        elif self._phase=='completing' and state==self.sender.STATE_OPERATIONAL:
            # Provider must independently establish the final barrier. The EOF
            # callback and Operational state are not movement completion evidence.
            try:proven=self.evidence.completed(self.identity(),self._start_operation_id)
            except Exception:
                self._fault={'reason':'completion_evidence_unavailable'};self._phase='outcome_unknown'
                self.checkpoint=None;self.witness.invalidate_resume()
            else:
                if self._evidence_valid(proven,'completed',self._start_operation_id):self._phase='completed'

    def identity(self):
        return dict(printer_id=self.printer_id,generation=self.generation,host_session=self.host_session,
                    job_id=self.job_id,file_sha256=self.sha256)

    def _evidence_valid(self,proof,phase,operation_id,*,deadline=None):
        if type(proof) is not AssignedJobEvidence:return False
        expected=hashlib.sha256(json.dumps(self.identity(),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return (proof.identity_sha256==expected and proof.operation_id==operation_id and proof.phase==phase
                and proof.scope==self.evidence_kind and operation_id in self._dispatched_at
                and self._dispatched_at[operation_id]<=proof.observed_at<=self.clock()
                and (deadline is None or proof.observed_at<deadline))

    def context(self):
        self.prompt.context();self._drain()
        epoch=self.witness.snapshot()[2] if self.sender.getState()==self.sender.STATE_PAUSED else None
        signature=(self.sender.getState(),self._phase,self._stage_revision,self._started,epoch,bool(self._fault),self.prompt._version)
        if signature!=self._signature:self._revision+=1;self._signature=signature
        return Context(self.printer_id,self.generation,self._revision,self.clock())

    @staticmethod
    def _read_bounded(path):
        with Path(path).open('rb') as source:data=source.read(16*1024*1024+1)
        if not 0<len(data)<=16*1024*1024:raise RequestRejected('job_size_out_of_range')
        return data

    def can_overlap(self,operation,pending_ids):
        return operation in self.prompt.supported_operations and bool(pending_ids) and all(
            self._operations.get(key) in ('job.start','job.pause','job.resume') for key in pending_ids)

    def _file_valid(self):
        try:return hashlib.sha256(self._read_bounded(self.path)).hexdigest()==self.sha256
        except (OSError,RequestRejected):return False

    def validate(self,operation,arguments):
        self._drain()
        if self._closed:raise RequestRejected('backend_closed')
        if operation not in self.supported_operations:raise RequestRejected('unsupported_operation')
        if operation in self.prompt.supported_operations:
            if self._fault and operation!='prompt.inspect':raise RequestRejected('job_evidence_requires_reconciliation')
            self.prompt.validate(operation,arguments)
            return
        expected={'job_id':self.job_id}
        if operation=='job.stage':expected['file_sha256']=self.sha256
        if arguments!=expected:raise RequestRejected('wrong_job_or_unknown_arguments')
        if self._fault:
            cursor=self.sender.getFilePosition()
            if operation=='job.cancel' and self._started and cursor and cursor.get('origin')=='local' and Path(cursor.get('filename','')).resolve()==self.path:return
            raise RequestRejected('shared_connection_requires_reconciliation')
        state=self.sender.getState()
        if operation=='job.stage':
            if self._started or state!=self.sender.STATE_OPERATIONAL:raise RequestRejected('printer_not_idle_for_stage')
            data=self._read_bounded(self.source_path)
            if len(data)>16*1024*1024 or hashlib.sha256(data).hexdigest()!=self.sha256:raise RequestRejected('source_file_changed')
            if self.evidence.preflight(data,self.identity()) is not True:raise RequestRejected('job_preflight_unqualified')
            return
        if not self._staged:raise RequestRejected('job_not_staged')
        cursor=self.sender.getFilePosition()
        if not cursor or cursor.get('origin')!='local' or Path(cursor.get('filename','')).resolve()!=self.path:
            raise RequestRejected('sender_job_changed')
        if operation=='job.cancel':
            if not self._started or state not in (self.sender.STATE_PRINTING,self.sender.STATE_PAUSED):raise RequestRejected('job_not_running_or_paused')
            return  # An owned cancellation must not depend on a valid job file.
        if not self._file_valid():raise RequestRejected('staged_job_missing_or_changed')
        if operation=='job.start':
            if self._started or state!=self.sender.STATE_OPERATIONAL:raise RequestRejected('job_not_ready_to_start')
            blockers=self.evidence.start_blockers(self.identity())
            if type(blockers) is not list or len(blockers)>32 or any(type(x) is not str or not 1<=len(x)<=128 for x in blockers):raise RequestRejected('invalid_start_evidence')
            if blockers:raise RequestRejected(','.join(blockers))
        elif operation=='job.pause':
            if state!=self.sender.STATE_PRINTING:raise RequestRejected('job_not_running')
        elif operation=='job.resume':
            if state!=self.sender.STATE_PAUSED:raise RequestRejected('job_not_paused')
            blockers=self.resume_blockers()
            if blockers:raise RequestRejected(','.join(blockers))
        elif state not in (self.sender.STATE_PRINTING,self.sender.STATE_PAUSED):
            raise RequestRejected('job_not_running_or_paused')

    def dispatch(self,operation_id,operation,arguments,*,deadline):
        self._operations[operation_id]=operation;self._deadlines[operation_id]=deadline;self._dispatched_at[operation_id]=self.clock()
        if operation in self.prompt.supported_operations:
            self.prompt.dispatch(operation_id,operation,arguments,deadline=deadline)
            return
        self._transitions[operation_id]=self._printing
        if operation=='job.stage':
            data=self._read_bounded(self.source_path)
            if not 0<len(data)<=16*1024*1024 or hashlib.sha256(data).hexdigest()!=self.sha256:raise RequestRejected('source_changed_before_stage')
            if self.evidence.preflight(data,self.identity()) is not True:raise RequestRejected('job_preflight_unqualified')
            destination=(Path(self._staging.name)/(uuid.uuid4().hex+'.gcode')).resolve()
            with destination.open('xb') as output:output.write(data)
            destination.chmod(0o400)
            self.sender.selectFile(str(destination),False)
            self.path=destination;self._staged=True;self._stage_revision+=1;self.checkpoint=None;self._phase='staged'
        elif operation=='job.start':
            self.witness.start_job();self._started=True;self._start_operation_id=operation_id;self._phase='starting';self.sender.startPrint()
        elif operation=='job.pause':self._phase='pausing';self.sender.setPause(True)
        elif operation=='job.resume':
            if not self.witness.arm_resume(self._pause_epoch):raise RequestRejected('stream_changed_before_resume')
            self.checkpoint=None;self._phase='resuming';self.sender.setPause(False)
        elif operation=='job.cancel':
            self.checkpoint=None;self._phase='cancelling';self.witness.invalidate_resume();self.sender.cancelPrint()
        else:raise RequestRejected('unsupported_operation')

    def _capture(self):
        try:checkpoint=self.evidence.checkpoint(self.identity())
        except (RequestRejected,InvalidCheckpoint):raise
        except Exception:raise RequestRejected('checkpoint_evidence_unavailable') from None
        if not isinstance(checkpoint,PauseCheckpoint):raise RequestRejected('qualified_checkpoint_unavailable')
        saved=checkpoint.to_dict();now=self.clock()
        for key,value in self.identity().items():
            if saved[key]!=value:raise RequestRejected('checkpoint_identity_mismatch')
        if not 0<=now-saved['created_at']<=1 or now>=saved['expires_at']:raise RequestRejected('checkpoint_stale')
        cursor=self.sender.getFilePosition()
        if not cursor or saved['cursor']!=cursor['pos'] or saved['next_line_number']!=self.sender._current_line:
            raise RequestRejected('checkpoint_cursor_mismatch')
        reported=self.sender.pause_position.as_dict()
        if any(type(reported.get(k)) not in (int,float) or not math.isfinite(reported[k]) or reported[k]!=v for k,v in saved['position'].items()):
            raise RequestRejected('checkpoint_position_unconfirmed')
        modes,blockers,_=self.witness.snapshot()
        if blockers or any(saved['modal'][k]!=modes[k] for k in ('units','xyz_relative','e_relative','feedrate','tool','fan_pwm','nozzle_target','bed_target')):
            raise RequestRejected('checkpoint_transmitted_modes_mismatch')
        if not all(saved['evidence'].values()):raise RequestRejected('checkpoint_proofs_incomplete')
        return checkpoint

    def resume_blockers(self):
        if self._fault:return ['shared_connection_requires_reconciliation']
        if self.checkpoint is None:return ['qualified_checkpoint_unavailable']
        if not self._file_valid():return ['staged_file_changed']
        if self.witness.snapshot()[2]!=self._pause_epoch:return ['commands_since_checkpoint']
        try:current=self._capture()
        except (RequestRejected,InvalidCheckpoint):return ['current_checkpoint_unqualified']
        return self.checkpoint.in_place_resume_blockers(current,now=self.clock(),max_observation_age=1)

    def poll(self,operation_id):
        self._drain();operation=self._operations[operation_id]
        if operation in self.prompt.supported_operations:return self.prompt.poll(operation_id)
        state=self.sender.getState()
        if self.clock()>=self._deadlines[operation_id]:
            self._fault=self._fault or {'reason':'operation_deadline_expired'}
            self._phase='outcome_unknown';self.checkpoint=None;self.witness.invalidate_resume()
            return Outcome('outcome_unknown',{'reason':'operation_deadline_expired','physical_stop_confirmed':False})
        if self._fault:return Outcome('outcome_unknown',{'reason':'shared_connection_uncertain'})
        if operation=='job.stage':
            cursor=self.sender.getFilePosition()
            if cursor and Path(cursor.get('filename','')).resolve()==self.path and self._file_valid():
                return Outcome('completed',{'evidence_type':'assigned_file_selected','job_id':self.job_id,'file_sha256':self.sha256})
        if operation in ('job.start','job.resume') and self._printing>self._transitions[operation_id]:
            phase='started' if operation=='job.start' else 'resumed'
            try:proof=getattr(self.evidence,phase)(self.identity(),operation_id)
            except Exception:
                self._fault={'reason':'transition_evidence_unavailable'};self._phase='outcome_unknown'
                self.checkpoint=None;self.witness.invalidate_resume()
                return Outcome('outcome_unknown',{'reason':'transition_evidence_unavailable'})
            if self._evidence_valid(proof,phase,operation_id,deadline=self._deadlines[operation_id]):
                return Outcome('completed',{'evidence_type':'assigned_transition_with_external_evidence','job_id':self.job_id,
                    'evidence_source':proof.source,'evidence_scope':proof.scope,'evidence_observed_at':proof.observed_at,
                    'physical_control_qualified':False})
        if operation=='job.pause' and state==self.sender.STATE_PAUSED:
            self.checkpoint=None
            result={'evidence_type':'assigned_sender_pause_state','job_id':self.job_id,'resumable':False,'evidence_scope':self.evidence_kind,'physical_control_qualified':False}
            try:
                epoch=self.witness.snapshot()[2];checkpoint=self._capture()
                if not self.witness.freeze(epoch):raise RequestRejected('commands_during_checkpoint')
                self.checkpoint=checkpoint;self._pause_epoch=epoch
                result.update(checkpoint.envelope());result['resumable']=True
            except (RequestRejected,InvalidCheckpoint) as exc:
                result['resume_blockers']=[str(exc)];result['reason']='pause_evidence_unqualified'
                self._fault={'reason':'pause_evidence_unqualified'};self._phase='outcome_unknown';self.witness.invalidate_resume()
                return Outcome('outcome_unknown',result)
            return Outcome('completed',result)
        if operation=='job.cancel' and state==self.sender.STATE_OPERATIONAL:
            try:proven=self.evidence.cancelled(self.identity(),operation_id)
            except Exception:
                self._fault={'reason':'cancellation_evidence_unavailable'};self._phase='outcome_unknown';self.witness.invalidate_resume()
                return Outcome('outcome_unknown',{'reason':'cancellation_evidence_unavailable'})
            if self._evidence_valid(proven,'cancelled',operation_id,deadline=self._deadlines[operation_id]):
                self._phase='cancelled'
                return Outcome('completed',{'evidence_type':'assigned_cancel_with_external_completion_evidence','job_id':self.job_id,
                    'evidence_source':proven.source,'evidence_scope':proven.scope,'evidence_observed_at':proven.observed_at,'physical_control_qualified':False})
        if state==self.sender.STATE_OPERATIONAL and operation in ('job.start','job.pause','job.resume'):
            return Outcome('outcome_unknown',{'reason':'job_ended_before_transition_observed'})
        return None

    def inspect(self):
        context=self.context();paused=self.sender.getState()==self.sender.STATE_PAUSED
        blockers=self.resume_blockers() if paused else ['job_not_paused']
        return dict(self.prompt.inspect(),printer_id=self.printer_id,connection_generation=context.generation,state_version=context.state_version,
                    observed_at=context.observed_at,evidence_type='assigned_sender_with_external_qualification',
                    job_id=self.job_id,file_sha256=self.sha256,job_state=self._phase,staged=self._staged,
                    sender_state=self.sender.getStateString(),evidence_scope=self.evidence_kind,
                    resumable=paused and not blockers,resume_blockers=blockers,blocking_fault=self._fault,
                    physical_control_qualified=False,external_evidence_provider_required=True,
                    prompt_pump=dict(started=self._pump_started,running=bool(self._pump_thread and self._pump_thread.is_alive()),
                        samples=self._pump_samples,error=self._pump_error),
                    serial_reports=self.machine_events.snapshot() if self.machine_events is not None else None)

    def capabilities(self):
        return dict(scope='experimental_assigned_job_adapter',physical_control_qualified=False,
                    external_evidence_provider_required=True,evidence_scope=self.evidence_kind,
                    coordinated_prompt_pump_supported=True,
                    automatic_resend_supported=False,timeout_ack_credit_supported=False,
                    transport_recovery='reconciliation_required_on_resend_or_timeout',unsupported=['physical_qualification','raw_gcode','reconnect','physical_setup'])

    def close(self):
        if os.getpid()!=self._owner_pid:raise OSError('assigned lifecycle belongs to another process')
        if not self._closed:
            self._pump_stop.set()
            if self._pump_thread is not None and self._pump_thread.ident is not None:
                if self._pump_thread is threading.current_thread():raise RuntimeError('prompt consumer cannot close itself')
                self._pump_thread.join(2)
                if self._pump_thread.is_alive():raise TimeoutError('prompt consumer still running; ownership retained')
            self.sender.close()
            if self.machine_events is not None:self.machine_events.close()
            if self.sender._callback is self._callback:self.sender._callback=self._callback.original
            self.prompt.close()
            self._staging.cleanup();self._closed=True
