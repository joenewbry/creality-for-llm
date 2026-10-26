"""Durable operation dispatch under one process owner and one backend state lock.

The backend owns transport and completion criteria. Merely returning from
dispatch never completes an operation. This service does not discover devices.
"""
from dataclasses import dataclass, asdict
import math
from pathlib import Path
import time
import uuid
from typing import Protocol, ContextManager

from .journal import Journal, RequestRejected
from .ownership import ControllerLease


@dataclass(frozen=True)
class Context:
    printer_id: str
    generation: str
    state_version: int
    observed_at: float


@dataclass(frozen=True)
class Outcome:
    status: str
    detail: dict


class Backend(Protocol):
    """guard serializes state checks and dispatch with all other command writers.

    Methods must not wait for physical completion while holding guard. poll
    returns None while pending, or explicit evidence, never inferred from a
    successful write. The backend must reject unsupported operations, unknown
    argument fields, unsafe states, and physical profiles it cannot qualify.
    """
    def guard(self) -> ContextManager: ...
    def context(self) -> Context: ...
    def validate(self, operation: str, arguments: dict) -> None: ...
    def dispatch(self, operation_id: str, operation: str, arguments: dict, *, deadline: float) -> None: ...
    def poll(self, operation_id: str) -> Outcome | None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class StartupState:
    has_history: bool
    recovered_operations: int
    unknown_operations: int
    thermal_recovery_required: bool
    restoration_recovery_required: bool
    previous_backend_attempts: int


@dataclass(frozen=True)
class StartupAdmission:
    evidence_id: str
    prior_state_reconciled: bool


class OperationService:
    def __init__(self, journal_path, backend: Backend, *, clock=time.time, max_age=5.0):
        self.backend = backend
        self._initialize(journal_path, clock=clock, max_age=max_age)

    def _initialize(self, journal_path, *, clock, max_age):
        if type(max_age) not in (int, float) or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError('max_age must be positive and finite')
        # Resolve aliases so two spellings of one journal use the same lock.
        path = Path(journal_path).resolve()
        self.lease = ControllerLease(str(path) + '.owner').acquire()
        self.clock, self.max_age = clock, max_age
        self.startup_state = self.startup_admission = None
        self._pending = {}
        self._thermal_pause = None
        self._thermal_monitor = None
        self._thermal_recovery_required = False
        self._restore = None
        self._restore_recovery_required = False
        self._resume_workflow = None
        try:
            self.journal = Journal(path)
            with self.journal._db() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='camera_binding'").fetchone():
                    raise RequestRejected('camera_journal_cannot_control_printer')
            self.recovered = self.journal.recover_after_restart(now=self.clock())
            self._thermal_recovery_required = self.journal.has_unreconciled_thermal_pause()
            self._restore_recovery_required = self.journal.has_unreconciled_restore()
        except BaseException:
            self.lease.close()
            raise

    @classmethod
    def from_backend_factory(cls, journal_path, backend_factory, *, readiness_check,
                             clock=time.time, max_age=5.0):
        """Acquire/recover the journal before any backend construction.

        readiness_check is trusted operator-side code, not an LLM approval tool.
        It must establish assignment, hardware/recovery and current-state facts
        without opening a device. An evidence ID is a reference, not proof of
        physical readiness. Factories own cleanup of partially created resources.
        Historical receipts never become live resume authority here.
        """
        if not callable(backend_factory) or not callable(readiness_check):
            raise TypeError('backend factory and readiness check required')
        service = cls.__new__(cls)
        service.backend = None
        service._initialize(journal_path, clock=clock, max_age=max_age)
        try:
            with service.journal._db() as db:
                db.execute('CREATE TABLE IF NOT EXISTS backend_startups (attempt_id TEXT PRIMARY KEY, recorded_at REAL NOT NULL, evidence TEXT NOT NULL)')
                attempts = db.execute('SELECT count(*) FROM backend_startups').fetchone()[0]
                count = db.execute('SELECT count(*) FROM operations').fetchone()[0]
                unknown = db.execute("SELECT count(*) FROM operations WHERE status='outcome_unknown'").fetchone()[0]
                observations = db.execute('SELECT count(*) FROM observations').fetchone()[0]
            state = StartupState(bool(count or observations or attempts), service.recovered, unknown,
                service._thermal_recovery_required, service._restore_recovery_required, attempts)
            service.startup_state = state
            service.lease.check()
            admission = readiness_check(state)
            if (type(admission) is not StartupAdmission or type(admission.evidence_id) is not str
                    or not 1 <= len(admission.evidence_id) <= 128 or not admission.evidence_id.strip()
                    or type(admission.prior_state_reconciled) is not bool):
                raise RequestRejected('invalid_startup_admission')
            if (state.has_history or state.thermal_recovery_required or state.restoration_recovery_required) and not admission.prior_state_reconciled:
                raise RequestRejected('startup_requires_reconciliation')
            service.lease.check()  # a slow readiness check may have lost ownership
            service.startup_admission = admission
            # Durable intent precedes construction: a crash during an open may
            # leave no ordinary operation receipt but still affect the machine.
            now = service.clock()
            Journal._time(now)
            evidence = Journal._encode({'startup_state': asdict(state), 'admission': asdict(admission)})
            with service.journal._db() as db:
                db.execute('INSERT INTO backend_startups VALUES (?,?,?)', (uuid.uuid4().hex, now, evidence))
            service.lease.check()  # persistence may also outlive ownership
            backend = backend_factory(service.lease, state, admission)
            if backend is None:
                raise TypeError('backend factory returned no backend')
            service.backend = backend
            return service
        except BaseException:
            service.lease.close()
            raise

    def _fresh(self, context, now):
        Journal._time(now)
        if type(context.observed_at) not in (int, float) or not math.isfinite(context.observed_at):
            raise RequestRejected('invalid_observation_time')
        if not 0 <= now - context.observed_at <= self.max_age:
            raise RequestRejected('stale_observation')

    def submit(self, request):
        # Freeze caller-owned dictionaries before validation or asynchronous use.
        import json
        request = json.loads(Journal.validate(request))
        with self.backend.guard():
            self.lease.check()
            context = self.backend.context()
            now = self.clock()
            receipt = self.journal.accept(request, now=now, printer_id=context.printer_id,
                                          generation=context.generation, state_version=context.state_version)
            if receipt['status'] != 'accepted':
                return receipt
            op = receipt['operation_id']
            restored_resume = (self._restore is not None and request['operation'] == 'job.resume'
                               and self._restore.status == 'completed')
            managed_resume = (request['operation']=='job.resume' and self._restore is None
                and self._thermal_pause is not None and not self._thermal_recovery_required
                and not self._restore_recovery_required
                and callable(getattr(self.backend,'make_resume_workflow',None)))
            try:
                if (self._restore is not None or self._restore_recovery_required) and not restored_resume and request['operation'] not in ('job.cancel', 'printer.stop'):
                    return self.journal.finish(op, 'rejected', {'reason': 'restore_requires_resume_integration_or_recovery'}, now=self.clock())
                thermal_active = self._thermal_pause is not None and (self._thermal_pause.policy.state != 'consumed' or self._thermal_pause.receipt_failure)
                if (thermal_active or self._thermal_recovery_required) and not managed_resume and request['operation'] not in ('job.cancel', 'printer.stop'):
                    return self.journal.finish(op, 'rejected', {'reason': 'thermal_pause_requires_completion_or_recovery'}, now=self.clock())
                overlap = getattr(self.backend, 'can_overlap', None)
                allowed_overlap = bool(self._pending and callable(overlap) and
                                       overlap(request['operation'], tuple(self._pending)) is True)
                if self._pending and not allowed_overlap and request['operation'] not in ('job.cancel', 'printer.stop'):
                    return self.journal.finish(op, 'rejected', {
                        'reason': 'operation_in_progress',
                        'pending_operation_ids': list(self._pending),
                        'next_action': 'Poll each pending operation with operation.get before submitting another operation.',
                    }, now=self.clock())
                self._fresh(context, now)
                if restored_resume:
                    self._restore.validate_resume(request['arguments'])
                elif managed_resume:
                    if self._resume_workflow is not None:
                        raise RequestRejected('resume_workflow_already_exists')
                    self.backend.validate_managed_resume(request['arguments'])
                else:
                    self.backend.validate(request['operation'], request['arguments'])
            except RequestRejected as exc:
                return self.journal.finish(op, 'rejected', {'reason': str(exc)}, now=self.clock())
            except Exception:
                return self.journal.finish(op, 'failed', {'reason': 'validation_unavailable', 'dispatched': False}, now=self.clock())
            if not self.journal.claim(op, now=self.clock()):
                return self.journal.get(op)
            self._pending[op] = (context.printer_id, context.generation)
            try:
                if self._resume_workflow is not None and request['operation'] in ('job.cancel','printer.stop'):
                    self._resume_workflow.interrupt('interrupted_by_'+request['operation'])
                if self._restore is not None and request['operation'] in ('job.cancel', 'printer.stop'):
                    self._restore.interrupt('interrupted_by_' + request['operation'])
                if self._thermal_pause is not None and request['operation'] in ('job.cancel', 'printer.stop'):
                    self._thermal_pause.interrupt('interrupted_by_' + request['operation'])
                if restored_resume:
                    self._restore.dispatch_resume(op,request['arguments'],deadline=request['deadline'])
                elif managed_resume:
                    self._resume_workflow=self.backend.make_resume_workflow(self,op,request['deadline'])
                    self._resume_workflow.start()
                else:
                    self.backend.dispatch(op, request['operation'], request['arguments'], deadline=request['deadline'])
            except Exception:
                # Bytes may already have been emitted. Never retry dispatch.
                if managed_resume and self._resume_workflow is not None:
                    self._resume_workflow.interrupt('managed_resume_start_failed')
                self._pending.pop(op, None)
                return self.journal.finish(op, 'outcome_unknown', {'reason': 'dispatch_interrupted'}, now=self.clock())
            return self.journal.get(op)

    def poll(self, operation_id):
        with self.backend.guard():
            self.lease.check()
            receipt = self.journal.get(operation_id)
            if receipt['status'] != 'running':
                return receipt
            context = self.backend.context()
            now = self.clock()
            identity = self._pending.get(operation_id)
            workflow=self._resume_workflow
            managed=workflow is not None and workflow.operation_id==operation_id
            if identity != (context.printer_id, context.generation):
                outcome = Outcome('outcome_unknown', {'reason': 'connection_changed'})
            elif managed and workflow.error:
                outcome=Outcome('outcome_unknown',{'reason':workflow.error})
            elif managed and not workflow.done.is_set():
                return receipt
            else:
                try:
                    self._fresh(context, now)
                except RequestRejected:
                    # Staleness is not proof of completion or failure. A future
                    # fresh observation may reconcile the pending operation.
                    return receipt
                outcome = self.backend.poll(operation_id)
                if outcome is None:
                    return receipt
            if outcome.status not in {'completed', 'failed', 'outcome_unknown'}:
                raise RequestRejected('Invalid backend terminal outcome')
            if type(outcome.detail) is not dict or not outcome.detail:
                raise RequestRejected('Backend outcome requires evidence')
            if managed and outcome.status!='completed':
                workflow.interrupt('resume_workflow_outcome_unconfirmed')
            restore = self._restore
            if restore is not None and restore.resume_operation_id == operation_id:
                if restore.invalidated:
                    outcome = Outcome('outcome_unknown',{'reason':'restored_resume_invalidated'})
                restore.finish_resume(operation_id,outcome)
            result = self.journal.finish(operation_id, outcome.status, outcome.detail, now=now)
            self._pending.pop(operation_id, None)
            if restore is not None and restore.status == 'resumed':
                self._restore = None
                if managed:
                    workflow.retired=True
                    self._resume_workflow=None
            return result

    def close(self):
        if self._resume_workflow is not None:
            self._resume_workflow.close()
        if self._thermal_monitor is not None:
            self._thermal_monitor.close()
        with self.backend.guard():
            self.lease.check()
            if self._restore is not None:
                self._restore.interrupt('service_closed')
            # Do not permit a successor while the old transport can still emit
            # commands. If shutdown fails, keep ownership and surface the error.
            self.backend.close()
            self.lease.close()

    def __enter__(self):
        self.lease.check()
        return self

    def __exit__(self, *args):
        self.close()
