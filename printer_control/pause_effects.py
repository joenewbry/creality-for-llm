"""Experimental durable thermal effects under an existing service owner.

No heater transport or timer is installed here. The caller supplies a qualified
observation and a target-only dispatch adapter. Tests use memory effects and an explicit virtual-only serial adapter.
"""
from dataclasses import asdict
import json

from .journal import RequestRejected
from .thermal_pause import ThermalPausePolicy


class DurablePauseEffects:
    def __init__(self, service, policy, send_targets):
        if not isinstance(policy, ThermalPausePolicy) or not callable(send_targets):
            raise ValueError('policy_and_target_adapter_required')
        self.service, self.policy, self.send_targets = service, policy, send_targets
        self.receipt_failure = False
        self.retired = False
        self.bound_operation_id = None
        with service.backend.guard():
            self._owner()
            previous = service._thermal_pause
            if previous is not None and not previous.retired:
                raise RequestRejected('prior_thermal_session_not_retired')
            with service.journal._db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("""CREATE TABLE IF NOT EXISTS thermal_sessions (
                    checkpoint_id TEXT PRIMARY KEY, host_session TEXT NOT NULL,
                    state TEXT NOT NULL, policy_limits TEXT NOT NULL, snapshot TEXT NOT NULL)""")
                db.execute("""CREATE TABLE IF NOT EXISTS thermal_effects (
                    effect_id TEXT PRIMARY KEY, checkpoint_id TEXT NOT NULL,
                    status TEXT NOT NULL, action TEXT NOT NULL, result TEXT NOT NULL)""")
                if db.execute("SELECT 1 FROM thermal_sessions WHERE state != 'consumed'").fetchone():
                    raise RequestRejected('prior_thermal_session_requires_recovery')
                if db.execute('SELECT 1 FROM thermal_sessions WHERE checkpoint_id=?', (policy.checkpoint_id,)).fetchone():
                    raise RequestRejected('thermal_checkpoint_already_used')
                db.execute('INSERT INTO thermal_sessions VALUES (?,?,?,?,?)', (
                    policy.checkpoint_id, policy.saved['host_session'], policy.state,
                    service.journal._encode(asdict(policy.limits)), service.journal._encode(policy.status())))
            service._thermal_pause = self

    def _owner(self, allowed_operation=None):
        service = self.service
        if self.retired:
            raise RequestRejected('thermal_session_retired')
        allowed_operation = allowed_operation or self.bound_operation_id
        if self.bound_operation_id and self.bound_operation_id not in service._pending:
            raise RequestRejected('thermal_operation_no_longer_pending')
        service.lease.check()
        context = service.backend.context()
        service._fresh(context, service.clock())
        if (context.printer_id, context.generation) != (self.policy.saved['printer_id'], self.policy.saved['generation']):
            raise RequestRejected('thermal_service_identity_changed')
        if service._pending and set(service._pending) != {allowed_operation}:
            raise RequestRejected('ordinary_operation_in_progress')

    def bind_resume_operation(self, operation_id):
        self._owner(operation_id)
        if self.bound_operation_id is not None:
            raise RequestRejected('thermal_operation_already_bound')
        with self.service.journal._db() as db:
            row=db.execute('SELECT request,status FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
        request=json.loads(row['request']) if row else {}
        if (not row or row['status']!='running' or request.get('operation')!='job.resume' or
                request.get('arguments')!={'job_id':self.policy.saved['job_id']}):
            raise RequestRejected('running_resume_operation_required')
        self.bound_operation_id=operation_id
        self._persist()

    def recheck_consumed(self, observation, *, now, allowed_operation=None):
        with self.service.backend.guard():
            try:
                self._owner(allowed_operation)
                if self.receipt_failure:
                    raise RequestRejected('thermal_receipt_storage_failed')
                observation = json.loads(self.service.journal._encode(observation))
                result = self.policy.recheck_consumed(observation,now=now)
                self._persist()
                return result
            except Exception:
                self.interrupt('thermal_resume_recheck_failed')
                raise

    def _persist(self, *, action=None, completed=None, unknown=None):
        journal = self.service.journal
        with journal._db() as db:
            db.execute('BEGIN IMMEDIATE')
            updated = db.execute('UPDATE thermal_sessions SET state=?,snapshot=? WHERE checkpoint_id=?', (
                self.policy.state, journal._encode({**self.policy.status(),'bound_operation_id':self.bound_operation_id}), self.policy.checkpoint_id))
            if updated.rowcount != 1:
                raise RequestRejected('thermal_session_record_missing')
            if action:
                db.execute('INSERT INTO thermal_effects VALUES (?,?,?,?,?)', (
                    action['effect_id'], self.policy.checkpoint_id, 'dispatching', journal._encode(action), '{}'))
            if completed:
                effect_id, observation = completed
                updated = db.execute("UPDATE thermal_effects SET status='completed',result=? WHERE effect_id=? AND status='dispatching'", (
                    journal._encode({'evidence_type':'correlated_target_observation', 'observation':observation}), effect_id))
                if updated.rowcount != 1:
                    raise RequestRejected('thermal_effect_record_not_dispatching')
            if unknown:
                db.execute("UPDATE thermal_effects SET status='outcome_unknown',result=? WHERE effect_id=? AND status='dispatching'", (
                    journal._encode({'reason':self.policy.reason, 'requires_recovery':True}), unknown))

    def advance(self, observation, *, now, request_resume=False):
        with self.service.backend.guard():
            if self.retired:
                raise RequestRejected('thermal_session_retired')
            try:
                self._owner()
                # Freeze nested caller data before policy and persistence use.
                observation = json.loads(self.service.journal._encode(observation))
            except Exception:
                self.policy.invalidate('thermal_owner_or_observation_unavailable')
                raise
            before = dict(self.policy.pending) if self.policy.pending else None
            result = self.policy.step(observation, now=now, request_resume=request_resume)
            action = result['action']
            completed = ((before['effect_id'], observation)
                if before and self.policy.pending is None and self.policy.state != 'invalid' else None)
            unknown = before['effect_id'] if before and self.policy.state == 'invalid' else None
            try:
                # Dispatching commits before callback; target receipt commits
                # before exposing the next policy transition to its caller.
                self._persist(action=action, completed=completed, unknown=unknown)
            except Exception:
                self.receipt_failure = True
                self.policy.invalidate('thermal_receipt_storage_failed')
                raise
            if action:
                try:
                    self.send_targets(dict(action))
                except Exception:
                    self.policy.invalidate('thermal_target_dispatch_interrupted')
                    try:
                        self._persist(unknown=action['effect_id'])
                    except Exception:
                        self.receipt_failure = True
                        raise
            return {**self.policy.status(), 'new_effect_id':action['effect_id'] if action else None,
                    'receipt_storage_failed':self.receipt_failure}

    def consume_resume(self, observation, *, now):
        with self.service.backend.guard():
            self._owner()
            if self.receipt_failure:
                raise RequestRejected('thermal_receipt_storage_failed')
            result = self.policy.consume_resume(observation, now=now)
            try:
                self._persist()
            except Exception:
                self.receipt_failure = True
                self.policy.invalidate('thermal_receipt_storage_failed')
                raise
            return result

    def interrupt(self, reason):
        # Called under the service guard before a validated cancel/stop dispatch.
        # Storage failure must not suppress the stop; in-memory invalidity still
        # blocks any later heater effect, and the prior durable session blocks restart.
        if self.retired:
            return
        self.policy.invalidate(reason)
        witness = getattr(self.service.backend, 'witness', None)
        if witness is not None:
            witness.revoke_thermal()
        try:
            self._persist(unknown=self.policy.pending['effect_id'] if self.policy.pending else None)
        except Exception:
            self.receipt_failure = True

    def effects(self):
        with self.service.backend.guard():
            self.service.lease.check()
            with self.service.journal._db() as db:
                return [{**dict(row), 'action':json.loads(row['action']), 'result':json.loads(row['result'])}
                        for row in db.execute('SELECT * FROM thermal_effects WHERE checkpoint_id=? ORDER BY rowid', (self.policy.checkpoint_id,))]
