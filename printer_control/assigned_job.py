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


class _AssignedJobCallbacks(_CallbackTap):
    """Stamp EOF at callback arrival; never wait on controller/journal locks."""
    def __init__(self,original,events,clock):
        super().__init__(original,events);self.clock=clock

    def on_comm_print_job_done(self,*args,**kwargs):
        try:observed_at=self.clock()
        except Exception:observed_at=None
        self.events.put(('completed',observed_at))
        return self.original.on_comm_print_job_done(*args,**kwargs)


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
        self.machine_events=None;self.state_reader=None;self.cold_preflight=None;self._cold_stage_screening=None
        self.cold_inspection=None;self._cold_admitting=False;self._cold_results={}
        self.completion_barrier=None;self._barrier_tokens={}
        self._resume_dispatched=set();self._resume_admission={}
        self._completion_window_seconds=30.;self._completion_window=None;self._completion_result=None
        self._owner_pid=os.getpid()
        self._pump_started=False;self._pump_thread=None;self._pump_stop=threading.Event()
        self._pump_error=None;self._pump_samples=0
        self._lock=threading.RLock();self._closed=False;self._fault=None
        self._phase='available';self._revision=0;self._signature=None
        self._staged=False;self._stage_revision=0;self._started=False;self._printing=0
        self._operations={};self._transitions={};self._deadlines={};self._dispatched_at={};self._start_operation_id=None;self.checkpoint=None;self._pause_epoch=None
        self._events=queue.SimpleQueue()
        self._callback=_AssignedJobCallbacks(sender._callback,self._events,self.clock)
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
                    self.prompt.context()  # Drain prompt replies before optional cold admission.
                    self._pump_samples+=1
                    if self.cold_inspection is not None:
                        for operation_id in tuple(self.cold_inspection.pending):
                            outcome=self.poll(operation_id)
                            if outcome is not None:self._cold_results[operation_id]=outcome
                    if (self.prompt.channel.state in ('uncertain','disconnected')
                            or self.prompt.receive_hub.fault or self.prompt.frame_writer.fault):
                        raise OSError('prompt connection uncertain')
                except BaseException:
                    # Either validated receive copy may observe a hazard first.
                    # Preserve its cause rather than winning the sticky fence
                    # with a generic consumer error.
                    self._pump_error=self.prompt.receive_hub.fault or {
                        'sensor_suspect':'assigned_serial_sensor_suspect',
                        'firmware_fault':'assigned_serial_fault',
                    }.get(getattr(self.prompt.channel,'invalidation_reason',None),'assigned_prompt_pump_unavailable')
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
            from .assigned_state import AssignedStateReader
            reader=AssignedStateReader(recorder)
            self.machine_events=recorder;self.state_reader=reader
            self.supported_reads=type(self).supported_reads+('printer.machine_events','printer.machine_state')

    def attach_cold_preflight(self,policy,*,automatic_inspection=False):
        """Add command screening before start; never grants hardware readiness."""
        from .assigned_preflight import AssignedColdPreflight
        if type(automatic_inspection) is not bool:raise TypeError("boolean inspection mode required")
        with self._lock:
            if (self.state_reader is None or self._closed or self._pump_started
                    or self.sender._assigned_endpoint._claimed or self.cold_preflight is not None):
                raise ValueError('attach one cold preflight after capture and before start')
            self.cold_preflight=AssignedColdPreflight(self.state_reader,policy)
            self.supported_reads=(*self.supported_reads,'job.cold_preflight')
            if automatic_inspection:
                from .assigned_inspection import AssignedColdInspection
                self.cold_inspection=AssignedColdInspection(self)

    def inspect_cold_preflight(self,job_id,operation_id):
        if self.cold_preflight is None or job_id!=self.job_id or self._closed or self._started:
            raise RequestRejected('cold_preflight_job_unavailable')
        data=self._read_bounded(self.path if self._staged else self.source_path)
        return self.cold_preflight.evaluate(data,self.identity(),operation_id)

    def _check_cold_preflight(self,data):
        if self.cold_inspection is not None and not self._cold_admitting:return None
        if self.cold_preflight is not None:return self.cold_preflight.check(data,self.identity())

    def attach_completion_barrier(self,*,completion_window_seconds=30.):
        """Opt in before start; keep external physical checkpoint proofs required."""
        from .assigned_barrier import AssignedCompletionBarrier,AssignedCompletionWindow
        AssignedCompletionWindow(0.,completion_window_seconds)
        with self._lock:
            if (self.machine_events is None or self._closed or self._pump_started
                    or self.sender._assigned_endpoint._claimed or self.completion_barrier is not None):
                raise ValueError('attach one completion barrier after capture and before sender start')
            self.completion_barrier=AssignedCompletionBarrier(self.machine_events)
            self._completion_window_seconds=completion_window_seconds

    def _barrier_evidence(self, operation_id, phase):
        """Queue the fixed pair only on the sender already owned by this backend."""
        key=(operation_id,phase)
        if key not in self._barrier_tokens:
            def enqueue(command):
                if self.sender.sendCommand(command,tags={'source:llm-completion-barrier'}) is not True:
                    raise RequestRejected('assigned_sender_refused_barrier_command')
            token=self.completion_barrier.dispatch(operation_id,phase,self.identity(),enqueue,
                **({'completion_window':self._completion_window} if phase=='completed' else {}))
            self._barrier_tokens[key]=token
        return self.completion_barrier.poll()

    def _barrier_outcome(self, operation_id, phase):
        try:return self._barrier_evidence(operation_id,phase)
        except Exception as exc:
            self._fault={'reason':'completion_barrier_unqualified'};self._phase='outcome_unknown'
            self.checkpoint=None;self.witness.invalidate_resume()
            return Outcome('outcome_unknown',{'reason':'completion_barrier_unqualified',
                'detail':str(exc)[:128],'physical_stop_confirmed':False})

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
            elif event=='completed':
                if self.completion_barrier is not None:
                    from .assigned_barrier import AssignedCompletionWindow
                    try:
                        if self._completion_window is not None:raise ValueError('duplicate EOF')
                        self._completion_window=AssignedCompletionWindow(value,self._completion_window_seconds)
                    except (ValueError,TypeError):self._fault=self._fault or {'reason':'completion_notice_invalid'}
                self._phase='completing'
            elif event=='cancelled':self._phase='cancelling'
            else:self._fault=self._fault or {'reason':'sender_job_failed'}
        if self.prompt.channel.state in ('uncertain','disconnected') or self.prompt.receive_hub.fault or self.prompt.frame_writer.fault:
            self._fault=self._fault or {'reason':'shared_connection_uncertain'}
        state=self.sender.getState()
        if state in (self.sender.STATE_ERROR,self.sender.STATE_CLOSED,self.sender.STATE_CLOSED_WITH_ERROR):
            self._fault=self._fault or {'reason':'sender_fault_or_disconnect'}
        if self._fault:
            self._phase='outcome_unknown';self.checkpoint=None;self.witness.invalidate_resume()
        elif self._phase=='completing' and self.completion_barrier is not None:
            self._finish_lifecycle(state)
        elif self._phase=='completing' and state==self.sender.STATE_OPERATIONAL:
            # Provider must independently establish the final barrier. The EOF
            # callback and Operational state are not movement completion evidence.
            try:proven=self.evidence.completed(self.identity(),self._start_operation_id)
            except Exception:
                self._fault={'reason':'completion_evidence_unavailable'};self._phase='outcome_unknown'
                self.checkpoint=None;self.witness.invalidate_resume()
            else:
                if self._evidence_valid(proven,'completed',self._start_operation_id):self._phase='completed'

    def _completion_file(self):
        cursor=self.sender.getFilePosition()
        if not cursor or cursor.get('origin')!='local' or Path(cursor.get('filename','')).resolve()!=self.path:
            raise RequestRejected('completion_file_identity_changed')
        data=self._read_bounded(self.path)
        if hashlib.sha256(data).hexdigest()!=self.sha256 or cursor.get('pos')!=len(data):
            raise RequestRejected('completion_file_hash_or_cursor_unqualified')

    def _finish_lifecycle(self,state):
        try:
            window=self._completion_window;now=self.clock()
            if window is None or not window.observed_at<=now<window.deadline:
                raise RequestRejected('completion_lifecycle_window_expired')
            if state!=self.sender.STATE_OPERATIONAL:return
            self._completion_file()
            with self.machine_events.journal._db() as db:
                row=db.execute('SELECT status FROM operations WHERE operation_id=?',(self._start_operation_id,)).fetchone()
            if row is not None and row['status']=='running':return
            if row is None or row['status']!='completed':
                raise RequestRejected('completed_start_receipt_required')
            marker=self._barrier_evidence(self._start_operation_id,'completed')
            if marker is None:return
            proof=self.evidence.completed(self.identity(),self._start_operation_id)
            if not self._evidence_valid(proof,'completed',self._start_operation_id,deadline=window.deadline):return
            if not window.observed_at<=proof.observed_at:
                raise RequestRejected('completion_provider_before_eof')
            marker=self._barrier_evidence(self._start_operation_id,'completed')
            if marker is None:return
            if marker.get('identity')!=self.identity() or marker.get('operation_id')!=self._start_operation_id:
                raise RequestRejected('completion_marker_job_identity_changed')
            result=dict(identity=self.identity(),start_operation_id=self._start_operation_id,
                completion_window=window.record(),marker=marker,external_evidence=dict(source=proof.source,
                    scope=proof.scope,observed_at=proof.observed_at),physical_control_qualified=False)
            # This is a durable historical evidence input, not restored authority.
            sequence=self.machine_events.journal.record_observation(dict(event='completion_evidence_observed',
                **result),source='job.inspect',now=self.clock())
            if self.sender.getState()!=self.sender.STATE_OPERATIONAL:
                raise RequestRejected('completion_identity_changed_during_commit')
            self._completion_file()
            marker=self._barrier_evidence(self._start_operation_id,'completed')
            if marker is None:raise RequestRejected('completion_marker_lost_during_commit')
            result['observation_sequence']=sequence
            self._completion_result=result;self._phase='completed'
        except Exception as exc:
            self._fault=self._fault or {'reason':'completion_lifecycle_unqualified','detail':str(exc)[:128]}
            self._phase='outcome_unknown';self.checkpoint=None;self.witness.invalidate_resume()

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
        if self.cold_inspection is not None and self.cold_inspection.pending:return False
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
            self._check_cold_preflight(data)
            if (self.cold_inspection is None or self._cold_admitting) and self.evidence.preflight(data,self.identity()) is not True:raise RequestRejected('job_preflight_unqualified')
            self._check_cold_preflight(data)
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
            self._check_cold_preflight(self._read_bounded(self.path))
            if self._started or state!=self.sender.STATE_OPERATIONAL:raise RequestRejected('job_not_ready_to_start')
            blockers=[] if self.cold_inspection is not None and not self._cold_admitting else self.evidence.start_blockers(self.identity())
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
        if self.cold_inspection is not None and operation in ('job.stage','job.start'):
            self.cold_inspection.begin(operation_id,operation,arguments,deadline)
            return
        self._dispatch_job_effect(operation_id,operation,arguments,deadline=deadline)

    def _dispatch_job_effect(self,operation_id,operation,arguments,*,deadline):
        if operation=='job.stage':
            data=self._read_bounded(self.source_path)
            if not 0<len(data)<=16*1024*1024 or hashlib.sha256(data).hexdigest()!=self.sha256:raise RequestRejected('source_changed_before_stage')
            self._check_cold_preflight(data)
            if self.evidence.preflight(data,self.identity()) is not True:raise RequestRejected('job_preflight_unqualified')
            screening=self._check_cold_preflight(data)
            destination=(Path(self._staging.name)/(uuid.uuid4().hex+'.gcode')).resolve()
            with destination.open('xb') as output:output.write(data)
            destination.chmod(0o400)
            if self.cold_inspection is not None:
                self.machine_events.lease.check()
                self._check_cold_preflight(data)
                if self.clock()>=deadline:raise RequestRejected('cold_deadline_before_file_selection')
            self.sender.selectFile(str(destination),False)
            self._cold_stage_screening=screening
            self.path=destination;self._staged=True;self._stage_revision+=1;self.checkpoint=None;self._phase='staged'
        elif operation=='job.start':
            self._check_cold_preflight(self._read_bounded(self.path))
            self.witness.start_job()
            if self.cold_inspection is not None:
                self.machine_events.lease.check()
                self._check_cold_preflight(self._read_bounded(self.path))
                if self.clock()>=deadline:raise RequestRejected('cold_deadline_before_print_start')
            self._started=True;self._start_operation_id=operation_id;self._phase='starting';self.sender.startPrint()
        elif operation=='job.pause':self._phase='pausing';self.sender.setPause(True)
        elif operation=='job.resume':
            if self.completion_barrier is not None:
                self._phase='resume_admission'
                return  # The journaled operation polls fresh evidence before motion.
            if not self.witness.arm_resume(self._pause_epoch):raise RequestRejected('stream_changed_before_resume')
            self._resume_dispatched.add(operation_id)
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
        return self._resume_blockers(None)

    def _resume_blockers(self,barrier):
        if self._fault:return ['shared_connection_requires_reconciliation']
        if self.checkpoint is None:return ['qualified_checkpoint_unavailable']
        if not self._file_valid():return ['staged_file_changed']
        if self.witness.snapshot()[2]!=self._pause_epoch:return ['commands_since_checkpoint']
        try:current=self._capture()
        except (RequestRejected,InvalidCheckpoint):return ['current_checkpoint_unqualified']
        if not self._file_valid():return ['staged_file_changed']
        if barrier is not None:
            operation_id=barrier.get('operation_id')
            if (barrier.get('phase')!='resuming' or barrier.get('identity')!=self.identity()
                    or self._operations.get(operation_id)!='job.resume'
                    or barrier.get('token')!=self._barrier_tokens.get((operation_id,'resuming'))):
                return ['resume_barrier_identity_mismatch']
            writes=barrier.get('writes')
            if type(writes) is not list or len(writes)!=2 or any(type(w) is not dict or 'line_number' not in w for w in writes):
                return ['resume_barrier_transport_line_unqualified']
            numbers=[w['line_number'] for w in writes if w['line_number'] is not None]
            saved=self.checkpoint.to_dict();live=current.to_dict()
            if (any(type(n) is not int for n in numbers)
                    or numbers!=list(range(saved['next_line_number'],saved['next_line_number']+len(numbers)))
                    or live['next_line_number']!=saved['next_line_number']+len(numbers)):
                return ['resume_barrier_transport_line_mismatch']
            # Only these exact read-only barrier frames may advance transport
            # numbering. File cursor and every physical/modal proof stay strict.
            live['next_line_number']=saved['next_line_number'];current=PauseCheckpoint(live)
        return self.checkpoint.in_place_resume_blockers(current,now=self.clock(),max_observation_age=1)

    def _resume_refusal(self,reason):
        self._fault={'reason':'resume_admission_unqualified'};self._phase='outcome_unknown'
        self.checkpoint=None;self.witness.invalidate_resume()
        return Outcome('outcome_unknown',{'reason':'resume_admission_unqualified','detail':reason[:128],
            'physical_stop_confirmed':False})

    def poll(self,operation_id):
        if operation_id in self._cold_results:return self._cold_results[operation_id]
        self._drain();operation=self._operations[operation_id]
        if operation in self.prompt.supported_operations:return self.prompt.poll(operation_id)
        state=self.sender.getState()
        if self._fault:return Outcome('outcome_unknown',{'reason':'shared_connection_uncertain'})
        if self.cold_inspection is not None and operation_id in self.cold_inspection.pending:
            admission=self.cold_inspection.poll(operation_id)
            if admission is None:return None
            if isinstance(admission,Outcome):
                del self.cold_inspection.pending[operation_id]
                return admission
            item=self.cold_inspection.pending[operation_id]
            self._cold_admitting=True
            try:
                self.validate(operation,item['arguments'])
                self.machine_events.lease.check()
                if self.clock()>=item['deadline']:raise RequestRejected('cold_deadline_before_job_effect')
            except RequestRejected as exc:
                del self.cold_inspection.pending[operation_id]
                return Outcome('failed',{'reason':str(exc),'job_effect_dispatched':False})
            except Exception:
                del self.cold_inspection.pending[operation_id]
                return Outcome('failed',{'reason':'cold_admission_unavailable','job_effect_dispatched':False})
            finally:self._cold_admitting=False
            # Consume admission before the effect; exceptions never permit replay.
            del self.cold_inspection.pending[operation_id]
            self._cold_admitting=True
            try:self._dispatch_job_effect(operation_id,operation,item['arguments'],deadline=item['deadline'])
            except Exception:
                self._fault=self._fault or {'reason':'cold_job_dispatch_interrupted'}
                return Outcome('outcome_unknown',{'reason':'cold_job_dispatch_interrupted','physical_stop_confirmed':False})
            finally:self._cold_admitting=False
            state=self.sender.getState()
        if self.clock()>=self._deadlines[operation_id]:
            self._fault=self._fault or {'reason':'operation_deadline_expired'}
            self._phase='outcome_unknown';self.checkpoint=None;self.witness.invalidate_resume()
            return Outcome('outcome_unknown',{'reason':'operation_deadline_expired','physical_stop_confirmed':False})
        if self._fault:return Outcome('outcome_unknown',{'reason':'shared_connection_uncertain'})
        if operation=='job.stage':
            cursor=self.sender.getFilePosition()
            if cursor and Path(cursor.get('filename','')).resolve()==self.path and self._file_valid():
                return Outcome('completed',{'evidence_type':'assigned_file_selected','job_id':self.job_id,'file_sha256':self.sha256,
                    **({'cold_preflight':json.loads(json.dumps(self._cold_stage_screening))} if self._cold_stage_screening is not None else {})})
        if operation=='job.resume' and self.completion_barrier is not None and operation_id not in self._resume_dispatched:
            if state!=self.sender.STATE_PAUSED:
                return self._resume_refusal('sender_left_pause_before_resume_admission')
            proof=self._barrier_outcome(operation_id,'resuming')
            if isinstance(proof,Outcome):return proof
            if proof is None:return None
            blockers=self._resume_blockers(proof)
            if blockers:return self._resume_refusal(','.join(blockers))
            proof=self._barrier_outcome(operation_id,'resuming')
            if isinstance(proof,Outcome):return proof
            if proof is None:return None
            if self.sender.getState()!=self.sender.STATE_PAUSED or not self.witness.arm_resume(self._pause_epoch):
                return self._resume_refusal('stream_or_sender_changed_before_resume')
            # Acquiring the stream guard may wait. Recheck source and original
            # arrival/deadline once more before the single resume effect.
            proof=self._barrier_outcome(operation_id,'resuming')
            if isinstance(proof,Outcome):return proof
            if proof is None:return self._resume_refusal('resume_marker_lost_before_effect')
            self._resume_admission[operation_id]=proof
            self._resume_dispatched.add(operation_id)
            self.checkpoint=None;self._phase='resuming'
            try:self.sender.setPause(False)
            except Exception:return self._resume_refusal('resume_effect_interrupted')
            state=self.sender.getState()
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
                    'physical_control_qualified':False,
                    **({'admission_barrier':self._resume_admission[operation_id]} if operation_id in self._resume_admission else {})})
        if operation=='job.pause' and state==self.sender.STATE_PAUSED:
            self.checkpoint=None
            result={'evidence_type':'assigned_sender_pause_state','job_id':self.job_id,'resumable':False,'evidence_scope':self.evidence_kind,'physical_control_qualified':False}
            if self.completion_barrier is not None:
                proof=self._barrier_outcome(operation_id,'paused')
                if isinstance(proof,Outcome):return proof
                if proof is None:return None
                result['completion_barrier']=proof
            try:
                epoch=self.witness.snapshot()[2];checkpoint=self._capture()
                if self.completion_barrier is not None:
                    # An external provider may block. Recheck original marker
                    # age, source authority and actual writes after it returns.
                    proof=self._barrier_outcome(operation_id,'paused')
                    if isinstance(proof,Outcome):return proof
                    if proof is None:return None
                    result['completion_barrier']=proof
                if not self.witness.freeze(epoch):raise RequestRejected('commands_during_checkpoint')
                self.checkpoint=checkpoint;self._pause_epoch=epoch
                result.update(checkpoint.envelope());result['resumable']=True
            except (RequestRejected,InvalidCheckpoint) as exc:
                result['resume_blockers']=[str(exc)];result['reason']='pause_evidence_unqualified'
                self._fault={'reason':'pause_evidence_unqualified'};self._phase='outcome_unknown';self.witness.invalidate_resume()
                return Outcome('outcome_unknown',result)
            return Outcome('completed',result)
        if operation=='job.cancel' and state==self.sender.STATE_OPERATIONAL:
            barrier=None
            if self.completion_barrier is not None:
                barrier=self._barrier_outcome(operation_id,'cancelled')
                if isinstance(barrier,Outcome):return barrier
                if barrier is None:return None
            try:proven=self.evidence.cancelled(self.identity(),operation_id)
            except Exception:
                self._fault={'reason':'cancellation_evidence_unavailable'};self._phase='outcome_unknown';self.witness.invalidate_resume()
                return Outcome('outcome_unknown',{'reason':'cancellation_evidence_unavailable'})
            if self._evidence_valid(proven,'cancelled',operation_id,deadline=self._deadlines[operation_id]):
                if self.completion_barrier is not None:
                    barrier=self._barrier_outcome(operation_id,'cancelled')
                    if isinstance(barrier,Outcome):return barrier
                    if barrier is None:return None
                self._phase='cancelled'
                return Outcome('completed',{'evidence_type':'assigned_cancel_with_external_completion_evidence','job_id':self.job_id,
                    'evidence_source':proven.source,'evidence_scope':proven.scope,'evidence_observed_at':proven.observed_at,'physical_control_qualified':False,
                    **({'completion_barrier':barrier} if barrier is not None else {})})
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
                    completion_evidence=json.loads(json.dumps(self._completion_result)),
                    resumable=paused and not blockers,resume_blockers=blockers,blocking_fault=self._fault,
                    physical_control_qualified=False,external_evidence_provider_required=True,
                    prompt_pump=dict(started=self._pump_started,running=bool(self._pump_thread and self._pump_thread.is_alive()),
                        samples=self._pump_samples,error=self._pump_error),
                    serial_reports=self.machine_events.snapshot() if self.machine_events is not None else None)

    def capabilities(self):
        return dict(scope='experimental_assigned_job_adapter',physical_control_qualified=False,
                    pause_cancel_barrier_enabled=self.completion_barrier is not None,
                    resume_completion_barrier_enabled=self.completion_barrier is not None,
                    lifecycle_completion_barrier_enabled=self.completion_barrier is not None,
                    cold_preflight_enabled=self.cold_preflight is not None,
                    cold_automatic_inspection=self.cold_inspection is not None,
                    cold_preflight_authorizes_physical_control=False,
                    completion_window_seconds=self._completion_window_seconds if self.completion_barrier is not None else None,
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
