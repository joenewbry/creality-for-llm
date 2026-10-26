"""Fault assertions during actual paused hot software job; no physical effects."""
import re
import time


def verify_fault(service,backend,device,logs,denied,thermal,monitor,prefix,*,fault,before,write_offset,effect_write_offset):
    monitor.close()
    assert thermal.policy.state=='invalid'
    if fault=='reset':
        # Allow only the sender's normal reset handshake to settle; our policy
        # must not interpret that handshake as resumed job authority.
        deadline=time.monotonic()+5
        while not any('External reset?' in line for line in logs):
            assert time.monotonic()<deadline,'reset not observed by sender'
            time.sleep(.01)
        time.sleep(.3)
    after=backend.inspect()
    from printer_control.tools import ToolRouter
    pause_status=ToolRouter(service).call('job.pause_status',{})
    assert pause_status['phase']=='recovery_required'
    assert not pause_status['resume_request_available'] and not pause_status['resume_authorized']
    if fault!='missing_receipt':
        assert after['connection_generation']!=before.generation
        assert after['job_state']=='outcome_unknown'
    rejected=[]
    for index,context in enumerate((before,backend.context())):
        request=dict(request_id='fault-resume-'+str(index),printer_id=context.printer_id,
            connection_generation=context.generation,expected_state_version=context.state_version,
            deadline=backend.clock()+10,operation='job.resume',arguments={'job_id':backend.job_id})
        receipt=service.submit(request)
        assert receipt['status']=='rejected',receipt
        assert service.submit(request)==receipt
        rejected.append(receipt)
    time.sleep(.1)
    assert device.completed_moves==prefix
    assert not backend.witness.allow_job_commands
    writes=[line for line in logs[write_offset:] if line.startswith('Send:')]
    assert not any(re.search(r'\b(?:G0|G1|M104|M140)\s',line) for line in writes),writes
    target_writes=[]
    for line in logs[effect_write_offset:]:
        if not line.startswith('Send:'):continue
        match=re.search(r'\bM(104|140) S([0-9.]+)',line)
        if match:target_writes.append((int(match[1]),float(match[2])))
    assert target_writes==[(104,100.),(140,60.)],target_writes
    effects=thermal.effects()
    assert len(effects)==1
    assert effects[0]['status']==('outcome_unknown' if fault=='missing_receipt' else 'completed')
    assert not denied
    return dict(evidence_type='extended_virtual_hot_pause_fault',fault=fault,
        physical_qualified=False,physical_serial_open_attempts=len(denied),pause_status=pause_status,
        scripted_temperatures=True,virtual_continuity_proofs=True,
        job_moves_before=len(prefix),new_job_moves_after_fault=0,new_heater_commands_after_invalidation=0,standby_target_writes=len(target_writes),
        measurement_start='policy_invalidation' if fault=='missing_receipt' else 'connection_fault_injection',
        thermal_state=thermal.policy.state,thermal_reason=thermal.policy.reason,
        monitor_closed=monitor.closed,monitor_alive=monitor.thread.is_alive(),
        thermal_effects=effects,rejected_resumes=rejected,
        generation_changed=after['connection_generation']!=before.generation,
        automatic_reconnect_attempted=False,heater_shutdown_confirmed=False)
