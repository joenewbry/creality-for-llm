"""Integration experiment using the unmodified OctoPrint sender and virtual printer.

Run separately: .venv-octoprint/bin/python tests/octoprint_virtual_smoke.py
No server is started. Physical serial opens are denied for the entire experiment.
Application callbacks and plugin discovery are replaced; sender and virtual
firmware are real OctoPrint code. This is not full LLM-interface qualification.
"""
from contextlib import ExitStack
import argparse
import hashlib
import io
import json
import logging
from pathlib import Path
import tempfile
import time
import sys
import faulthandler
from types import SimpleNamespace
from unittest.mock import patch

import serial
import octoprint.plugin
from octoprint.settings import settings
from octoprint.printer.profile import PrinterProfileManager
from octoprint.plugins.virtual_printer import VirtualPrinterPlugin
from octoprint.plugins.virtual_printer.virtual import VirtualPrinter
from octoprint.plugins.virtual_printer import virtual as virtual_module
from octoprint.util import comm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from printer_control.service import OperationService
from printer_control.octoprint_virtual import OctoPrintVirtualBackend
from printer_control.stream_witness import StreamWitness
from printer_control.tools import ToolRouter, serve
from printer_control.resend_guard import VirtualResendGuard
from printer_control.shared_writer import SharedFrameWriter
from printer_control.shared_receiver import SharedReceiveBuffer
from printer_control.octoprint_sender import VirtualSender

# Synthetic device fault reports through the actual virtual serial receive path.
# These test host handling, not the firmware's fault-detection algorithms.
HOT_SCENARIOS = ('hot_resume_managed_stop','hot_resume_managed_stop_lost','hot_console','hot_resume_managed_cancel_missing_marker','hot_resume_managed_cancel','hot_resume_managed_shutdown','hot_resume_managed','hot_resume_managed_timeout','hot_resume','hot_resume_temp_loss','hot_resume_twice',
                 'hot_pause_disconnect','hot_pause_reset','hot_pause_missing_receipt')

RESEND_SCENARIOS = {
    'resend_checksum': 'trigger_resend_checksum',
    'resend_lineno': 'trigger_resend_lineno',
}

FAULT_REPORTS = {
    'homing_fault': 'Homing failed, printer halted',
    'probe_fault': 'Probing failed, printer halted',
    'probe_no_halt_fault': 'Probing Failed',
    'heating_fault': 'Heating failed, system stopped! Heater_ID: 0',
}


def wait_for(predicate, label, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Timed out: ' + label)


class Observer(comm.MachineComPrintCallback):
    def __init__(self):
        self.logs = []
        self.states = []
        self.done = False
        self.errors = []

    def on_comm_log(self, message):
        self.logs.append(message)

    def on_comm_state_change(self, state):
        self.states.append(state)

    def on_comm_print_job_done(self, suppress_script=False):
        self.done = True

    def on_comm_error(self, error, reason, consequence=None, faq=None, logs=None):
        self.errors.append({'error': error, 'reason': reason})


class RecordingVirtualPrinter(VirtualPrinter):
    """Observe completed simulated moves without replacing their implementation."""
    def __init__(self, *args, **kwargs):
        self.completed_moves = []
        super().__init__(*args, **kwargs)

    def _performMove(self, line):
        super()._performMove(line)
        time.sleep(getattr(self, "model_move_delay", 0))
        self.completed_moves.append(line.strip())

    def readline(self):
        # VirtualPrinter queues logical lines, sometimes without delimiters.
        # Supply serial line framing in this fixture so the shared byte-stream
        # observer sees the same boundaries as real LF-terminated firmware.
        data = super().readline()
        return data + b'\n' if data and not data.endswith(b'\n') else data


def main(scenario='resume', use_service=False, console=False, journal_path=None, mcp=False, model_fixture=False):
    if not console:
        faulthandler.dump_traceback_later(45)
    logging.basicConfig(level=logging.WARNING)
    denied = []

    def no_physical(*args, **kwargs):
        denied.append('physical_serial_open_attempt')
        raise AssertionError('Physical serial access is forbidden in this test')

    with tempfile.TemporaryDirectory(prefix='llm-printer-virtual-') as temp, ExitStack() as stack:
        # Installed before MachineCom construction, including its worker threads.
        stack.enter_context(patch.object(serial.Serial, 'open', no_physical))
        stack.enter_context(patch.object(comm, 'serialList', lambda *a, **kw: ['VIRTUAL']))
        config = settings(init=True, basedir=temp)
        config.set(['serial', 'logPositionOnPause'], True)
        config.set(['feature', 'sdSupport'], False)
        config.set(['serial', 'timeout', 'positionLogWait'], 5.0 if model_fixture else .5)
        profiles = PrinterProfileManager()
        defaults = VirtualPrinterPlugin().get_settings_defaults()
        defaults.update({'enabled': True, 'throttle': .02, 'simulated_errors': [], 'sendWait': False})
        if scenario in HOT_SCENARIOS:defaults['enable_eeprom']=True
        if scenario == 'missing_position':
            defaults['m114FormatString'] = 'position unavailable'
        virtual_settings = octoprint.plugin.plugin_settings('virtual_printer', defaults=defaults, settings=config)
        virtual = []

        device_type = RecordingVirtualPrinter
        if scenario == 'restore_serial' or scenario in HOT_SCENARIOS:
            from marlin_modal_virtual import MarlinModalMixin
            class ModalVirtualPrinter(MarlinModalMixin, RecordingVirtualPrinter):
                pass
            device_type = ModalVirtualPrinter
        native_options = {}
        if scenario in ('native_prompt', 'unified_prompt'):
            from native_harness import build_native_harness
            from native_wait_bridge import NativeWaitMixin
            class HybridVirtualPrinter(NativeWaitMixin, RecordingVirtualPrinter):
                pass
            device_type = HybridVirtualPrinter
            native_options = {'native_binary': build_native_harness(Path(temp)/'native'),
                              'wait_timeout': 120 if console else 15}

        def factory(instance, port, baudrate, timeout):
            assert port == 'VIRTUAL'
            device = device_type(virtual_settings, profiles, temp, read_timeout=.1, **native_options)
            # All ordinary sender writes share this complete-frame boundary.
            # A future prompt lane uses write_prompt on the same object, never
            # a second port owner or a competing raw writer.
            device.resend_guard = VirtualResendGuard(device.write)
            device.shared_frame_writer = SharedFrameWriter(device.resend_guard.write)
            device.write = device.shared_frame_writer.write_job
            device.model_move_delay = .25 if model_fixture else 0
            if scenario in HOT_SCENARIOS:
                device._simulateTemps = lambda *a, **k: None
                device.temp[0], device.bedTemp = 200., 60.
                device.model_move_delay = .25 if model_fixture else .02
            virtual.append(device)
            return device

        witness = StreamWitness()
        receive_copy = None
        machine_events = None
        transport_backend = None
        def copy_received(sender, line, *args, **kwargs):
            original = line
            recorder = machine_events
            if recorder is not None:
                recorder.received_hook(sender, original)
            line = sender._serial.resend_guard.received(line)
            if transport_backend is not None:
                transport_backend.observe_transport_line(line)
            # No prompt authority exists during the sender's bootstrap. The
            # default virtual reset banner deliberately includes a non-ASCII byte.
            if receive_copy is not None:
                receive_copy.received_hook(sender, original)
            return line
        def get_hooks(name):
            if name == 'octoprint.comm.transport.serial.factory':
                return {'virtual_only': factory}
            if name == 'octoprint.comm.protocol.gcode.received':
                return {'prompt_receive_copy': copy_received}
            if name in ('octoprint.comm.protocol.gcode.queuing', 'octoprint.comm.protocol.gcode.queued', 'octoprint.comm.protocol.gcode.sending', 'octoprint.comm.protocol.gcode.sent'):
                return {'modal_witness': witness.hook}
            return {}
        hooks = SimpleNamespace(get_hooks=get_hooks)
        stack.enter_context(patch.object(octoprint.plugin, 'plugin_manager', lambda: hooks))
        stack.enter_context(patch.object(virtual_module, 'plugin_manager', lambda: hooks))
        # Avoid global event-service startup; recordable sender callbacks remain.
        stack.enter_context(patch.object(comm, 'eventManager', lambda: SimpleNamespace(fire=lambda *a, **kw: None)))
        observer = Observer()
        class AckBeforeCallbackSender(VirtualSender):
            early_ack_checks = 0
            def _do_send(self, command, gcode=None):
                result = super()._do_send(command, gcode=gcode)
                if gcode == 'M400':
                    self._clear_to_send.wait(2)
                    assert not self._clear_to_send.blocked(), 'fixture expected real M400 acknowledgment'
                    self.early_ack_checks += 1
                return result
        sender_type = AckBeforeCallbackSender if scenario == 'unified_prompt' else VirtualSender
        sender = sender_type(port='VIRTUAL', baudrate=115200, callbackObject=observer, printerProfileManager=profiles)
        service = None
        receipts = []
        tool_calls = []
        native_recovery = None
        clock_shift = [0]
        clock = lambda: time.time() + clock_shift[0]
        try:
            sender.start()
            wait_for(sender.isOperational, 'virtual connection')
            receive_copy = SharedReceiveBuffer()
            content = 'G21\nG90\nM82\nT0\nM106 S0\nM104 S0\nM140 S0\nG92 E0\n' + ''.join(f'G1 X{i} Y10 E{i / 10:.1f} F6000\nM117 MODEL_{i:03d}\n' for i in range(1, 81))
            if scenario in HOT_SCENARIOS:
                from hot_virtual_backend import HOT_JOB
                content = HOT_JOB
            job = Path(temp) / 'virtual-only.gcode'
            job.write_text(content)
            if use_service:
                class AuditedVirtualBackend(OctoPrintVirtualBackend):
                    def inspect(self):
                        result = super().inspect()
                        completed = list(virtual[0].completed_moves)
                        expected = [line for line in content.splitlines() if line.startswith('G1 ')]
                        result['simulation_evidence'] = {
                            'completed_moves': len(completed), 'expected_moves': len(expected),
                            'moves_match_expected_prefix': completed == expected[:len(completed)],
                            'all_moves_completed_once_in_order': completed == expected,
                            'physical_serial_open_attempts': len(denied),
                        }
                        return result
                backend_type = AuditedVirtualBackend if console else OctoPrintVirtualBackend
                if scenario in HOT_SCENARIOS:
                    from hot_virtual_backend import HotVirtualBackend
                    backend_type = HotVirtualBackend
                backend = backend_type(sender, job_path=job, job_id='simulation-job', witness=witness, clock=clock)
                if scenario in HOT_SCENARIOS:
                    backend.model_fixture=model_fixture
                    backend.physical_serial_attempts=denied
                transport_backend = backend
                if scenario == 'unified_prompt':
                    from printer_control.combined_virtual import CombinedVirtualBackend
                    backend = CombinedVirtualBackend(backend, receive_copy)
                service = OperationService(journal_path or Path(temp) / 'operations.sqlite', backend, clock=clock)

                if scenario == 'hot_console':
                    from printer_control.machine_events import MachineEventRecorder
                    machine_events = MachineEventRecorder(service.journal, generation=backend.generation)
                    backend.machine_events = machine_events
                    backend.supported_reads = backend.supported_reads | {'printer.machine_events'}
                if scenario in HOT_SCENARIOS:backend.calibration.attach(service)
                if scenario in HOT_SCENARIOS and scenario!='hot_console':
                    from hot_resume_scenario import exercise
                    result = exercise(service,backend,virtual[0],observer.logs,denied,managed=scenario.startswith('hot_resume_managed'),managed_timeout=scenario=='hot_resume_managed_timeout',managed_interrupt=scenario.removeprefix('hot_resume_managed_') if scenario in ('hot_resume_managed_stop','hot_resume_managed_stop_lost','hot_resume_managed_cancel','hot_resume_managed_cancel_missing_marker','hot_resume_managed_shutdown') else None,temperature_loss=scenario.endswith('temp_loss'),pause_count=2 if scenario=='hot_resume_twice' else 1,pause_fault=scenario.removeprefix('hot_pause_') if scenario.startswith('hot_pause_') else None)
                    if result.get('service_closed'):service=None
                    evidence = Path(__file__).resolve().parents[1] / 'evidence'
                    evidence.mkdir(exist_ok=True)
                    (evidence / (scenario + '.json')).write_text(json.dumps(result,indent=2)+'\n')
                    print(json.dumps(result))
                    return
                if scenario == 'restore_serial':
                    from restore_serial_scenario import exercise
                    result = exercise(service, backend, virtual[0], observer.logs, denied)
                    evidence = Path(__file__).resolve().parents[1] / 'evidence'
                    evidence.mkdir(exist_ok=True)
                    (evidence / 'restore_serial.json').write_text(json.dumps(result, indent=2) + '\n')
                    print(json.dumps(result))
                    return
                if scenario in ('thermal_serial', 'thermal_serial_missing_receipt', 'thermal_serial_monitor'):
                    from thermal_serial_scenario import exercise
                    result = exercise(service, backend, virtual[0], observer.logs, denied,
                                      missing_receipt=scenario.endswith('missing_receipt'),
                                      continuous=scenario=='thermal_serial_monitor')
                    if result.get('service_closed'):service=None
                    evidence = Path(__file__).resolve().parents[1] / 'evidence'
                    evidence.mkdir(exist_ok=True)
                    (evidence / (scenario + '.json')).write_text(json.dumps(result, indent=2) + '\n')
                    print(json.dumps(result))
                    return
                router = ToolRouter(service)
                if scenario=='hot_console':
                    from hot_fixture_router import HotFixtureRouter
                    router=HotFixtureRouter(service)
                if console:
                    if mcp:
                        import asyncio
                        from printer_control.mcp_adapter import serve_mcp
                        asyncio.run(serve_mcp(router))
                    else:
                        serve(router, sys.stdin, sys.stdout)
                    return

                def call_tool(name, arguments):
                    envelope = {'id': len(tool_calls) + 1, 'tool': name, 'arguments': arguments}
                    output = io.StringIO()
                    serve(router, io.StringIO(json.dumps(envelope) + '\n'), output)
                    result = json.loads(output.getvalue())
                    tool_calls.append({'request': envelope, 'response': result})
                    assert 'error' not in result, result
                    return result['result']

                def tool_submit(request):
                    return call_tool(request['operation'], {k: v for k, v in request.items() if k != 'operation'})

                def tool_poll(operation_id):
                    return call_tool('operation.get', {'operation_id': operation_id})

                def request(operation, request_id):
                    context = call_tool('printer.status', {})
                    arguments = {'job_id': 'simulation-job'}
                    if operation == 'job.stage':
                        arguments['file_sha256'] = context['file_sha256']
                    return dict(request_id=request_id, printer_id=context['printer_id'],
                                connection_generation=context['connection_generation'], expected_state_version=context['state_version'],
                                deadline=clock() + 10, operation=operation, arguments=arguments)

                assert len(call_tool('printer.describe', {})['tools']) == (18 if scenario == 'unified_prompt' else 14)
                staged = tool_submit(request('job.stage', 'stage-1'))
                assert staged['status'] == 'running', staged
                wait_for(lambda: tool_poll(staged['operation_id'])['status'] == 'completed', 'stage receipt')
                receipts.append(tool_poll(staged['operation_id']))
                job.write_text('changed input after stage\n')  # sender must use immutable staged bytes
                start_request = request('job.start', 'start-1')
                started = tool_submit(start_request)
                assert started['status'] == 'running', started
                wait_for(lambda: tool_poll(started['operation_id'])['status'] == 'completed', 'start receipt')
                receipts.append(tool_poll(started['operation_id']))
                wait_for(lambda: any('Recv: echo:MODEL_005' in line for line in observer.logs), 'initial print progress')
                if scenario in RESEND_SCENARIOS:
                    # Reject the next unexecuted numbered line. The generic
                    # debug trigger asks for lastN instead and replays an already
                    # executed command; that distinct ambiguity is not qualified.
                    device = virtual[0]
                    device._prepared_errors.append(
                        (lambda cur, last, line: device._triggerResend(expected=cur, checksum=True))
                        if scenario == 'resend_checksum' else
                        (lambda cur, last, line: device._triggerResend(expected=cur, actual=cur+1)))
                    wait_for(lambda: any('Recv: Resend:' in line for line in observer.logs),
                             'firmware resend request')
                if scenario in ('active_disconnect', 'active_reset', 'active_old_resend'):
                    from stream_disconnect_scenario import exercise
                    result = exercise(sender, virtual[0], observer, call_tool, tool_submit, request,
                        start_request, receipts[-1], [line for line in content.splitlines() if line.startswith('G1 ')],
                        reset=scenario == 'active_reset', old_resend=scenario == 'active_old_resend')
                    assert not denied
                    result['physical_serial_open_attempts'] = len(denied)
                    evidence = Path(__file__).resolve().parents[1] / 'evidence'
                    evidence.mkdir(exist_ok=True)
                    (evidence/f'octoprint-virtual-service-{scenario}.json').write_text(json.dumps(result, indent=2)+'\n')
                    faulthandler.cancel_dump_traceback_later()
                    print(json.dumps(result, indent=2))
                    return
                if scenario == 'native_prompt':
                    from native_wait_bridge import recover_native_wait
                    native_recovery = recover_native_wait(sender, receive_copy, backend.generation, Path(temp)/'prompt.sqlite')
                if scenario == 'unified_prompt':
                    assert virtual[0].wait_active.wait(5)
                pause_request = request('job.pause', 'pause-1')
                accepted = tool_submit(pause_request)
                assert accepted['status'] == 'running', accepted
                assert tool_submit(pause_request) == accepted
                if scenario == 'unified_prompt':
                    from native_wait_bridge import recover_native_wait
                    assert tool_poll(accepted['operation_id'])['status'] == 'running'
                    native_recovery = recover_native_wait(sender, receive_copy, backend.generation, None, unified_service=service)
                    native_recovery['one_journal_with_pending_job_pause'] = True
            else:
                sender.selectFile(str(job), False)
                witness.start_job()
                sender.startPrint()
                wait_for(lambda: any('Recv: echo:MODEL_005' in line for line in observer.logs), 'initial print progress')
                sender.setPause(True)
            wait_for(lambda: sender.getState() == sender.STATE_PAUSED, 'pause barrier')
            if use_service:
                pause_receipt = tool_poll(accepted['operation_id'])
                assert pause_receipt['status'] == ('outcome_unknown' if scenario == 'missing_position' else 'completed'), pause_receipt
                assert pause_receipt['result']['resumable'] == (scenario != 'missing_position'), pause_receipt
                receipts.append(pause_receipt)
            cursor = sender.getFilePosition()
            position = sender.pause_position.as_dict()
            has_position = all(position.get(axis) is not None for axis in ('x', 'y', 'z', 'e'))
            assert has_position == (scenario != 'missing_position')
            count_at_pause = sum('Recv: echo:MODEL_' in line for line in observer.logs)
            moves_at_pause = list(virtual[0].completed_moves)
            sender.setPause(True)  # explicit duplicate pause must not resume
            time.sleep(.5)
            assert sender.getState() == sender.STATE_PAUSED
            assert sender.getFilePosition() == cursor
            assert sum('Recv: echo:MODEL_' in line for line in observer.logs) == count_at_pause
            assert virtual[0].completed_moves == moves_at_pause
            if scenario in ('resume', 'native_prompt', 'unified_prompt', *RESEND_SCENARIOS):
                if use_service:
                    resume_request = request('job.resume', 'resume-1')
                    resumed = tool_submit(resume_request)
                    assert resumed['status'] == 'running', resumed
                    assert tool_submit(resume_request) == resumed
                    wait_for(lambda: tool_poll(resumed['operation_id'])['status'] == 'completed', 'resume receipt')
                    receipts.append(tool_poll(resumed['operation_id']))
                else:
                    sender.setPause(False)
                    sender.setPause(False)
                wait_for(lambda: observer.done, 'job completion', timeout=30)
                if use_service:
                    wait_for(lambda: call_tool('job.inspect', {})['job_state'] == 'completed', 'tool-observed job completion')
                    assert call_tool('printer.events', {'after': 0, 'limit': 1000})['events']
            elif scenario in ('missing_position', 'interference', 'expired'):
                if scenario == 'expired':
                    assert use_service, 'Expiry scenario requires --service'
                    eligible = call_tool('job.inspect', {})
                    assert eligible['resumable']
                    assert 0 < eligible['pause_policy']['remaining_seconds'] <= 60
                    clock_shift[0] = 61  # virtual policy time, not a 61-second endurance test
                    expired = call_tool('job.inspect', {})
                    assert not expired['resumable']
                    assert expired['state_version'] > eligible['state_version']
                    assert expired['pause_policy']['interval_invalidated']
                    assert expired['pause_policy']['remaining_seconds'] == 0
                    assert not expired['pause_policy']['extended_pause_supported']
                    clock_shift[0] = 0  # clock correction must not revive expired authority
                    renewal = tool_submit(request('job.pause', 'pause-renewal'))
                    assert renewal['status'] == 'rejected'
                    assert renewal['result']['reason'] == 'job_already_paused_checkpoint_cannot_be_renewed'
                    receipts.append(renewal)
                    corrected = call_tool('job.inspect', {})
                    assert not corrected['resumable']
                    assert corrected['pause_policy']['interval_invalidated']
                    assert corrected['pause_policy']['remaining_seconds'] == 0
                    rejected = tool_submit(request('job.resume', 'resume-1'))
                    assert rejected['status'] == 'rejected', rejected
                    assert 'pause_interval_expired' in rejected['result']['reason']
                    receipts.append(rejected)
                if scenario == 'interference':
                    assert use_service, 'Interference scenario requires --service'
                    before_epoch = witness.snapshot()[2]
                    sender.sendCommand('G91')
                    wait_for(lambda: witness.snapshot()[2] > before_epoch, 'blocked manual mode change')
                    assert witness.snapshot()[0]['xyz_relative'] is False
                    rejected = tool_submit(request('job.resume', 'resume-1'))
                    assert rejected['status'] == 'rejected', rejected
                    assert 'commands_queued_since_checkpoint' in rejected['result']['reason']
                    receipts.append(rejected)
                # Harness declines resume when the host lacks position evidence.
                # This is the required adapter policy, not built-in OctoPrint policy.
                if use_service:
                    cancelling = tool_submit(request('job.cancel', 'cancel-1'))
                    assert cancelling['status'] == 'running'
                else:
                    sender.cancelPrint()
                wait_for(lambda: sender.getState() == sender.STATE_OPERATIONAL, 'cancel while paused')
                if use_service:
                    wait_for(lambda: tool_poll(cancelling['operation_id'])['status'] == 'completed', 'cancel receipt')
                    receipts.append(tool_poll(cancelling['operation_id']))
            else:
                if scenario in FAULT_REPORTS:
                    virtual[0]._send('Error:' + FAULT_REPORTS[scenario])
                    if scenario != 'probe_no_halt_fault':
                        virtual[0]._send('Error:Printer halted. kill() called!')
                else:
                    virtual[0]._debugTrigger('trigger_fatal_error_marlin' if scenario == 'thermal_fault' else 'drop_connection')  # simulator-only injection
                wait_for(lambda: sender.getState() in (sender.STATE_CLOSED, sender.STATE_CLOSED_WITH_ERROR), 'connection loss')
                assert not observer.done
                if use_service:
                    status = call_tool('job.inspect', {})
                    assert status['job_state'] == 'outcome_unknown'
                    assert not status['resumable']
                    if scenario == 'thermal_fault' or scenario in FAULT_REPORTS:
                        message = FAULT_REPORTS.get(scenario, 'Thermal Runaway')
                        assert status['blocking_fault'] and message in status['blocking_fault']['message']
                    for operation in ('job.resume', 'job.cancel'):
                        refused = tool_submit(request(operation, operation + '-after-fault'))
                        assert refused['status'] == 'rejected', refused
                        receipts.append(refused)
                    assert virtual[0].completed_moves == moves_at_pause
            markers = [line.split('echo:MODEL_', 1)[1].strip() for line in observer.logs if 'Recv: echo:MODEL_' in line]
            expected_count = 80 if scenario in ('resume', 'native_prompt', 'unified_prompt', *RESEND_SCENARIOS) else count_at_pause
            assert markers == [f'{i:03d}' for i in range(1, expected_count + 1)], markers
            expected_moves = [line for line in content.splitlines() if line.startswith('G1 ')] if scenario in ('resume', 'native_prompt', 'unified_prompt', *RESEND_SCENARIOS) else moves_at_pause
            assert virtual[0].completed_moves == expected_moves
            if scenario not in ('disconnect', 'thermal_fault') and scenario not in FAULT_REPORTS:
                assert not observer.errors, observer.errors
            assert not denied
            copied = receive_copy.drain()
            assert b'echo:MODEL_' in copied or scenario == 'unified_prompt'
            if scenario == 'thermal_fault' or scenario in FAULT_REPORTS:
                assert FAULT_REPORTS.get(scenario, 'Thermal Runaway').encode() in copied
            result = {'evidence_type': 'OctoPrint sender + bundled virtual printer', 'octoprint_version': '1.11.8', 'physical_serial_open_attempts': 0,
                      'file_sha256': hashlib.sha256(content.encode()).hexdigest(), 'model_markers_in_order_once': len(markers),
                      'scenario': scenario, 'pause_cursor': {**cursor, 'filename': job.name}, 'pause_position': position, 'pause_interval_seconds': .5,
                      'completed_simulated_moves_once_in_order': len(virtual[0].completed_moves),
                      'policy_clock_advance_seconds': clock_shift[0],
                      'duplicate_pause_checked': True, 'duplicate_resume_checked': scenario in ('resume', 'native_prompt', 'unified_prompt', *RESEND_SCENARIOS), 'state_sequence': [sender.getStateString(s) for s in observer.states],
                      'limits': ['No real hardware', 'No parking/thermal standby script', 'No durable checkpoint or LLM adapter exercised', 'Virtual firmware is not the S1 firmware']}
            if scenario in RESEND_SCENARIOS:
                result['resend_reports'] = [line for line in observer.logs if 'Recv: Resend:' in line]
                assert result['resend_reports']
                result['resend_injection'] = 'reject_next_unexecuted_numbered_line'
                result['arbitrary_old_line_resend_qualified'] = False
            if scenario in FAULT_REPORTS:
                result['fault_injection'] = {'scope': 'synthetic_serial_report_while_paused',
                    'message': FAULT_REPORTS[scenario], 'halt_report_injected': scenario != 'probe_no_halt_fault',
                    'firmware_fault_detection_tested': False}
            if scenario == 'expired':
                result['pause_policy_observations'] = [eligible, expired, corrected]
                result['policy_clock_shift_sequence_seconds'] = [0, 61, 0]
            if native_recovery:
                result['native_prompt_recovery'] = native_recovery
            if scenario == 'unified_prompt':
                assert sender.early_ack_checks >= 2
                result['sender_ack_before_callback_checks'] = sender.early_ack_checks
                # Exercise shared invalidation independently of physical faults:
                # a synthetic sender callback must revoke the prompt session too.
                sender._callback.on_comm_error('Injected shared-backend fault', 'firmware')
                fault_status = call_tool('printer.status', {})
                assert fault_status['prompt']['channel_state'] == 'uncertain'
                assert fault_status['prompt']['prompt_snapshot'] is None
                assert not fault_status['resumable']
                refused = tool_submit(request('job.start', 'start-after-shared-fault'))
                assert refused['status'] == 'rejected'
                assert refused['result']['reason'] == 'shared_connection_unqualified'
                result['shared_fault_invalidation_verified'] = True
            if use_service:
                result['shared_receive_copy_verified'] = True
                result['service_receipts'] = receipts
                result['tool_transport'] = 'bounded newline JSON'
                result['tool_calls'] = tool_calls
                result['immutable_staged_copy_checked'] = True
                result['limits'][2] = 'Service checkpoint and in-place resume for cold virtual profile only; no physical/thermal/parking qualification.'
            prefix = 'console-' if console else ('service-' if use_service else '')
            evidence = Path(__file__).resolve().parents[1] / 'evidence'
            evidence.mkdir(exist_ok=True)
            (evidence / f'octoprint-virtual-{prefix}{scenario}.json').write_text(json.dumps(result, indent=2) + '\n')
            # Generated job and transcript contain no physical-printer data.
            (evidence / f'octoprint-virtual-{prefix}{scenario}.log').write_text('\n'.join(observer.logs) + '\n')
            faulthandler.cancel_dump_traceback_later()
            print(json.dumps(result, indent=2))
        finally:
            if machine_events is not None:
                recorder, machine_events = machine_events, None
                recorder.close()
            if service is not None:
                service.close()
            sender.close()
            for device in virtual:
                device.close()
            evidence = Path(__file__).resolve().parents[1] / 'evidence'
            evidence.mkdir(exist_ok=True)
            prefix = 'console-' if console else ('service-' if use_service else '')
            (evidence / f'octoprint-virtual-{prefix}{scenario}.log').write_text('\n'.join(observer.logs) + '\n')
            if use_service:
                (evidence / f'tools-{scenario}.json').write_text(json.dumps(tool_calls, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=[*HOT_SCENARIOS, 'restore_serial', 'thermal_serial_monitor', 'thermal_serial', 'thermal_serial_missing_receipt', 'resume', 'missing_position', 'disconnect', 'interference', 'expired', 'thermal_fault', 'native_prompt', 'unified_prompt', 'active_disconnect', 'active_reset', 'active_old_resend', *FAULT_REPORTS, *RESEND_SCENARIOS], default='resume')
    parser.add_argument('--service', action='store_true')
    parser.add_argument('--console', action='store_true', help='Serve typed tools over stdin/stdout; VIRTUAL only')
    parser.add_argument('--mcp', action='store_true', help='Serve the virtual-only console through MCP stdio')
    parser.add_argument('--model-fixture', action='store_true', help='Software-only movement delay for model latency')
    parser.add_argument('--journal', type=Path, help='Persistent console receipt database; parent must exist')
    args = parser.parse_args()
    if args.scenario=='hot_console' and not (args.console or args.mcp):
        parser.error('hot_console requires --console or --mcp')
    if args.scenario == 'native_prompt' and (not args.service or args.console or args.mcp or args.model_fixture):
        parser.error('native_prompt is a standalone --service experiment; use unified_prompt for the combined console')
    if args.scenario == 'unified_prompt' and not (args.service or args.console or args.mcp):
        parser.error('unified_prompt requires --service, --console or --mcp')
    if args.scenario in (*(s for s in HOT_SCENARIOS if s!='hot_console'), 'restore_serial', 'thermal_serial_monitor', 'thermal_serial', 'thermal_serial_missing_receipt', 'active_disconnect', 'active_reset', 'active_old_resend', *RESEND_SCENARIOS) and (not args.service or args.console or args.mcp):
        parser.error('active_disconnect requires standalone --service')
    journal_path = args.journal
    if (args.console or args.mcp) and journal_path is None:
        evidence = Path(__file__).resolve().parents[1] / 'evidence'
        evidence.mkdir(exist_ok=True)
        journal_path = evidence / 'virtual-console.sqlite'
    main(args.scenario, args.service or args.console or args.mcp, args.console or args.mcp, journal_path, args.mcp, args.model_fixture)
