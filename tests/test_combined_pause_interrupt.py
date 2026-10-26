"""Combined virtual backend interruption integration; kept outside core effect tests."""
import pytest
from printer_control.pause_effects import DurablePauseEffects
from printer_control.service import OperationService
from test_pause_effects import advance
from test_thermal_pause import fixture as policy_fixture

@pytest.mark.parametrize('operation',['printer.stop','job.cancel'])
@pytest.mark.parametrize('storage_failure',[False,True])
@pytest.mark.parametrize('grant_kind',['thermal','restore'])
def test_combined_interruption_revokes_queued_pause_grant_before_dispatch(tmp_path,monkeypatch,operation,storage_failure,grant_kind):
    import queue
    import threading
    from types import SimpleNamespace
    from printer_control.combined_virtual import CombinedVirtualBackend
    from printer_control.octoprint_virtual import OctoPrintVirtualBackend
    from printer_control.service import Context
    from printer_control.thermal_pause import ThermalPausePolicy
    from printer_control.checkpoint import PauseCheckpoint
    from printer_control.stream_witness import StreamWitness

    witness=StreamWitness()
    witness.start_job()
    assert witness.freeze(witness.snapshot()[2])
    queued=[]
    dispatched=[]
    def hook(phase,command,tag):
        return witness.hook(None,phase,command,None,command.split()[0],tags={tag})
    def verify_revoked():
        command,tag=queued[0]
        assert hook('sending',command,tag)==(None,), 'pause command still authorized at interruption'
    def stop_write(frame):
        verify_revoked()
        dispatched.append(frame)
        return len(frame)
    writer=SimpleNamespace(fault=None,write_prompt=lambda frame:len(frame),write_job=stop_write)
    device=SimpleNamespace(shared_frame_writer=writer,_supportM112=True,_killed=False,
        buffered=queue.Queue(),targetTemp=[0.],bedTargetTemp=0.,chamberTargetTemp=0.)
    lock=threading.RLock()
    job=OctoPrintVirtualBackend.__new__(OctoPrintVirtualBackend)
    job.sender=SimpleNamespace(_serial=device)
    job.generation='g1';job.clock=lambda:10.;job._fault=None;job.checkpoint=None
    job.witness=witness
    job.guard=lambda:lock
    job.context=lambda:Context('sim','g1',1,10.)
    job.validate=lambda *args:None
    def cancel_dispatch(*args,**kwargs):
        verify_revoked()
        dispatched.append('cancel')
    job.dispatch=cancel_dispatch
    job.close=lambda:None
    receiver=SimpleNamespace(drain=lambda:[],close=lambda:None)
    backend=CombinedVirtualBackend(job,receiver)
    with OperationService(tmp_path/'combined.sqlite',backend,clock=lambda:10.) as service:
        base=policy_fixture()
        policy=ThermalPausePolicy(PauseCheckpoint(base.saved),base.limits,started_at=0.)
        def enqueue(action):
            commands=[f"M104 S{action['nozzle_target']}",f"M140 S{action['bed_target']}",
                      'M118 E1 LLM_THERM_'+action['effect_id']]
            if grant_kind == 'thermal':
                tag=witness.authorize_thermal(action['effect_id'],commands,witness.snapshot()[2])
            else:
                from printer_control.pause_restore import InPlaceRestorePlan
                from test_checkpoint import data
                saved = PauseCheckpoint(data())
                live = data()
                live['created_at'] = 11
                plan = InPlaceRestorePlan(saved, PauseCheckpoint(live), now=11, max_observation_age=1)
                tag = witness.authorize_restore(plan, witness.snapshot()[2], now=11)
                commands = plan.commands + ('M400', 'M118 E1 ' + plan.marker)
            for phase in ('queuing','queued'):
                for command in commands:assert hook(phase,command,tag) is None
            queued.append((commands[0],tag))
        driver=DurablePauseEffects(service,policy,enqueue)
        advance(driver,1,resume=True)
        if storage_failure:
            def fail(**kwargs):raise OSError('thermal record unavailable')
            monkeypatch.setattr(driver,'_persist',fail)
        context=backend.context()
        receipt=service.submit(dict(request_id='interrupt',printer_id=context.printer_id,
            connection_generation=context.generation,expected_state_version=context.state_version,
            deadline=20.,operation=operation,arguments={} if operation=='printer.stop' else {'job_id':'job1'}))
        assert receipt['status']=='running',receipt
        assert dispatched==([b'M112\n'] if operation=='printer.stop' else ['cancel'])
        assert policy.state=='invalid'
        assert driver.receipt_failure is storage_failure
