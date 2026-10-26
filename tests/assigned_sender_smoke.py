"""Real pinned sender -> owned software terminal -> virtual/native firmware peer.

Never hardware. Job methods exercise sender attachment, not journaled job tools.
"""
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import json
import os
import pty
import select
import sqlite3
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import serial
import octoprint.plugin
from octoprint.settings import settings
from octoprint.printer.profile import PrinterProfileManager
from octoprint.plugins.virtual_printer import VirtualPrinterPlugin
from octoprint.plugins.virtual_printer import virtual as virtual_module
from octoprint.util import comm
from printer_control.assigned_sender import AssignedSerialEndpoint,AssignedSender
from printer_control.devices import Assignment,Device
from printer_control.service import OperationService,StartupAdmission
from printer_control.tty_prompt import TTYPromptBackend
from printer_control.tools import ToolRouter
from octoprint_virtual_smoke import Observer,RecordingVirtualPrinter,wait_for
from native_wait_bridge import NativeWaitMixin
from native_harness import build_native_harness


class PeerBridge:
    def __init__(self,master,peer):
        self.master,self.peer=master,peer
        self.stop=threading.Event();self.frames=[];self.errors=[]
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        pending=b''
        try:
            while not self.stop.is_set():
                if select.select([self.master],[],[],.001)[0]:
                    pending+=os.read(self.master,4096)
                    assert len(pending)<=8192
                    while b'\n' in pending:
                        frame,pending=pending.split(b'\n',1);frame+=b'\n'
                        self.frames.append(frame);self.peer.write(frame)
                line=self.peer.readline()
                if line:os.write(self.master,line if line.endswith(b'\n') else line+b'\n')
        except Exception as exc:
            if not self.stop.is_set():self.errors.append(repr(exc))
    def close(self):
        self.stop.set();self.thread.join(2);assert not self.thread.is_alive()


def main(scenario):
    denied=[];service=sender=peer=bridge=None;master=None;drain_thread=None
    drain_stop=threading.Event();drain_errors=[];gate=threading.Event()
    def forbidden(*a,**k):denied.append('forbidden_open_or_discovery');raise AssertionError('no physical serial access')
    with tempfile.TemporaryDirectory(prefix='assigned-sender-fixture-') as temp,ExitStack() as stack:
        temp=Path(temp)
        stack.enter_context(patch.object(serial.Serial,'open',forbidden))
        stack.enter_context(patch.object(comm,'serialList',forbidden))
        config=settings(init=True,basedir=str(temp));config.set(['serial','logPositionOnPause'],True)
        config.set(['serial','timeout','positionLogWait'],1.)
        config.set(['feature','sdSupport'],False)
        config.set(['serial','suppressSecondHello'],True)
        profiles=PrinterProfileManager()
        defaults=VirtualPrinterPlugin().get_settings_defaults()
        defaults.update(enabled=True,throttle=.001,simulateReset=False,sendWait=False,commandBuffer=4,simulated_errors=[])
        virtual_settings=octoprint.plugin.plugin_settings('virtual_printer',defaults=defaults,settings=config)
        def get_hooks(name):
            if name=='octoprint.comm.transport.serial.factory':return {'forbidden':forbidden}
            return {}
        plugins=SimpleNamespace(get_hooks=get_hooks)
        stack.enter_context(patch.object(octoprint.plugin,'plugin_manager',lambda:plugins))
        stack.enter_context(patch.object(virtual_module,'plugin_manager',lambda:plugins))
        stack.enter_context(patch.object(comm,'eventManager',lambda:SimpleNamespace(fire=lambda *a,**k:None)))
        class GatedPeer(RecordingVirtualPrinter):
            def _performMove(self,line):
                if scenario=='resume' and line.startswith('G1 X6 '):assert gate.wait(15)
                return super()._performMove(line)
        class NativePeer(NativeWaitMixin,GatedPeer):pass
        peer_type=NativePeer if scenario=='native_prompt' else GatedPeer
        options={'native_binary':build_native_harness(temp/'native')} if scenario=='native_prompt' else {}
        try:
            peer=peer_type(virtual_settings,profiles,str(temp),read_timeout=.01,**options)
            master,slave=pty.openpty();path=os.ttyname(slave);os.close(slave)
            device=Device(path,'software-terminal','fixture-instance',1,2)
            assignment=Assignment('development-fixture',device,time.time()+90,'owned PTY only',frozenset({'protected-fixture'}),frozenset({'protected-path'}))
            journal=temp/'operations.sqlite'
            service=OperationService.from_backend_factory(journal,
                lambda lease,*_:TTYPromptBackend.open(assignment,lambda:[device],time.time,lambda _:False,lease,copy_jobs=True),
                readiness_check=lambda _:StartupAdmission('fresh-software-peer-only',False))
            backend=service.backend
            original=backend.transport.write
            def checked_write(data):
                with sqlite3.connect(journal) as db:assert db.execute('SELECT count(*) FROM backend_startups').fetchone()[0]==1
                return original(data)
            backend.transport.write=checked_write
            endpoint=AssignedSerialEndpoint(backend)
            observer=Observer();sender=AssignedSender(endpoint,callbackObject=observer,printerProfileManager=profiles)
            bridge=PeerBridge(master,peer)
            sender.start()
            def drain():
                try:
                    while not drain_stop.wait(.005):
                        with backend.guard():backend.context()
                except Exception as exc:
                    if not drain_stop.is_set():drain_errors.append(repr(exc))
            drain_thread=threading.Thread(target=drain,daemon=True);drain_thread.start()
            wait_for(sender.isOperational,'assigned sender handshake')
            assert sender.getTransport() is endpoint
            content='G21\nG90\nM82\nT0\nM104 S0\nM140 S0\nG92 E0\n'+''.join(f'G1 X{i} Y10 E{i/10:.1f} F6000\nM117 MODEL_{i:03d}\n' for i in range(1,81))
            job=temp/'software-only.gcode';job.write_text(content)
            sender.selectFile(str(job),False);sender.startPrint()
            wait_for(lambda:peer.wait_active.is_set() if scenario=='native_prompt' else any('Recv: echo:MODEL_005' in s for s in observer.logs),'initial progress')
            sender.setPause(True);gate.set()
            receipts=[]
            if scenario=='native_prompt':
                router=ToolRouter(service)
                for name in ('prompt.inspect','prompt.bind','prompt.respond'):
                    state=router.call('printer.status',{})
                    args={'prompt':state['prompt_snapshot']['prompt'],'choice':0} if name=='prompt.respond' else {}
                    request=dict(request_id=name,printer_id=state['printer_id'],connection_generation=state['connection_generation'],
                        expected_state_version=state['state_version'],deadline=time.time()+5,arguments=args)
                    result=router.call(name,request)
                    end=time.monotonic()+5
                    while result['status']=='running' and time.monotonic()<end:
                        result=router.call('operation.get',{'operation_id':result['operation_id']})
                    assert result['status']=='completed',result
                    assert router.call(name,request)==result
                    receipts.append(result)
                assert peer.wait_released.is_set() and len(peer.prompt_writes)==3
            wait_for(lambda:sender.getState()==sender.STATE_PAUSED,'assigned pause barrier')
            cursor=sender.getFilePosition();before=list(peer.completed_moves)
            sender.setPause(True);time.sleep(.1)
            assert sender.getFilePosition()==cursor and peer.completed_moves==before
            sender.setPause(False);wait_for(lambda:observer.done,'assigned job completion',timeout=30)
            expected=[s for s in content.splitlines() if s.startswith('G1 ')]
            assert peer.completed_moves==expected and 0<len(before)<80
            assert not denied and not bridge.errors and not drain_errors
            assert not backend.receive_hub.fault and not backend.frame_writer.fault
            assert sender.getTransport() is endpoint
            print(json.dumps({'scenario':scenario,'scope':'real sender through software terminal; not job tool/backend or physical qualification',
                'completed_moves_once_in_order':len(expected),'moves_at_pause':len(before),'cursor_at_pause':cursor,
                'native_prompt_receipts':len(receipts),'physical_serial_open_or_discovery_attempts':len(denied),
                'single_endpoint':True,'copy_drainer_errors':drain_errors,'peer_errors':bridge.errors,'wire_frames':len(bridge.frames)}))
        finally:
            gate.set();drain_stop.set()
            if drain_thread:drain_thread.join(2);assert not drain_thread.is_alive()
            if bridge:bridge.close()
            if sender:sender.close()
            if service:service.close()
            if peer:peer.close()
            if master is not None:os.close(master)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--scenario',choices=['resume','native_prompt'],required=True)
    main(parser.parse_args().scenario)
