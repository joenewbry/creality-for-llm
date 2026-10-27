"""Journal a fresh source inspection within an already committed job operation.

This coordinator never supplies physical qualification. Its caller owns the
backend guard and may dispatch the job effect only after poll returns evidence.
"""
import json
from .journal import RequestRejected
from .service import Outcome


class AssignedColdInspection:
    def __init__(self, backend):
        self.backend = backend
        self.pending = {}

    def begin(self, operation_id, operation, arguments, deadline):
        b = self.backend
        recorder = b.machine_events
        recorder.lease.check()
        with recorder.journal._db() as db:
            row = db.execute('SELECT request,status FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
        if row is None or row['status'] != 'running':
            raise RequestRejected('committed_cold_parent_required')
        parent = json.loads(row['request'])
        context = b.context()
        if (parent['operation'] != operation or parent['arguments'] != arguments
                or parent['deadline'] != deadline or operation not in ('job.stage', 'job.start')
                or parent['printer_id'] != context.printer_id
                or parent['connection_generation'] != context.generation
                or b.clock() >= deadline or operation_id in self.pending):
            raise RequestRejected('cold_parent_identity_or_deadline_mismatch')
        b.prompt.validate('prompt.inspect', {})
        request = dict(request_id='cold-inspect:'+operation_id,
            printer_id=context.printer_id, connection_generation=context.generation,
            expected_state_version=context.state_version, deadline=deadline,
            operation='prompt.inspect', arguments={})
        child = recorder.journal.accept(request, now=b.clock(), printer_id=context.printer_id,
            generation=context.generation, state_version=context.state_version)
        child_id = child['operation_id']
        # Existing child intent is never replay authority, even if memory was lost.
        if child['status'] != 'accepted' or not recorder.journal.claim(child_id, now=b.clock()):
            raise RequestRejected('cold_inspect_already_claimed_or_expired')
        recorder.journal.record_observation(dict(kind='cold_inspection_link', operation_id=operation_id,
            child_operation_id=child_id, scope='cold_source_inspection_only',
            physical_readiness_qualified=False), source='job.inspect', now=b.clock())
        recorder.lease.check()
        if b.clock() >= deadline:
            recorder.journal.finish(child_id, 'failed', {'reason':'cold_inspect_deadline_before_dispatch'}, now=b.clock())
            raise RequestRejected('cold_inspect_deadline_before_dispatch')
        # Set ownership before dispatch: an interrupted write is never repeated.
        self.pending[operation_id] = dict(child=child_id, operation=operation,
            arguments=dict(arguments), deadline=deadline, identity=b.identity(), session=b.prompt.channel.session, capture_id=recorder.capture_id, completed=False)
        try:
            b.prompt.validate('prompt.inspect', {})
            b.prompt.dispatch(child_id, 'prompt.inspect', {}, deadline=deadline)
        except BaseException:
            recorder.journal.finish(child_id, 'outcome_unknown', {'reason':'cold_inspect_dispatch_interrupted'}, now=b.clock())
            raise

    def poll(self, operation_id):
        b = self.backend
        item = self.pending[operation_id]
        journal = b.machine_events.journal
        b.machine_events.lease.check()
        if (b.identity() != item['identity'] or b.clock() >= item['deadline']
                or b.prompt.channel.session != item['session'] or b.machine_events.capture_id != item['capture_id']):
            if journal.get(item['child'])['status']=='running':
                journal.finish(item['child'],'outcome_unknown',{'reason':'cold_parent_admission_expired'},now=b.clock())
            return Outcome('failed', {'reason':'cold_inspection_identity_or_deadline_changed', 'job_effect_dispatched':False})
        child = item['child']
        if not item['completed']:
            result = b.prompt.poll(child)
            if result is None:
                return None
            journal.finish(child, result.status, result.detail, now=b.clock())
            if result.status != 'completed':
                return Outcome('failed', {'reason':'cold_inspection_unconfirmed', 'inspect_operation_id':child,
                    'job_effect_dispatched':False})
            item['completed'] = True
        try:
            path = b.source_path if item['operation'] == 'job.stage' else b.path
            b.cold_preflight.evaluate(b._read_bounded(path), b.identity(), child)
        except RequestRejected as exc:
            # The state line may follow the prompt receipt in the same response.
            # Wait only for that correlated line, without refreshing its clock.
            if str(exc) in ('correlated_state_report_pending', 'state_report_correlation_mismatch'):
                return None
            return Outcome('failed', {'reason':str(exc), 'inspect_operation_id':child,
                'job_effect_dispatched':False})
        return True
