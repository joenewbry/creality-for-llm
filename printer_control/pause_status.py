"""Read-only pause workflow summary. Reading cannot grant resume authority."""
from .journal import RequestRejected


def inspect_pause(service):
    # Caller holds the backend guard and service lease. This performs no device
    # reads/writes: backend.inspect consumes the adapter's existing observations.
    backend=service.backend
    snapshot=backend.inspect()
    context=backend.context()
    now=service.clock()
    blockers=[]
    try:
        service._fresh(context,now)
    except RequestRejected as exc:
        blockers.append(str(exc))
    history=service.journal.latest_pause_evidence()
    thermal=service._thermal_pause
    restore=service._restore
    monitor=service._thermal_monitor
    checkpoint=getattr(backend,'checkpoint',None)
    saved=checkpoint.to_dict() if checkpoint is not None else None
    checkpoint_id=checkpoint.id if checkpoint is not None else None
    if saved is None and restore is not None:
        saved=restore.plan.saved.to_dict()
        checkpoint_id=restore.plan.saved.id
    elif saved is None and thermal is not None:
        saved=thermal.policy.saved
        checkpoint_id=thermal.policy.checkpoint_id

    phase='paused' if snapshot.get('job_state')=='paused' else 'inactive'
    if thermal is not None:
        phase={'short_pause':'holding','entering_standby':'entering_standby','standby':'standby',
               'requesting_reheat':'requesting_reheat','reheating':'reheating',
               'ready':'thermal_ready_restore_required','consumed':'restoration_required',
               'invalid':'recovery_required'}[thermal.policy.state]
        if thermal.policy.reason:blockers.append(thermal.policy.reason)
        if thermal.receipt_failure:blockers.append('thermal_receipt_storage_failed')
    if restore is not None:
        phase={'prepared':'restoration_prepared','dispatching':'restoring','completed':'restored',
               'resume_dispatching':'resuming','resumed':'inactive','outcome_unknown':'recovery_required'}[restore.status]
        if restore.invalidated:blockers.append(restore.invalidated)
        if restore.receipt_failure:blockers.append('restore_receipt_storage_failed')
    if service._thermal_recovery_required or service._restore_recovery_required:
        blockers.append('prior_pause_requires_recovery')
    if snapshot.get('blocking_fault'):
        blockers.append('observed_sender_fault_requires_recovery')
    if blockers:
        phase='recovery_required'

    monitor_closed=(monitor is None or (monitor.closed and
                    (monitor.thread is None or not monitor.thread.is_alive())))
    available=(not blockers and not service._pending and monitor_closed and (
        (restore is not None and restore.status=='completed' and
         callable(getattr(backend,'restored_resume_evidence',None)) and
         callable(getattr(backend,'dispatch_restored_resume',None))) or
        (restore is None and thermal is None and snapshot.get('resumable') is True)))
    workflow=service._resume_workflow
    managed_available=(not blockers and not service._pending and restore is None and thermal is not None
        and thermal.policy.state in ('short_pause','standby') and monitor is not None
        and not monitor.closed and monitor.thread is not None and monitor.thread.is_alive()
        and callable(getattr(backend,'make_resume_workflow',None)) and workflow is None)
    available=available or managed_available
    expires=saved['expires_at'] if saved is not None else None
    if expires is not None and (now<saved['created_at'] or now>=expires):
        blockers.append('pause_interval_expired_or_clock_reversed')
        phase,available='recovery_required',False
    if available:
        gate='fresh_recheck_required'
    elif phase=='recovery_required':
        gate='recovery_required'
    else:
        gate='not_ready'
    return dict(schema_version=1,printer_id=context.printer_id,connection_generation=context.generation,
        state_version=context.state_version,observed_at=context.observed_at,
        scope='pause_workflow_snapshot',continuous_capture=False,phase=phase,
        job_id=saved['job_id'] if saved else snapshot.get('job_id'),checkpoint_id=checkpoint_id,
        expires_at=expires,remaining_seconds=max(0,expires-now) if expires is not None else None,
        resume_request_available=available,resume_gate=gate,
        resume_authorized=False,blockers=sorted(set(blockers)),
        backend_resume_blockers=snapshot.get('resume_blockers',[]),
        pending_operation_ids=list(service._pending),
        resume_workflow=dict(active=workflow is not None,phase=workflow.phase if workflow else None,
                            operation_id=workflow.operation_id if workflow else None,
                            error=workflow.error if workflow else None),
        thermal=dict(active=thermal is not None,live_state=thermal.policy.state if thermal else None,
                     last_sample_monotonic=thermal.policy.last_sample if thermal else None,
                     durable_latest=history['thermal']),
        monitor=dict(installed=monitor is not None,running=bool(monitor and monitor.thread and monitor.thread.is_alive()),
                     samples=monitor.samples if monitor else 0,error=monitor.error if monitor else None),
        restoration=dict(active=restore is not None,live_state=restore.status if restore else None,
                         durable_latest=history['restoration']),physical_pause_qualified=False)
