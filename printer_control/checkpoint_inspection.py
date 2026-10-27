"""Fresh controller variables after a drain marker, without physical authority."""
import json
from .journal import RequestRejected


class AssignedCheckpointInspection:
    def __init__(self,backend):
        self.backend=backend
        self.operations={}
        self.current=None

    def advance(self,operation_id,phase,barrier):
        b=self.backend;r=b.machine_events;j=r.journal
        if phase not in ('paused','resuming'):raise RequestRejected('checkpoint_inspection_phase_required')
        if operation_id not in self.operations:
            with j._db() as db:
                if db.execute('SELECT 1 FROM operations WHERE request_id=?',('checkpoint-inspect:'+operation_id,)).fetchone():
                    raise RequestRejected('checkpoint_inspection_not_new')
            # A supported prompt operation may predate the drain. Wait for its
            # receipt before claiming this child, without extending marker age.
            if b.prompt.channel.state=='busy':return None
            c=b.context();r.lease.check()
            with j._db() as db:
                row=db.execute('SELECT request,status FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
            if row is None or row['status']!='running':raise RequestRejected('running_checkpoint_parent_required')
            parent=json.loads(row['request'])
            if (parent['operation']!={'paused':'job.pause','resuming':'job.resume'}[phase]
                    or parent['arguments']!={'job_id':b.job_id}
                    or parent['printer_id']!=c.printer_id or parent['connection_generation']!=c.generation
                    or barrier['operation_id']!=operation_id or barrier['phase']!=phase
                    or barrier['identity']!=b.identity() or b.clock()>=parent['deadline']):
                raise RequestRejected('checkpoint_parent_or_barrier_mismatch')
            request=dict(request_id='checkpoint-inspect:'+operation_id,printer_id=c.printer_id,
                connection_generation=c.generation,expected_state_version=c.state_version,
                deadline=parent['deadline'],operation='prompt.inspect',arguments={})
            child=j.accept(request,now=b.clock(),printer_id=c.printer_id,generation=c.generation,state_version=c.state_version)
            child_id=child['operation_id']
            if child['status']!='accepted' or not j.claim(child_id,now=b.clock()):
                raise RequestRejected('checkpoint_inspection_not_new')
            b.prompt.validate('prompt.inspect',{})
            wire=b.prompt.channel._sequence+1
            b.completion_barrier.authorize_inspection(operation_id,child_id,wire)
            item=dict(child=child_id,phase=phase,identity=b.identity(),capture=r.capture_id,
                session=b.prompt.channel.session,deadline=parent['deadline'],complete=False)
            self.operations[operation_id]=item
            try:
                r.lease.check()
                if b.clock()>=item['deadline']:raise RequestRejected('checkpoint_inspection_expired_before_dispatch')
                b.prompt.dispatch(child_id,'prompt.inspect',{},deadline=item['deadline'])
            except BaseException:
                j.finish(child_id,'outcome_unknown',{'reason':'checkpoint_inspection_dispatch_interrupted'},now=b.clock())
                raise
        item=self.operations[operation_id]
        if (item['identity']!=b.identity() or item['phase']!=phase or item['capture']!=r.capture_id
                or item['session']!=b.prompt.channel.session or b.clock()>=item['deadline']):
            raise RequestRejected('checkpoint_inspection_authority_changed')
        if not item['complete']:
            outcome=b.prompt.poll(item['child'])
            if outcome is None:return None
            j.finish(item['child'],outcome.status,outcome.detail,now=b.clock())
            if outcome.status!='completed':raise RequestRejected('checkpoint_inspection_unconfirmed')
            item['complete']=True
        # A state line can arrive after its prompt receipt. Only this specific
        # absence/correlation gap is pending; never refresh its arrival time.
        try:report=b.state_reader.read(item['child'])
        except RequestRejected as exc:
            if str(exc) in ('correlated_state_report_pending','state_report_correlation_mismatch'):return None
            raise
        proof=b.completion_barrier.poll()
        if proof is None:raise RequestRejected('checkpoint_drain_marker_lost')
        tail=proof.get('inspection',{})
        writes=tail.get('writes',[])
        if (tail.get('operation_id')!=item['child'] or len(writes)!=1 or not writes[0]['confirmed']
                or not proof['receipt']['observed_at']<=writes[0]['started_at']<=report['observed_at']
                or report['state']['planner_blocks']!=0):
            raise RequestRejected('checkpoint_report_not_after_owned_drain')
        proof=b.completion_barrier.finish_inspection(item['child'])
        final=b.state_reader.read(item['child'])
        if any(final[key]!=report[key] for key in ('operation_id','observed_at','capture_id','state')):
            raise RequestRejected('checkpoint_source_changed_during_commit')
        self.current=operation_id
        return dict(proof,controller_state=report,
            evidence_scope='post_drain_controller_variables_with_external_physical_proofs_required')

    def read(self):
        if self.current is None:raise RequestRejected('post_drain_controller_report_required')
        item=self.operations[self.current]
        if self.backend.machine_events.journal.get(self.current)['status']!='running':
            raise RequestRejected('new_checkpoint_operation_required')
        proof=self.backend.completion_barrier.poll()
        if proof is None:raise RequestRejected('checkpoint_drain_marker_lost')
        return self.advance(self.current,item['phase'],proof)
