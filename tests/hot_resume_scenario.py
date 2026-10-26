"""One actual sender job, software temperatures and virtual continuity proofs."""
import time
from printer_control.pause_effects import DurablePauseEffects
from printer_control.pause_monitor import ThermalPauseMonitor
from printer_control.pause_restore import InPlaceRestorePlan
from printer_control.restore_effects import DurableRestore
from printer_control.thermal_pause import ThermalPauseLimits,ThermalPausePolicy
from printer_control.virtual_thermal import VirtualThermalTransport
from hot_virtual_backend import HOT_JOB
from printer_control.tools import ToolRouter


def exercise(service,backend,device,logs,denied,*,temperature_loss=False,pause_count=1,pause_fault=None,managed=False,managed_timeout=False,managed_interrupt=None):
    def wait(predicate,label):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.01)
        raise AssertionError('hot simulation timeout: '+label)
    receipts=[]
    pause_status_trace=[]
    router=ToolRouter(service)
    def inspect_pause():
        value=router.call('job.pause_status',{})
        pause_status_trace.append(value)
        assert not value['resume_authorized']
        return value
    def operation(name):
        context=backend.context()
        args={'job_id':backend.job_id}
        if name=='job.stage':args['file_sha256']=backend.sha256
        request=dict(request_id=name+'-'+str(len(receipts)),printer_id=context.printer_id,
            connection_generation=context.generation,expected_state_version=context.state_version,
            deadline=backend.clock()+10,operation=name,arguments=args)
        receipt=service.submit(request)
        assert receipt['status']=='running',receipt
        def complete():
            result=service.poll(receipt['operation_id'])
            assert result['status'] in ('running','completed'),result
            return result['status']=='completed'
        wait(complete,name)
        result=service.poll(receipt['operation_id']);receipts.append(result)
        assert service.submit(request)==result
        return result
    operation('job.stage');operation('job.start')
    expected=[line for line in HOT_JOB.splitlines() if line.startswith('G1 ')]
    expected_trace=list(expected)
    cycles=[]
    for pause_index in range(pause_count):
        progress_target=len(device.completed_moves)+6
        wait(lambda:len(device.completed_moves)>=progress_target,'job progress before pause')
        paused=operation('job.pause')
        assert paused['result']['resumable'],paused
        assert inspect_pause()['phase']=='paused'
        saved=backend.checkpoint
        prefix=list(device.completed_moves)
        assert prefix==expected_trace[:len(prefix)] and len(prefix)<len(expected_trace)
        paused_cursor=saved.to_dict()['cursor']
        if cycles:
            from printer_control.journal import RequestRejected
            before_epoch=backend.witness.snapshot()[2]
            try:
                old_transport.send_targets(next(iter(old_transport.actions.values())))
            except RequestRejected:
                pass
            else:
                raise AssertionError('retired thermal transport accepted new work')
            try:
                old_monitor.request_resume()
            except ValueError:
                pass
            else:
                raise AssertionError('retired monitor accepted resume')
            assert backend.witness.snapshot()[2]==before_epoch
        started=time.monotonic()
        limits=ThermalPauseLimits(short_seconds=.2,max_pause_seconds=15,standby_nozzle=100,
            max_nozzle=250,max_bed=90,effect_timeout_seconds=2,reheat_timeout_seconds=5,
            stable_seconds=.1,tolerance_celsius=2,max_observation_gap_seconds=1)
        policy=ThermalPausePolicy(saved,limits,started_at=started)
        backend.thermal_policy=policy
        transport=VirtualThermalTransport(backend,limits,clock=time.monotonic)
        thermal=DurablePauseEffects(service,policy,transport.send_targets)
        if pause_fault=='missing_receipt':
            transport.observe=lambda line:None
        def sample(at):
            snapshot=transport.snapshot()
            return backend.thermal_observation(at,effect_id=snapshot['effect_id'])
        effect_write_offset=len(logs)
        with ThermalPauseMonitor(thermal,sample,interval=.02) as monitor:
            if pause_fault=='missing_receipt':
                from hot_pause_faults import verify_fault
                before=backend.context()
                wait(lambda:policy.state=='invalid','missing target receipt invalidation')
                return verify_fault(service,backend,device,logs,denied,thermal,monitor,prefix,
                    fault=pause_fault,before=before,write_offset=len(logs),effect_write_offset=effect_write_offset)
            def state(name):
                assert policy.state!='invalid',policy.status()
                return policy.state==name
            wait(lambda:state('standby'),'standby')
            assert inspect_pause()['phase']=='standby'
            if pause_fault in ('disconnect','reset'):
                from hot_pause_faults import verify_fault
                before=backend.context()
                write_offset=len(logs)
                device._debugTrigger('reset' if pause_fault=='reset' else 'drop_connection')
                wait(lambda:policy.state=='invalid','standby loss invalidation')
                return verify_fault(service,backend,device,logs,denied,thermal,monitor,prefix,
                    fault=pause_fault,before=before,write_offset=write_offset,effect_write_offset=effect_write_offset)
            assert device.completed_moves==prefix
            device.temp[0]=100
            if managed:
                context=backend.context()
                request=dict(request_id='managed-resume-'+str(pause_index),printer_id=context.printer_id,
                    connection_generation=context.generation,expected_state_version=context.state_version,
                    deadline=backend.clock()+(.5 if managed_timeout else 10),operation='job.resume',arguments={'job_id':backend.job_id})
                receipt=router.call('job.resume',{k:v for k,v in request.items() if k!='operation'})
                assert receipt['status']=='running',receipt
                workflow=service._resume_workflow
                assert service.submit(request)==receipt
                wait(lambda:state('reheating'),'managed reheat')
                if managed_interrupt:
                    if managed_interrupt.startswith(('cancel','stop')):
                        backend.drop_shutdown_marker=managed_interrupt=='cancel_missing_marker'
                        if managed_interrupt=='stop_lost':
                            device.shared_frame_writer.write_job=lambda data:len(data)
                        context=backend.context()
                        cancel=service.submit(dict(request_id='interrupt-cancel',printer_id=context.printer_id,
                            connection_generation=context.generation,expected_state_version=context.state_version,
                            deadline=backend.clock()+2,operation='printer.stop' if managed_interrupt.startswith('stop') else 'job.cancel',
                            arguments={} if managed_interrupt.startswith('stop') else {'job_id':backend.job_id}))
                        assert cancel['status']=='running',cancel
                        wait(lambda:workflow.done.is_set(),'cancelled resume worker')
                        result=service.poll(receipt['operation_id'])
                        assert result['status']=='outcome_unknown',result
                        assert service.submit(request)==result
                        terminal='outcome_unknown' if backend.drop_shutdown_marker or managed_interrupt=='stop_lost' else 'completed'
                        wait(lambda:service.poll(cancel['operation_id'])['status']==terminal,'cancel shutdown')
                        cancel_result=service.poll(cancel['operation_id'])
                        if managed_interrupt!='stop_lost':assert device.targetTemp[0]==0 and device.bedTargetTemp==0
                        else:assert device.targetTemp[0]>0 or device.bedTargetTemp>0
                    else:
                        service.close()
                        result=service.journal.get(receipt['operation_id'])
                        assert service.lease._fd is None and backend._closed
                    assert workflow.done.is_set() and not workflow.thread.is_alive()
                    assert monitor.closed and not monitor.thread.is_alive()
                    assert policy.state=='invalid' and not backend.witness.allow_job_commands
                    device.temp[0]=200
                    time.sleep(.1)
                    assert device.completed_moves==prefix and service._restore is None
                    return dict(physical_serial_open_attempts=len(denied),resume_receipt=result,
                        interruption=managed_interrupt,service_closed=managed_interrupt=='shutdown',
                        worker_stopped=True,monitor_closed=True,new_job_moves_after_fault=0,
                        cancel_receipt=cancel_result if managed_interrupt.startswith(('cancel','stop')) else None,
                        heater_shutdown_confirmed=managed_interrupt in ('cancel','stop'),thermal_state=policy.state)
                if managed_timeout:
                    wait(lambda:workflow.done.is_set(),'managed deadline')
                    result=service.poll(receipt['operation_id'])
                    assert result['status']=='outcome_unknown',result
                    assert service.submit(request)==result
                    assert device.completed_moves==prefix and service._restore is None
                    assert monitor.closed and not backend.witness.allow_job_commands
                    return dict(physical_serial_open_attempts=len(denied),resume_receipt=result,
                        job_still_paused=backend.sender.getState()==backend.sender.STATE_PAUSED,
                        new_job_moves_after_fault=0,thermal_state=policy.state,monitor_closed=monitor.closed)
                device.temp[0]=200
                def finished():
                    result=service.poll(receipt['operation_id'])
                    assert result['status'] in ('running','completed'),result
                    return result['status']=='completed'
                wait(finished,'managed resume')
                resumed=service.poll(receipt['operation_id'])
                receipts.append(resumed)
                assert service.submit(request)==resumed
                restore=workflow.restore
                plan=restore.plan
                assert workflow.retired and workflow.done.is_set()
            else:
                monitor.request_resume()
                wait(lambda:state('reheating'),'reheating')
                assert not policy.status()['resume_ready']
                device.temp[0]=200
                wait(lambda:state('ready'),'stable reheat')
                assert inspect_pause()['phase']=='thermal_ready_restore_required'
                monitor.consume_resume()
        if not managed:
            assert device.completed_moves==prefix
            plan=InPlaceRestorePlan(saved,backend._capture(),now=backend.clock(),max_observation_age=1)
            epoch=backend.witness.snapshot()[2]
            commands=plan.commands+('M400','M118 E1 '+plan.marker)
            def send(plan,expected_epoch):
                tag=backend.witness.authorize_restore(plan,expected_epoch,now=backend.clock())
                for command in commands:
                    assert backend.sender.sendCommand(command,tags={tag,'source:hot-restore-test'})
            restore=DurableRestore(service,plan,send,expected_epoch=epoch)
            restore.dispatch(now=backend.clock())
            wait(lambda:plan.marker in backend.restore_markers,'restore barrier marker')
            current=backend._capture()
            restore.complete(current,marker=plan.marker,barrier_completed_at=backend.restore_markers[plan.marker],now=backend.clock())
            assert inspect_pause()['phase']=='restored'
            assert current.to_dict()['cursor']==paused_cursor
            assert current.to_dict()['position']==saved.to_dict()['position']
            assert device.completed_moves==prefix+[plan.commands[-1]]
            if temperature_loss:
                device.temp[0]=180
                context=backend.context()
                denied_resume=service.submit(dict(request_id='lost-temperature-resume',printer_id=context.printer_id,
                    connection_generation=context.generation,expected_state_version=context.state_version,
                    deadline=backend.clock()+10,operation='job.resume',arguments={'job_id':backend.job_id}))
                assert denied_resume['status'] in ('failed','rejected'),denied_resume
                time.sleep(.1)
                assert device.completed_moves==prefix+[plan.commands[-1]]
                assert backend.sender.getState()==backend.sender.STATE_PAUSED
                assert not backend.witness.allow_job_commands and policy.state=='invalid'
                assert not denied
                return dict(evidence_type='extended_virtual_hot_resume_temperature_loss',
                    physical_qualified=False,physical_serial_open_attempts=len(denied),
                    resume_receipt=denied_resume,job_still_paused=True,new_job_moves_after_temperature_loss=0,
                    thermal_state=policy.state,restoration_invalidated=restore.invalidated)
        expected_trace.insert(len(prefix),plan.commands[-1])
        if not managed:resumed=operation('job.resume')
        pause_duration=time.monotonic()-started
        assert restore.status=='resumed' and service._restore is None
        assert inspect_pause()['phase']=='inactive'
        assert monitor.closed and thermal.retired and transport.retired
        assert service._thermal_monitor is None and service._thermal_pause is None
        assert backend.thermal_transport is None
        cycles.append(dict(checkpoint_id=saved.id,cursor=paused_cursor,
            paused_model_moves=len(prefix)-pause_index,pause_duration_seconds=pause_duration,
            thermal_effects=thermal.effects(),restore_receipt=restore.receipt(),
            monitor_closed=monitor.closed,thermal_retired=thermal.retired,transport_retired=transport.retired))
        old_transport,old_monitor=transport,monitor
    wait(lambda:backend.inspect()['job_state']=='completed','full job completion')
    assert device.completed_moves==expected_trace
    assert len({c['checkpoint_id'] for c in cycles})==pause_count
    assert all(a['cursor']<b['cursor'] for a,b in zip(cycles,cycles[1:]))
    assert device._lastX==80 and device._lastE[0]==80
    assert device.targetTemp[0]==0 and device.bedTargetTemp==0
    assert not denied
    return dict(evidence_type='extended_virtual_streamed_hot_resume',physical_qualified=False,
        scripted_temperatures=True,virtual_continuity_proofs=True,physical_serial_open_attempts=len(denied),
        paused_cursor=paused_cursor,paused_model_moves=len(prefix),expected_job_moves=len(expected),
        pause_status_trace=pause_status_trace,restoration_only_moves=pause_count,pause_count=pause_count,cycles=cycles,all_job_moves_once_in_order=True,final_position=[device._lastX,device._lastY,device._lastZ,device._lastE[0]],
        thermal_effects=[effect for cycle in cycles for effect in cycle['thermal_effects']],restore_receipt=restore.receipt(),operation_receipts=receipts,
        heater_targets_zero=True,pause_duration_seconds=sum(c['pause_duration_seconds'] for c in cycles))
