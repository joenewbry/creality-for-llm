"""Serial target routing with scripted thermal samples, never a hot print test."""
import time

from printer_control.checkpoint import PauseCheckpoint
from printer_control.journal import RequestRejected
from printer_control.pause_effects import DurablePauseEffects
from printer_control.thermal_pause import ThermalPauseLimits, ThermalPausePolicy
from printer_control.virtual_thermal import VirtualThermalTransport


def exercise(service, backend, device, logs, denied, *, missing_receipt=False, continuous=False):
    # Fixture setup explicitly seeds a hot, idle software model. This does not
    # alter cold job preflight or pretend a real streamed hot job was paused.
    device._simulateTemps = lambda *a, **k: None
    device.temp[0], device.targetTemp[0] = 200., 200.
    device.bedTemp, device.bedTargetTemp = 60., 60.
    limits = ThermalPauseLimits(short_seconds=3., max_pause_seconds=30., standby_nozzle=100.,
        max_nozzle=250., max_bed=90., effect_timeout_seconds=3., reheat_timeout_seconds=10.,
        stable_seconds=2., tolerance_celsius=2., max_observation_gap_seconds=2.)
    # Exercise the same frozen command witness used by a paused stream. This
    # remains an idle fixture; no hot print or parking behavior is inferred.
    backend.witness.start_job()
    frozen_epoch = backend.witness.snapshot()[2]
    assert backend.witness.freeze(frozen_epoch)
    context = backend.context()
    checkpoint = PauseCheckpoint(dict(schema_version=1, printer_id=context.printer_id,
        generation=context.generation, host_session=backend.host_session, job_id=backend.job_id,
        file_sha256=backend.sha256, firmware_id='OctoPrint-1.11.8-virtual-scripted-thermal',
        config_sha256='a'*64, cursor=0, next_line_number=0, created_at=10., expires_at=40.,
        position=dict(x=0.,y=0.,z=0.,e=0.), offsets=dict(x=0.,y=0.,z=0.,e=0.),leveling_active=False,
        evidence={k:True for k in PauseCheckpoint.PROOFS},
        modal=dict(units='mm',xyz_relative=False,e_relative=False,feedrate=600.,tool=0,
            fan_pwm=0.,nozzle_target=200.,bed_target=60.,feedrate_percent=100.,flow_percent=100.)))
    if continuous:
        from dataclasses import replace
        limits = replace(limits, short_seconds=.15, max_pause_seconds=5., effect_timeout_seconds=1.,
                         reheat_timeout_seconds=2., stable_seconds=.1, max_observation_gap_seconds=.5)
    started_at = time.monotonic() if continuous else 0.
    policy = ThermalPausePolicy(checkpoint, limits, started_at=started_at)
    now=[0.]
    transport = VirtualThermalTransport(backend,limits,clock=time.monotonic if continuous else lambda:now[0])
    if missing_receipt:
        transport.observe = lambda line: None  # drop evidence, not the serial target commands
    rejected = []
    template = dict(effect_id='b'*32, kind='set_pause_targets', nozzle_target=100., bed_target=60., deadline=started_at+3.)
    for change in ({'nozzle_target':251.}, {'bed_target':91.}, {'nozzle_target':100.5},
                   {'nozzle_target':True}, {'deadline':0.}, {'effect_id':'M112'}, {'extra':1}):
        try:
            transport.send_targets({**template, **change})
        except RequestRejected as exc:
            rejected.append(str(exc))
        else:
            raise AssertionError('invalid target effect was accepted')
    assert not transport.actions
    driver = DurablePauseEffects(service,policy,transport.send_targets)
    history=[]

    def sample(at):
        snapshot=transport.snapshot()
        return {**{k:policy.saved[k] for k in policy.IDENTITY}, 'checkpoint_id':policy.checkpoint_id,
            'sample_at':at, **{k:True for k in policy.PROOFS}, 'motor_hold_until':started_at+31.,
            **{k:snapshot[k] for k in ('nozzle_actual','nozzle_target','bed_actual','bed_target','effect_id')}}

    if continuous:
        from printer_control.pause_monitor import ThermalPauseMonitor
        def wait_state(state):
            deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                with backend.guard():
                    current=policy.state
                if current==state:return
                assert current!='invalid', policy.status()
                time.sleep(.01)
            raise AssertionError('monitor state timeout: '+state)
        with ThermalPauseMonitor(driver,sample,interval=.02) as monitor:
            # No driver.advance or printer-status calls: the worker alone must
            # cross the short-pause threshold and confirm the serial effect.
            wait_state('standby')
            device.temp[0]=100.
            monitor.request_resume()
            wait_state('reheating')
            assert not policy.status()['resume_ready']
            device.temp[0]=200.
            wait_state('ready')
            receipt=monitor.consume_resume()
            assert receipt['restoration_still_required']
        sends=[x for x in logs if x.startswith('Send:') and ('M104 S' in x or 'M140 S' in x)]
        assert len(sends)==4 and monitor.samples>=5
        assert all(e['status']=='completed' for e in driver.effects())
        assert not denied and not device.completed_moves
        return {'scenario':'thermal_serial_monitor','monitor_samples':monitor.samples,
            'worker_alive_after_close':monitor.thread.is_alive(), 'effects':driver.effects(),
            'physical_serial_open_attempts':len(denied),'executed_moves':len(device.completed_moves),
            'target_command_sends':sends,'temperature_samples':'scripted virtual fixture',
            'streamed_hot_print_resume_tested':False,'physical_thermal_qualified':False}

    def tick(at,resume=False):
        now[0]=at
        result=driver.advance(sample(at),now=at,request_resume=resume)
        history.append(result)
        return result

    def receipt_on_wire():
        end=time.monotonic()+5
        while time.monotonic()<end:
            # Keep monotonic POLICY time fixed while awaiting this real serial
            # roundtrip; this is accelerated policy testing, not an endurance run.
            if any('Recv: echo:LLM_THERM_'+transport.active in x for x in logs): return
            time.sleep(.01)
        raise AssertionError('thermal serial marker missing')

    tick(1);tick(2);tick(3)
    receipt_on_wire()
    assert device.targetTemp[0]==100. and device.bedTargetTemp==60.
    if missing_receipt:
        tick(4);tick(5);result=tick(6)
        assert result['reason']=='target_effect_unconfirmed'
        assert driver.effects()[0]['status']=='outcome_unknown'
    else:
        assert tick(4)['state']=='standby'
        device.temp[0]=100.
        assert tick(5)['state']=='standby'
        assert tick(6,resume=True)['state']=='requesting_reheat'
        receipt_on_wire()
        assert tick(7)['state']=='reheating'
        assert not policy.status()['resume_ready']
        device.temp[0]=200.
        tick(8);tick(9)
        assert tick(10)['resume_ready']
        now[0]=11.
        eligibility=driver.consume_resume(sample(11.),now=11.)
        assert eligibility['restoration_still_required']
        assert all(e['status']=='completed' for e in driver.effects())
    try:
        transport.send_targets({**driver.effects()[0]['action'], 'deadline':now[0]+3.})
    except RequestRejected as exc:
        assert str(exc)=='thermal_effect_replay_or_session_limit'
    else:
        raise AssertionError('effect replay accepted')
    assert not backend.witness.arm_resume(frozen_epoch)
    assert not denied and not device.completed_moves
    sends=[x for x in logs if x.startswith('Send:') and ('M104 S' in x or 'M140 S' in x)]
    assert len(sends)==(2 if missing_receipt else 4),sends
    return {'scenario':'thermal_serial_missing_receipt' if missing_receipt else 'thermal_serial',
        'rejected_effects':rejected, 'states':[x['state'] for x in history], 'effects':driver.effects(),
        'target_command_sends':sends, 'physical_serial_open_attempts':len(denied),
        'executed_moves':len(device.completed_moves), 'frozen_witness_exercised':True,
        'old_checkpoint_invalidated':True, 'temperature_samples':'scripted virtual fixture',
        'motor_and_part_proofs':'fixture assumptions, not hardware evidence',
        'streamed_hot_print_resume_tested':False,'physical_thermal_qualified':False}
