"""Operation-bound M400/echo evidence on an existing assigned connection.

Internal adapter primitive, not an LLM G-code interface or physical checkpoint.
The caller supplies its qualified firmware profile and queues through its existing
sender. Original source capture and actual complete-frame writes establish the
correlation; sender callbacks and ok responses do not establish completion.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import threading
import uuid

from .assigned_observations import AssignedMachineEventRecorder
from .journal import RequestRejected


@dataclass(frozen=True)
class AssignedCompletionWindow:
    """Internal bounded EOF evidence window; never extends command deadlines."""
    observed_at: float
    seconds: float

    def __post_init__(self):
        if (type(self.observed_at) not in (int,float) or not math.isfinite(self.observed_at) or self.observed_at<0
                or type(self.seconds) not in (int,float) or not math.isfinite(self.seconds) or not 0<self.seconds<=120
                or not math.isfinite(self.deadline) or self.deadline<=self.observed_at):
            raise ValueError('bounded completion lifecycle window required')

    @property
    def deadline(self):return self.observed_at+self.seconds

    def record(self):return dict(source='assigned_sender_eof_callback',observed_at=self.observed_at,
        seconds=self.seconds,deadline=self.deadline)


class AssignedCompletionBarrier:
    PHASE_OPERATIONS = {'paused': 'job.pause', 'completed': 'job.start', 'cancelled': 'job.cancel', 'resuming': 'job.resume', 'configuration': 'printer.configuration.inspect'}

    def __init__(self, recorder):
        if type(recorder) is not AssignedMachineEventRecorder:
            raise TypeError('owned assigned source capture required')
        self.recorder = recorder
        self._pid = os.getpid()
        self._lock = threading.RLock()
        self._active = None
        self._sequence = 0
        self._check_source()
        with recorder.journal._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS assigned_barriers (
                operation_id TEXT NOT NULL, phase TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
                intent TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(operation_id,phase))''')
            # This instance cannot resume any earlier intent, even in the same
            # process. A successor reconciles; it never resubmits old commands.
            if db.execute('SELECT 1 FROM assigned_barriers LIMIT 1').fetchone():
                raise RequestRejected('prior_barrier_session_requires_reconciliation')
        recorder.endpoint.backend.frame_writer.attach_observer(self)

    def _now(self):
        now = self.recorder.clock()
        if type(now) not in (int,float) or not math.isfinite(now):
            raise RequestRejected('barrier_clock_invalid')
        return now

    def _check_source(self):
        if os.getpid() != self._pid:
            raise RequestRejected('barrier_belongs_to_another_process')
        self.recorder.lease.check()
        if (self.recorder.endpoint.backend.channel.generation != self.recorder._connection_generation
                or self.recorder.endpoint.backend.printer_id != self.recorder._printer_id):
            raise RequestRejected('barrier_source_identity_changed')
        snapshot = self.recorder.snapshot()
        if (not snapshot['source_usable'] or not snapshot['capturing'] or snapshot['fault']
                or snapshot['controller_resets_observed'] or snapshot['dispatch_inhibit']):
            raise RequestRejected('barrier_source_requires_reconciliation')

    def _check_operation(self, action):
        with self.recorder.journal._db() as db:
            row = db.execute('SELECT request,status FROM operations WHERE operation_id=?',
                (action['operation_id'],)).fetchone()
        permitted = ('completed',) if action['phase'] == 'completed' else ('running',)
        if row is None or row['status'] not in permitted or json.loads(row['request']) != action['request']:
            raise RequestRejected('barrier_operation_no_longer_authorized')

    @staticmethod
    def _command(data):
        """Decode only exact plain commands or valid numbered/checksummed frames."""
        if type(data) is not bytes or not data.endswith(b'\n'):
            return None
        raw = data[:-1]
        match = re.fullmatch(rb'N[0-9]+ (.+)\*([0-9]{1,3})', raw)
        if match:
            checksum = 0
            for byte in raw[:raw.rfind(b'*')]: checksum ^= byte
            if checksum != int(match[2]): return None
            raw = match[1]
        try: return raw.decode('ascii')
        except UnicodeDecodeError: return None

    def before_write(self, sequence, data):
        with self._lock:
            self._sequence = sequence
            action = self._active
            if action is None: return
            now = self._now()
            if now < action['started_at'] or now >= action['deadline']:
                action['fault'] = action['fault'] or 'barrier_write_outside_deadline'
            command = self._command(data)
            index = action['begun']
            if index < len(action['commands']) and command == action['commands'][index]:
                action['begun'] += 1
                action['writes'].append(dict(sequence=sequence,started_at=now,
                    sha256=hashlib.sha256(data).hexdigest(),confirmed=False,
                    line_number=int(re.match(rb'N([0-9]+) ',data)[1]) if re.match(rb'N([0-9]+) ',data) else None))
            else:
                tail=action.get('inspection')
                if (index==len(action['commands']) and tail is not None and not tail['writes']
                        and command==tail['command'] and sequence==action['writes'][-1]['sequence']+1):
                    tail['writes'].append(dict(sequence=sequence,started_at=now,
                        sha256=hashlib.sha256(data).hexdigest(),confirmed=False,
                        line_number=int(re.match(rb'N([0-9]+) ',data)[1]) if re.match(rb'N([0-9]+) ',data) else None))
                else:action['fault'] = action['fault'] or 'barrier_frame_interleaved_or_replayed'

    def after_write(self, sequence, data):
        with self._lock:
            if self._active:
                for write in self._active['writes']+self._active.get('inspection',{}).get('writes',[]):
                    if write['sequence'] == sequence: write['confirmed'] = True

    def dispatch(self, operation_id, phase, identity, enqueue, *, completion_window=None, configuration_report=False):
        """Persist once, then enqueue the fixed command group through the owned sender.

        enqueue is an internal sender adapter, never a caller-supplied tool value.
        A failed/interrupted enqueue leaves a non-replayable durable intent.
        """
        with self._lock:
            self._check_source()
            if self._active is not None and (not self._active.get('completed')
                    or self._active.get('inspection') is not None and not self._active['inspection'].get('complete')):
                raise RequestRejected('barrier_already_pending')
            if type(configuration_report) is not bool or configuration_report and phase not in ('paused','resuming'):
                raise RequestRejected('configuration_report_requires_pause_or_resume')
            if phase not in self.PHASE_OPERATIONS or not callable(enqueue):
                raise RequestRejected('invalid_barrier_dispatch')
            if type(identity) is not dict or identity.keys() != {'printer_id','generation','host_session','job_id','file_sha256'}:
                raise RequestRejected('complete_job_identity_required')
            if any(type(v) is not str or not 1 <= len(v) <= 128 for v in identity.values()):
                raise RequestRejected('bounded_job_identity_required')
            if re.fullmatch('[0-9a-f]{64}', identity['file_sha256']) is None:
                raise RequestRejected('job_content_identity_required')
            recorder = self.recorder
            if identity['printer_id'] != recorder._printer_id or identity['generation'] != recorder._connection_generation:
                raise RequestRejected('barrier_connection_identity_mismatch')
            if (phase=='completed') != (type(completion_window) is AssignedCompletionWindow):
                raise RequestRejected('completion_window_only_for_final_lifecycle')
            if phase!='completed' and completion_window is not None:
                raise RequestRejected('command_deadline_cannot_be_overridden')
            identity = dict(identity)
            now = self._now()
            token = uuid.uuid4().hex
            commands=('M400','M118 E1 LLM_BARRIER_'+token)
            reports_configuration=phase=='configuration' or configuration_report
            if reports_configuration:commands+=('M115','M503','M118 E1 LLM_CONFIG_END_'+token)
            with recorder.journal._db() as db:
                row = db.execute('SELECT request,status FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
                if row is None: raise RequestRejected('journaled_barrier_operation_required')
                request = json.loads(row['request'])
                if (row['status'] not in (('completed',) if phase == 'completed' else ('running',)) or request['operation'] != self.PHASE_OPERATIONS[phase]
                        or request['printer_id'] != identity['printer_id']
                        or request['connection_generation'] != identity['generation']
                        or request['arguments'] != {'job_id': identity['job_id']}
                        or phase!='completed' and now >= request['deadline']):
                    raise RequestRejected('barrier_operation_identity_or_deadline_mismatch')
                deadline=request['deadline']
                if phase=='completed':
                    claimed=db.execute("SELECT MIN(observed_at) FROM events WHERE operation_id=? AND status='running'",(operation_id,)).fetchone()[0]
                    if (claimed is None or not claimed<=completion_window.observed_at<=now<completion_window.deadline):
                        raise RequestRejected('completion_window_stale_or_before_start')
                    deadline=completion_window.deadline
                cursor = db.execute('SELECT MAX(sequence) FROM machine_events').fetchone()[0] or 0
                intent = dict(operation_id=operation_id,phase=phase,identity=identity,token=token,
                    capture_id=recorder.capture_id,started_at=now,deadline=deadline,after=cursor,request=request,commands=commands,reports_configuration=reports_configuration)
                if completion_window is not None:intent['completion_window']=completion_window.record()
                try:
                    db.execute('INSERT INTO assigned_barriers VALUES (?,?,?,?,?,?)',
                        (operation_id,phase,token,recorder.journal._encode(intent),'dispatching','{}'))
                except Exception as exc:
                    raise RequestRejected('barrier_intent_not_new') from exc
            self._active = dict(intent,commands=commands,
                begun=0,writes=[],fault=None,receipt=None,end_receipt=None)
            # Commit latency may consume the deadline or ownership.
            try:
                self._check_source()
                self._check_operation(self._active)
                if self._now() >= intent['deadline']: raise RequestRejected('barrier_expired_before_enqueue')
            except BaseException:
                self._active['fault'] = 'barrier_authority_lost_before_enqueue'
                raise
            commands = self._active['commands']
        try:
            for command in commands: enqueue(command)
        except BaseException:
            with self._lock: self._active['fault'] = self._active['fault'] or 'barrier_enqueue_interrupted'
            raise
        return token

    def retire_configuration(self, operation_id):
        """Release a completed read interval before future ordinary job writes."""
        with self._lock:
            proof=self.poll()
            if proof is None or proof['operation_id']!=operation_id or proof['phase']!='configuration':
                raise RequestRejected('completed_configuration_interval_required')
            self._active=None
            return proof

    def authorize_inspection(self, operation_id, child_id, wire_request):
        """Permit one exact prompt-lane inspect after a fresh completed marker.

        The caller journals/claims the child and dispatches via PromptBackend.
        No raw bytes or arbitrary read commands are accepted here.
        """
        from .prompt_wire import PromptWireCommand
        with self._lock:
            proof=self.poll()
            action=self._active
            if (proof is None or action['operation_id']!=operation_id
                    or action['phase'] not in ('paused','resuming') or 'inspection' in action):
                raise RequestRejected('fresh_barrier_before_one_inspection_required')
            if type(child_id) is not str or not 1<=len(child_id)<=128:
                raise RequestRejected('journaled_barrier_inspect_required')
            command=PromptWireCommand('inspect',wire_request).encode().decode('ascii').strip()
            with self.recorder.journal._db() as db:
                row=db.execute('SELECT request,status FROM operations WHERE operation_id=?',(child_id,)).fetchone()
                if row is None or row['status']!='running':raise RequestRejected('running_barrier_inspect_required')
                request=json.loads(row['request'])
                if (request['operation']!='prompt.inspect' or request['arguments']!={}
                        or request['request_id']!='checkpoint-inspect:'+operation_id
                        or request['printer_id']!=action['identity']['printer_id']
                        or request['connection_generation']!=action['identity']['generation']
                        or request['deadline']!=action['deadline']):
                    raise RequestRejected('barrier_inspect_identity_or_deadline_mismatch')
                # Durable link precedes allowing the single extra frame.
                intent=json.loads(db.execute('SELECT intent FROM assigned_barriers WHERE token=?',(action['token'],)).fetchone()[0])
                if 'inspection' in intent:raise RequestRejected('barrier_inspect_not_new')
                tail=dict(operation_id=child_id,wire_request=wire_request,command=command,writes=[],complete=False)
                intent['inspection']=tail
                db.execute('UPDATE assigned_barriers SET intent=? WHERE token=?',
                    (self.recorder.journal._encode(intent),action['token']))
            self._check_source();self._check_operation(action)
            if self._now()>=action['deadline']:raise RequestRejected('barrier_inspect_expired_during_commit')
            action['inspection']=tail
            return json.loads(json.dumps(tail))

    def finish_inspection(self, child_id):
        """Close the exact read tail after the coordinator validates its state."""
        with self._lock:
            proof=self.poll()
            tail=self._active.get('inspection')
            if (proof is None or tail is None or tail['operation_id']!=child_id
                    or len(tail['writes'])!=1 or not tail['writes'][0]['confirmed']
                    or self.recorder.journal.get(child_id)['status']!='completed'):
                raise RequestRejected('completed_barrier_inspection_required')
            tail['complete']=True
            return self.poll()

    def poll(self, *, max_age=1.):
        if type(max_age) not in (int,float) or not math.isfinite(max_age) or not 0 < max_age <= 1:
            raise RequestRejected('bounded_barrier_age_required')
        with self._lock:
            action = self._active
            if action is None: raise RequestRejected('no_pending_barrier')
            try:
                self._check_source()
                self._check_operation(action)
                page = self.recorder.read(after=action['after'],limit=1000)
                if page['history_gap'] or page['cursor_ahead']:
                    action['fault'] = action['fault'] or 'barrier_capture_history_gap'
                for row in page['events']:
                    event = row['event']
                    if event['kind'] != 'serial_line_received': continue
                    raw = event['data']['text'].encode('ascii')
                    marker=raw.rstrip(b'\r\n')
                    is_end=(action['reports_configuration'] and marker==('echo:LLM_CONFIG_END_'+action['token']).encode())
                    if not is_end and marker!=('echo:LLM_BARRIER_'+action['token']).encode():continue
                    key='end_receipt' if is_end else 'receipt'
                    write_index=4 if is_end else 1
                    provenance = event['data'].get('receive_provenance',{})
                    arrival = event['observed_at']
                    writes = action['writes']
                    valid = (row['capture_id'] == self.recorder.capture_id
                        and event['generation'] == self.recorder._connection_generation
                        and provenance.get('capture_id') == self.recorder.capture_id
                        and provenance.get('printer_id') == self.recorder._printer_id
                        and provenance.get('connection_generation') == self.recorder._connection_generation
                        and provenance.get('sha256') == hashlib.sha256(raw).hexdigest()
                        and len(writes)>write_index and writes[write_index]['started_at'] <= arrival < action['deadline'])
                    if not valid or action[key] is not None:
                        action['fault'] = action['fault'] or 'barrier_marker_invalid_or_replayed'
                    else: action[key] = dict(event_sequence=row['sequence'],observed_at=arrival,provenance=provenance)
                action['after'] = page['next_after']
                now = self._now()
                if now < action['started_at'] or now >= action['deadline']:
                    action['fault'] = action['fault'] or 'barrier_deadline_expired'
                receipt = action['receipt']
                if receipt and not 0 <= now-receipt['observed_at'] <= max_age:
                    action['fault'] = action['fault'] or 'barrier_marker_stale'
                end=action.get('end_receipt')
                if end and not 0<=now-end['observed_at']<=max_age:action['fault']=action['fault'] or 'configuration_end_marker_stale'
                if end and (receipt is None or end['event_sequence']<=receipt['event_sequence']):action['fault']=action['fault'] or 'configuration_marker_order_invalid'
                if action['fault']: raise RequestRejected(action['fault'])
                if (not receipt or len(action['writes'])!=len(action['commands']) or not all(w['confirmed'] for w in action['writes'])
                        or action['reports_configuration'] and end is None):return None
                result = dict(operation_id=action['operation_id'],phase=action['phase'],identity=action['identity'],
                    token=action['token'],capture_id=action['capture_id'],writes=action['writes'],receipt=receipt,
                    **({'end_receipt':end} if end is not None else {}),
                    evidence_type='source_correlated_firmware_barrier_marker',
                    physical_readiness_qualified=False,resume_authorized=False,
                    **({'inspection':action['inspection']} if 'inspection' in action else {}),
                    **({'completion_window':action['completion_window']} if 'completion_window' in action else {}))
                with self.recorder.journal._db() as db:
                    db.execute("UPDATE assigned_barriers SET status='completed',result=? WHERE token=?",
                        (self.recorder.journal._encode(result),action['token']))
                # Never upgrade old source time after persistence/lease latency.
                self._check_source()
                self._check_operation(action)
                now = self._now()
                if (not 0 <= now-receipt['observed_at'] <= max_age or now >= action['deadline']
                        or end is not None and not 0<=now-end['observed_at']<=max_age):
                    raise RequestRejected('barrier_expired_during_commit')
                action['completed'] = True
                return json.loads(json.dumps(result))
            except Exception:
                action['fault'] = action['fault'] or 'barrier_evidence_unavailable'
                with self.recorder.journal._db() as db:
                    db.execute("UPDATE assigned_barriers SET status='outcome_unknown',result=? WHERE token=?",
                        (self.recorder.journal._encode({'reason':action['fault']}),action['token']))
                raise
