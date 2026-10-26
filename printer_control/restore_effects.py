"""Durable restoration evidence, not permission to resume a job.

The adapter owns exact command dispatch and independently observed barrier/state.
All access shares the operation service's owner and backend guard. No retries.
"""
import json

from .journal import RequestRejected
from .pause_restore import InPlaceRestorePlan


class DurableRestore:
    def __init__(self, service, plan, send_plan, *, expected_epoch, owning_operation=None):
        if not isinstance(plan, InPlaceRestorePlan) or not callable(send_plan):
            raise ValueError('restore_plan_and_adapter_required')
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise ValueError('restore_epoch_required')
        self.service, self.plan, self.send_plan = service, plan, send_plan
        self.epoch = expected_epoch
        self.status, self.invalidated, self.receipt_failure = 'prepared', None, False
        self.resume_operation_id = None
        self.owning_operation = owning_operation
        with service.backend.guard():
            self._owner()
            self._witness(expected_epoch)
            if service._restore is not None or service._restore_recovery_required:
                raise RequestRejected('prior_restore_requires_recovery')
            service._restore = self
            try:
                with service.journal._db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute('''CREATE TABLE IF NOT EXISTS restore_effects (
                        plan_id TEXT PRIMARY KEY, checkpoint_id TEXT UNIQUE NOT NULL,
                        status TEXT NOT NULL, invalidated TEXT, plan TEXT NOT NULL,
                        result TEXT NOT NULL)''')
                    if db.execute("SELECT 1 FROM restore_effects WHERE status != 'resumed'").fetchone():
                        raise RequestRejected('prior_restore_requires_recovery')
                    payload = dict(saved=plan.saved.envelope(), before=plan.before.envelope(),
                        planned_at=plan.planned_at,max_observation_age=plan.max_observation_age,
                        commands=list(plan.commands),marker=plan.marker,expected_epoch=expected_epoch)
                    db.execute('INSERT INTO restore_effects VALUES (?,?,?,?,?,?)',
                        (plan.id,plan.saved.id,'prepared',None,service.journal._encode(payload),'{}'))
            except Exception:
                self.invalidated, self.receipt_failure = 'restore_admission_storage_failed', True
                raise

    def _owner(self, allowed_operation=None):
        service = self.service
        allowed_operation = allowed_operation or self.owning_operation
        service.lease.check()
        context = service.backend.context()
        service._fresh(context,service.clock())
        saved = self.plan.saved.to_dict()
        if (context.printer_id,context.generation,getattr(service.backend,'host_session',None)) != (
                saved['printer_id'],saved['generation'],saved['host_session']):
            raise RequestRejected('restore_owner_identity_changed')
        if service._pending and set(service._pending) != {allowed_operation}:
            raise RequestRejected('ordinary_operation_in_progress')
        thermal = service._thermal_pause
        if service._thermal_recovery_required or (thermal is not None and (
                thermal.policy.state != 'consumed' or thermal.receipt_failure)):
            raise RequestRejected('thermal_restoration_not_ready')

    def validate_resume(self, arguments, *, allowed_operation=None):
        """Backend must acquire fresh job/position/thermal evidence each call."""
        if self.status != 'completed' or self.invalidated or self.receipt_failure:
            raise RequestRejected('restoration_not_ready_for_resume')
        if arguments != {'job_id':self.plan.saved.to_dict()['job_id']}:
            raise RequestRejected('wrong_restore_job_or_arguments')
        self._owner(allowed_operation)
        self._witness(self.epoch + 2 * len(self.plan.commands))
        monitor = self.service._thermal_monitor
        if monitor is not None and (not monitor.closed or
                (monitor.thread is not None and monitor.thread.is_alive())):
            raise RequestRejected('pause_monitor_not_closed_for_resume')
        acquire = getattr(self.service.backend,'restored_resume_evidence',None)
        dispatch = getattr(self.service.backend,'dispatch_restored_resume',None)
        if not callable(acquire) or not callable(dispatch):
            raise RequestRejected('backend_restored_resume_unsupported')
        try:
            evidence = acquire(self.plan)
            if type(evidence) is not dict or evidence.keys() != {'checkpoint','thermal','monotonic_now'}:
                raise RequestRejected('incomplete_restore_resume_evidence')
            barrier = self.receipt()['result']['barrier_completed_at']
            reasons = self.plan.verification_blockers(evidence['checkpoint'],now=self.service.clock(),
                                                      barrier_completed_at=barrier)
            if reasons:
                raise RequestRejected(','.join(reasons))
            thermal = self.service._thermal_pause
            if thermal is not None:
                if thermal.policy.checkpoint_id != self.plan.saved.id:
                    raise RequestRejected('thermal_restore_checkpoint_mismatch')
                thermal.recheck_consumed(evidence['thermal'],now=evidence['monotonic_now'],
                                        allowed_operation=allowed_operation)
            elif any(self.plan.saved.to_dict()['modal'][k] != 0 for k in ('nozzle_target','bed_target')):
                raise RequestRejected('hot_resume_requires_thermal_policy')
            return evidence
        except Exception:
            self.interrupt('restore_resume_evidence_failed')
            raise

    def dispatch_resume(self, operation_id, arguments, *, deadline):
        evidence = self.validate_resume(arguments,allowed_operation=operation_id)
        result = self.receipt()['result']
        result.update(resume_operation_id=operation_id,
                      resume_observation=evidence['checkpoint'].envelope())
        try:
            self._persist('resume_dispatching',result=result)
            self.status = 'resume_dispatching'
            self.resume_operation_id = operation_id
            self.service.backend.dispatch_restored_resume(operation_id,self.plan,
                expected_epoch=self.epoch + 2 * len(self.plan.commands),deadline=deadline)
        except BaseException:
            self.interrupt('restore_resume_dispatch_interrupted')
            raise

    def finish_resume(self, operation_id, outcome):
        if self.invalidated:
            return
        if self.status != 'resume_dispatching' or self.invalidated or self.receipt_failure:
            raise RequestRejected('restore_resume_not_pending')
        result = self.receipt()['result']
        if result.get('resume_operation_id') != operation_id:
            raise RequestRejected('restore_resume_operation_mismatch')
        if outcome.status != 'completed':
            self.interrupt('restore_resume_outcome_unconfirmed')
            return
        result['resume_result'] = outcome.detail
        try:
            monitor = self.service._thermal_monitor
            if monitor is not None and (not monitor.closed or
                    (monitor.thread is not None and monitor.thread.is_alive())):
                raise RequestRejected('pause_monitor_not_closed_for_retirement')
            retire = getattr(self.service.backend,'retire_restored_pause',None)
            if callable(retire):
                retire(self.plan.saved.id)
            self._persist('resumed',result=result)
        except Exception:
            self.interrupt('restore_resume_completion_storage_failed')
            raise
        self.status = 'resumed'
        thermal = self.service._thermal_pause
        if thermal is not None:
            thermal.retired = True
        self.service._thermal_pause = None
        self.service._thermal_monitor = None

    def _witness(self, epoch):
        witness = getattr(self.service.backend,'witness',None)
        if (witness is None or not witness.active or not witness.frozen or
                witness.allow_job_commands or witness.snapshot()[2] != epoch):
            raise RequestRejected('restore_stream_continuity_changed')

    def _persist(self, status, *, result=None, invalidated=None):
        try:
            self._write_receipt(status,result=result,invalidated=invalidated)
        except Exception:
            self.receipt_failure = True
            raise

    def _write_receipt(self, status, *, result=None, invalidated=None):
        with self.service.journal._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('UPDATE restore_effects SET status=?,invalidated=?,result=COALESCE(?,result) WHERE plan_id=?',
                (status,invalidated,None if result is None else self.service.journal._encode(result),self.plan.id))
            if row.rowcount != 1:
                raise RequestRejected('restore_receipt_missing')

    def dispatch(self, *, now):
        with self.service.backend.guard():
            if self.invalidated or self.receipt_failure or self.status != 'prepared':
                raise RequestRejected('restore_already_dispatched_or_invalid')
            try:
                self._owner()
                self._witness(self.epoch)
                if now < self.plan.planned_at:
                    raise RequestRejected('restore_clock_reversed')
                InPlaceRestorePlan(self.plan.saved,self.plan.before,now=now,
                                   max_observation_age=self.plan.max_observation_age)
                self._persist('dispatching')
            except Exception:
                self.interrupt('restore_dispatch_admission_failed')
                raise
            self.status = 'dispatching'
            try:
                self.send_plan(self.plan,self.epoch)
            except BaseException:
                self.interrupt('restore_dispatch_interrupted')
                raise
            return self.receipt()

    def complete(self, current, *, marker, barrier_completed_at, now):
        with self.service.backend.guard():
            if self.invalidated or self.receipt_failure or self.status != 'dispatching':
                raise RequestRejected('restore_not_awaiting_evidence')
            try:
                self._owner()
                self._witness(self.epoch + 2 * len(self.plan.commands))
                if marker != self.plan.marker:
                    raise RequestRejected('restore_marker_mismatch')
                reasons = self.plan.verification_blockers(current,now=now,
                                                         barrier_completed_at=barrier_completed_at)
                if reasons:
                    raise RequestRejected(','.join(reasons))
                self._persist('completed',result=dict(observation=current.envelope(),marker=marker,
                    barrier_completed_at=barrier_completed_at,completed_at=now,
                    resume_authorized=False))
            except Exception:
                self.interrupt('restore_completion_unconfirmed')
                raise
            self.status = 'completed'
            return self.receipt()

    def interrupt(self, reason):
        self.invalidated = reason
        witness = getattr(self.service.backend,'witness',None)
        if witness is not None:
            witness.invalidate_resume()
        if self.status in ('dispatching','resume_dispatching'):
            self.status = 'outcome_unknown'
        try:
            self._persist(self.status,invalidated=reason)
        except Exception:
            self.receipt_failure = True

    def receipt(self):
        with self.service.backend.guard():
            self.service.lease.check()
            with self.service.journal._db() as db:
                row = db.execute('SELECT * FROM restore_effects WHERE plan_id=?',(self.plan.id,)).fetchone()
            if row is None:
                raise RequestRejected('restore_receipt_missing')
            return {**dict(row),'plan':json.loads(row['plan']),'result':json.loads(row['result']),
                    'receipt_storage_failed':self.receipt_failure,'resume_authorized':False}
