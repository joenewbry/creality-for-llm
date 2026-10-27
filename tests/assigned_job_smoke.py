"""Journaled job/prompt tools over an assigned software terminal.

The evidence provider reads the virtual peer. No physical provider or access.
"""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib,json,os,pty,re,sqlite3,sys,tempfile,threading,time,argparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import serial,octoprint.plugin
from octoprint.settings import settings
from octoprint.printer.profile import PrinterProfileManager
from octoprint.plugins.virtual_printer import VirtualPrinterPlugin
from octoprint.plugins.virtual_printer import virtual as virtual_module
from octoprint.util import comm
from printer_control.assigned_sender import AssignedSerialEndpoint,AssignedSender
from printer_control.assigned_observations import AssignedMachineEventRecorder
from printer_control.assigned_job import AssignedJobBackend,AssignedJobEvidence
from printer_control.devices import Assignment,Device
from printer_control.service import OperationService,StartupAdmission
from printer_control.tty_prompt import TTYPromptBackend
from printer_control.tools import ToolRouter
from printer_control.preflight import cold_virtual_job
from printer_control.checkpoint import PauseCheckpoint
from printer_control.stream_witness import StreamWitness
from assigned_sender_smoke import PeerBridge,Observer,RecordingVirtualPrinter,wait_for,NativeWaitMixin,build_native_harness


class FixtureEvidence:
    """Test provider. Private virtual fields are not hardware evidence."""
    kind='software_only_virtual_peer'
    def __init__(self,peer,sender,witness,expected,denied=False):
        self.peer,self.sender,self.witness,self.expected=peer,sender,witness,expected
        self.denied=denied;self.pause_moves=0
    def preflight(self,data,identity):cold_virtual_job(data);return True
    def start_blockers(self,identity):return ['fixture_clearance_unqualified'] if self.denied else []
    def proof(self,identity,operation_id,phase,source):
        return AssignedJobEvidence.from_observation(identity,operation_id,phase,time.time(),source,self.kind)
    def started(self,identity,operation_id):
        if self.peer.completed_moves:return self.proof(identity,operation_id,'started','virtual_completed_move_counter')
    def resumed(self,identity,operation_id):
        if len(self.peer.completed_moves)>self.pause_moves:return self.proof(identity,operation_id,'resumed','virtual_progress_after_checkpoint')
    def completed(self,identity,operation_id):
        if self.peer.buffered.unfinished_tasks==0 and self.peer.completed_moves==self.expected:return self.proof(identity,operation_id,'completed','virtual_exact_move_trace_and_drained_queue')
    def cancelled(self,identity,operation_id):
        if self.peer.buffered.unfinished_tasks==0 and not any(self.peer.targetTemp) and self.peer.bedTargetTemp==0:return self.proof(identity,operation_id,'cancelled','virtual_drained_queue_and_zero_targets')
    def checkpoint(self,identity):
        modes,blockers,_=self.witness.snapshot()
        assert not blockers and self.peer.buffered.unfinished_tasks==0
        modal={k:modes[k] for k in ('units','xyz_relative','e_relative','feedrate','tool','fan_pwm','nozzle_target','bed_target')}
        modal.update(feedrate_percent=self.peer._feedrate_multiplier,flow_percent=self.peer._flowrate_multiplier)
        assert self.peer._relative==modes['xyz_relative'] and self.peer._lastF==modes['feedrate']
        self.pause_moves=len(self.peer.completed_moves)
        now=time.time();cursor=self.sender.getFilePosition()
        position=dict(x=self.peer._lastX,y=self.peer._lastY,z=self.peer._lastZ,e=self.peer._lastE[0])
        return PauseCheckpoint(dict(identity,schema_version=1,firmware_id='software-native-peer',
            config_sha256=hashlib.sha256(b'fixture-cold-assigned-v1').hexdigest(),cursor=cursor['pos'],
            next_line_number=self.sender._current_line,created_at=now,expires_at=now+60,
            position=position,modal=modal,offsets=dict(x=0,y=0,z=0,e=0),leveling_active=False,
            evidence={k:True for k in PauseCheckpoint.PROOFS}))


def main(scenario):
    denied=[];service=sender=peer=bridge=recorder=None;master=None;drain_thread=None
    drain_stop=threading.Event();drain_errors=[];gate=threading.Event();witness=StreamWitness()
    def forbidden(*a,**k):denied.append('forbidden_open_or_discovery');raise AssertionError('no physical serial access')
    with tempfile.TemporaryDirectory(prefix='assigned-job-fixture-') as temp,ExitStack() as stack:
        temp=Path(temp);stack.enter_context(patch.object(serial.Serial,'open',forbidden));stack.enter_context(patch.object(comm,'serialList',forbidden))
        config=settings(init=True,basedir=str(temp));config.set(['serial','logPositionOnPause'],True)
        config.set(['serial','timeout','positionLogWait'],1.);config.set(['feature','sdSupport'],False)
        config.set(['serial','suppressSecondHello'],True)
        if scenario=='ack_timeout':config.set(['serial','timeout','communication'],.5)
        if scenario in ('resend','ack_timeout','sensor_fault','thermal_fault'):config.set(['serial','sendM112OnError'],True)
        profiles=PrinterProfileManager()
        defaults=VirtualPrinterPlugin().get_settings_defaults();defaults.update(enabled=True,throttle=.001,simulateReset=False,sendWait=False,commandBuffer=4,simulated_errors=[])
        virtual_settings=octoprint.plugin.plugin_settings('virtual_printer',defaults=defaults,settings=config)
        def get_hooks(name):
            if name=='octoprint.comm.transport.serial.factory':return {'forbidden':forbidden}
            if name=='octoprint.comm.protocol.gcode.received':return {'source_capture':lambda sender,line:recorder.received_hook(sender,line) if recorder is not None else line}
            if name in ('octoprint.comm.protocol.gcode.queuing','octoprint.comm.protocol.gcode.queued','octoprint.comm.protocol.gcode.sending','octoprint.comm.protocol.gcode.sent'):
                return {'witness':witness.hook}
            return {}
        plugins=SimpleNamespace(get_hooks=get_hooks)
        stack.enter_context(patch.object(octoprint.plugin,'plugin_manager',lambda:plugins));stack.enter_context(patch.object(virtual_module,'plugin_manager',lambda:plugins))
        stack.enter_context(patch.object(comm,'eventManager',lambda:SimpleNamespace(fire=lambda *a,**k:None)))
        class GatedPeer(RecordingVirtualPrinter):
            def _performMove(self,line):
                if scenario!='native_prompt' and line.startswith('G1 X6 '):assert gate.wait(15)
                return super()._performMove(line)
        class NativePeer(NativeWaitMixin,GatedPeer):pass
        peer_type=NativePeer if scenario=='native_prompt' else GatedPeer
        options={'native_binary':build_native_harness(temp/'native')} if scenario=='native_prompt' else {}
        content='G21\nG90\nM82\nT0\nM106 S0\nM104 S0\nM140 S0\nG92 E0\n'+''.join(f'G1 X{i} Y10 E{i/10:.1f} F6000\nM117 MODEL_{i:03d}\n' for i in range(1,81))
        expected=[s for s in content.splitlines() if s.startswith('G1 ')]
        job=temp/'software-only.gcode';job.write_text(content)
        try:
            peer=peer_type(virtual_settings,profiles,str(temp),read_timeout=.01,**options)
            master,slave=pty.openpty();path=os.ttyname(slave);os.close(slave)
            device=Device(path,'software-terminal','fixture-instance',1,2)
            assignment=Assignment('development-fixture',device,time.time()+120,'owned PTY only',frozenset({'protected-fixture'}),frozenset({'protected-path'}))
            observer=Observer();journal=temp/'operations.sqlite'
            def factory(lease,*_):
                nonlocal sender
                prompt=TTYPromptBackend.open(assignment,lambda:[device],time.time,lambda _:False,lease,copy_jobs=True)
                sender=AssignedSender(AssignedSerialEndpoint(prompt),callbackObject=observer,printerProfileManager=profiles)
                evidence=FixtureEvidence(peer,sender,witness,expected,denied=scenario=='denied')
                return AssignedJobBackend(sender,job_path=job,job_id='fixture-job',witness=witness,evidence=evidence,clock=time.time)
            service=OperationService.from_backend_factory(journal,factory,readiness_check=lambda _:StartupAdmission('software-peer-only',False))
            backend=service.backend
            recorder=AssignedMachineEventRecorder(service.journal,endpoint=sender._assigned_endpoint,lease=service.lease,clock=time.time)
            backend.attach_machine_events(recorder)
            bridge=PeerBridge(master,peer);sender.start()
            def drain():
                try:
                    while not drain_stop.wait(.005):
                        with backend.guard():backend.context()
                except Exception as exc:
                    if not drain_stop.is_set():drain_errors.append(repr(exc))
            drain_thread=threading.Thread(target=drain,daemon=True);drain_thread.start()
            wait_for(sender.isOperational,'assigned job handshake');router=ToolRouter(service);receipts=[];counter=[0]
            def submit(name,args=None):
                counter[0]+=1;state=router.call('printer.status',{})
                if args is None:args={'job_id':'fixture-job'}
                if name=='job.stage':args={**args,'file_sha256':state['file_sha256']}
                request=dict(request_id=str(counter[0])+name,printer_id=state['printer_id'],connection_generation=state['connection_generation'],
                    expected_state_version=state['state_version'],deadline=time.time()+10,arguments=args)
                return request,router.call(name,request)
            def finish(name,request,receipt):
                end=time.monotonic()+12
                while receipt['status']=='running' and time.monotonic()<end:receipt=router.call('operation.get',{'operation_id':receipt['operation_id']})
                assert receipt['status']=='completed',receipt
                assert router.call(name,request)==receipt;receipts.append(receipt)
                return receipt
            req,result=submit('job.stage');finish('job.stage',req,result)
            staged_path=backend.path;job.write_text('changed source after staging\n')
            assert staged_path!=job and hashlib.sha256(staged_path.read_bytes()).hexdigest()==backend.sha256
            req,result=submit('job.start')
            if scenario=='denied':
                assert result['status']=='rejected' and not peer.completed_moves
                print(json.dumps({'scenario':scenario,'rejected_before_start':True,'physical_serial_open_or_discovery_attempts':len(denied)}));return
            finish('job.start',req,result)
            wait_for(lambda:peer.wait_active.is_set() if scenario=='native_prompt' else any('Recv: echo:MODEL_005' in s for s in observer.logs),'initial progress')
            if scenario in ('resend','ack_timeout','sensor_fault','thermal_fault'):
                if scenario=='resend':peer._send('Resend: 1')
                elif scenario=='sensor_fault':peer._send('ok T:-15.00 /0.00 B:21.70 /0.00')
                elif scenario=='thermal_fault':peer._send('Error:THERMAL RUNAWAY')
                expected_fault={'resend':'assigned_resend_unqualified','ack_timeout':'assigned_timeout_unqualified',
                    'sensor_fault':'assigned_serial_sensor_suspect','thermal_fault':'assigned_serial_fault'}[scenario]
                wait_for(lambda:backend.prompt.receive_hub.fault==expected_fault,'assigned replay retirement',timeout=10)
                wait_for(lambda:sender.getState()==sender.STATE_CLOSED_WITH_ERROR,'assigned sender error closure')
                before_frames=list(bridge.frames);time.sleep(.1)
                assert bridge.frames==before_frames
                motion=[re.search(rb'\bG1 X[^*\r\n]+',frame)[0] for frame in bridge.frames if re.search(rb'\bG1 X[^*\r\n]+',frame)]
                assert len(motion)==len(set(motion))
                assert sender._send_m112_on_error and not any(b'M112' in frame for frame in bridge.frames)
                state=router.call('job.inspect',{})
                assert state['job_state']=='outcome_unknown' and not state['resumable']
                assert not state['serial_reports']['source_usable']
                wait_for(lambda:recorder.read()['events'][-1]['event']['kind']!='capture_started','captured source lines')
                if scenario=='resend':
                    wait_for(lambda:any(row['event']['kind']=='resend_requested' for row in recorder.read(limit=1000)['events']),'recorded resend request')
                if scenario in ('sensor_fault','thermal_fault'):
                    kind='sensor_suspect' if scenario=='sensor_fault' else 'fault'
                    wait_for(lambda:any(row['event']['kind']==kind for row in recorder.read(limit=1000)['events']),'recorded source hazard')
                    assert state['serial_reports']['dispatch_inhibit']['reason']==expected_fault
                    for name in ('job.resume','prompt.inspect','prompt.bind'):
                        _,blocked=submit(name,{} if name.startswith('prompt.') else None)
                        assert blocked['status']=='rejected',blocked
                assert not denied and not bridge.errors and not drain_errors
                print(json.dumps(dict(scenario=scenario,transport_fault=expected_fault,job_state=state['job_state'],
                    physical_stop_confirmed=False,automatic_emergency_frame_emitted=False,physical_serial_open_or_discovery_attempts=len(denied),
                    no_additional_wire_frames_after_retirement=True,no_original_move_replay=True,source_bound_serial_capture=True)))
                return
            pause_req,pending=submit('job.pause');assert pending['status']=='running',pending
            gate.set()
            if scenario=='native_prompt':
                for name in ('prompt.inspect','prompt.bind','prompt.respond'):
                    state=router.call('printer.status',{})
                    args={'prompt':state['prompt_snapshot']['prompt'],'choice':0} if name=='prompt.respond' else {}
                    req,result=submit(name,args);finish(name,req,result)
                assert peer.wait_released.is_set() and len(peer.prompt_writes)==3
            paused=finish('job.pause',pause_req,pending)
            assert paused['result']['resumable'],paused
            before=list(peer.completed_moves);cursor=sender.getFilePosition()
            time.sleep(.1);assert peer.completed_moves==before and sender.getFilePosition()==cursor
            operation='job.cancel' if scenario=='cancel' else 'job.resume'
            req,result=submit(operation);finish(operation,req,result)
            if scenario!='cancel':
                wait_for(lambda:router.call('job.inspect',{})['job_state']=='completed','qualified job completion',timeout=30)
                assert peer.completed_moves==expected
            else:
                assert router.call('job.inspect',{})['job_state']=='cancelled' and len(peer.completed_moves)<80
            assert not denied and not bridge.errors and not drain_errors
            assert not backend.capabilities()['physical_control_qualified']
            wait_for(lambda:'reported_position' in recorder.snapshot()['reports'],'durable assigned position report')
            page=router.call('printer.machine_events',{'after':0,'limit':1000})
            assert page['health']['fault'] is None and not page['physical_readiness_qualified']
            assert any(row['event']['kind']=='serial_line_received' for row in page['events'])
            report=recorder.snapshot()['reports']['reported_position']
            assert report['data']['receive_provenance']['printer_id']==backend.printer_id
            assert not report['data']['independent_measurement']
            print(json.dumps({'scenario':scenario,'scope':'journaled assigned tools with software-only evidence provider',
                'moves_completed':len(peer.completed_moves),'moves_at_pause':len(before),'receipts':len(receipts),
                'native_prompt_frames':len(peer.prompt_writes) if scenario=='native_prompt' else 0,
                'physical_serial_open_or_discovery_attempts':len(denied),'source_changed_after_stage':True,'physical_provider_implemented':False,'source_bound_serial_capture':True}))
        finally:
            gate.set();drain_stop.set()
            if drain_thread:drain_thread.join(2);assert not drain_thread.is_alive()
            if bridge:bridge.close()
            if service:service.close()
            elif sender:sender.close()
            if peer:peer.close()
            if master is not None:os.close(master)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--scenario',choices=['resume','native_prompt','cancel','denied','resend','ack_timeout','sensor_fault','thermal_fault'],required=True)
    main(parser.parse_args().scenario)
