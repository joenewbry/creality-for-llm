"""Opt-in simulated restoration over the real sender; no streamed hot job yet."""
import time
from printer_control.checkpoint import PauseCheckpoint
from printer_control.pause_restore import InPlaceRestorePlan
from printer_control.restore_effects import DurableRestore
from test_checkpoint import data


def exercise(service, backend, device, logs, denied):
    def wait_marker(marker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if any(line.strip() == 'Recv: echo:' + marker for line in logs):
                return
            time.sleep(.01)
        raise AssertionError('restoration marker not observed: ' + marker)

    witness = backend.witness
    witness.start_job()
    initialization = ['G21','G90','M82','T0','M106 S0','M104 S0','M140 S0',
                      'G92 X10 Y20 Z0.2 E3','G1 F600','M220 S100','M221 T0 S100',
                      'M400','M118 E1 RESTORE_FIXTURE_READY']
    for command in initialization:
        assert backend.sender.sendCommand(command)
    wait_marker('RESTORE_FIXTURE_READY')
    epoch = witness.snapshot()[2]
    assert witness.freeze(epoch)
    saved = data()
    context = backend.context()
    saved.update(printer_id=context.printer_id,generation=context.generation,host_session=backend.host_session)
    saved['modal'].update(feedrate_percent=95, flow_percent=105)
    original = PauseCheckpoint(saved)
    current = data()
    current.update({key:saved[key] for key in ('printer_id','generation','host_session')})
    current['created_at'] = 20
    current['modal'].update(e_relative=False, fan_pwm=0, feedrate=600)
    plan = InPlaceRestorePlan(original, PauseCheckpoint(current), now=20, max_observation_age=1)
    commands = plan.commands + ('M400', 'M118 E1 ' + plan.marker)
    before_count = len(device.completed_moves)
    before_position = [device._lastX,device._lastY,device._lastZ,device._lastE[0]]
    def send_plan(plan, expected_epoch):
        assert driver.receipt()['status'] == 'dispatching'
        tag = witness.authorize_restore(plan, expected_epoch, now=20)
        for command in commands:
            assert backend.sender.sendCommand(command,tags={tag,'source:restore-fixture'})
    driver = DurableRestore(service,plan,send_plan,expected_epoch=epoch)
    driver.dispatch(now=20)
    wait_marker(plan.marker)
    observed = original.to_dict()
    observed['created_at'] = 22
    observed['position'] = dict(zip('xyze', [device._lastX,device._lastY,device._lastZ,device._lastE[0]]))
    observed['modal'].update(xyz_relative=device._relative,e_relative=device.e_relative,
        feedrate=device._lastF,fan_pwm=device._fan_pwm,feedrate_percent=device._feedrate_multiplier,
        flow_percent=device._flowrate_multiplier,nozzle_target=device.targetTemp[0],bed_target=device.bedTargetTemp)
    # Only modal/coordinate fields above are observed. Identity/continuity proof
    # fields are fixture assumptions; this does not qualify a resumable hot job.
    receipt = driver.complete(PauseCheckpoint(observed),marker=plan.marker,now=22,barrier_completed_at=21)
    assert receipt['status'] == 'completed' and not receipt['resume_authorized']
    assert before_position == list(observed['position'].values())
    assert device.completed_moves[before_count:] == ['G1 F1200']
    assert witness.frozen and not witness.allow_job_commands
    assert not witness.arm_resume(epoch)
    assert not denied
    return dict(evidence_type='extended_virtual_serial_restoration',
        simulation_extension='MarlinModalMixin', physical_serial_open_attempts=len(denied),
        physical_qualified=False, streamed_hot_print_resume_tested=False,
        restoration_receipt_status=receipt['status'],resume_authorized=receipt['resume_authorized'],
        plan_id=plan.id, commands=list(commands), observed_modal=observed['modal'],
        position_before=before_position,position_after=list(observed['position'].values()),
        job_still_frozen=True, old_epoch_rejected=True, epoch_delta=witness.snapshot()[2]-epoch)
