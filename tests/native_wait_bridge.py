"""Software-only bridge: actual native prompt handlers plus virtual movement.

The injected wait is a fixture at MODEL_010, not the real Marlin motion planner.
"""
from contextlib import nullcontext
import threading
import time
from native_prompt_console import NativeProcessTransport
from printer_control.prompt_wire import PromptWireCommand, PromptWireReceipt
from printer_control.prompt_channel import PromptChannel
from printer_control.prompt_backend import PromptBackend
from printer_control.service import OperationService
from printer_control.tools import ToolRouter


class NativeWaitMixin:
    def __init__(self, *args, native_binary, wait_timeout=15, **kwargs):
        self.native = NativeProcessTransport(native_binary)
        self.wait_active, self.wait_released = threading.Event(), threading.Event()
        self.native_stop = threading.Event()
        self.prompt_writes = []
        self.wait_timeout = wait_timeout
        super().__init__(*args, **kwargs)
        self.native_reader = threading.Thread(target=self._read_native, daemon=True)
        self.native_reader.start()

    def _read_native(self):
        buffer = b''
        while not self.native_stop.wait(.002):
            data = self.native.pump()
            buffer += data
            if len(buffer) > 8192:
                self._send('Error:Native fixture output overflow')
                return
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if line.startswith(b'//llm:v1 '):
                    receipt = PromptWireReceipt.decode(line.decode('ascii'))
                    self._send(line.decode('ascii'))
                    if not receipt.active and not receipt.waiting:
                        self.wait_released.set()
                elif line.startswith(b'//llmstate:'):
                    # Preserve the original controller report. The real host
                    # parser must classify malformed/stale reports itself.
                    self._send(line.decode('ascii'))
                elif line == b'start':
                    self._send('Error:Native fixture lost')
                    return

    def write(self, data):
        if data.startswith(b'M876 V1 '):
            PromptWireCommand.decode(data)
            if not self.wait_active.is_set():
                raise OSError('fixture prompt not active')
            self.prompt_writes.append(data)
            return self.native.write(data)
        return super().write(data)

    def _gcode_M117(self, data):
        if data == 'M117 MODEL_010':
            self.buffered.join()
            self.wait_active.set()
            if not self.wait_released.wait(self.wait_timeout):
                self._send('Error:Native fixture wait timed out')
                self._killed = True
                return
        return super()._gcode_M117(data)

    def close(self):
        if not self.native_stop.is_set():
            self.native_stop.set()
            self._killed = True
            self.wait_released.set()
            self.native_reader.join(2)
            self.native.close()
        super().close()


def recover_native_wait(sender, receiver, generation, journal, *, unified_service=None):
    device = sender._serial
    assert device.wait_active.wait(5)
    before = list(device.completed_moves)
    line_number = sender._current_line
    assert len(before) == 10
    hold_started = time.monotonic()
    time.sleep(.25)
    held_seconds = time.monotonic() - hold_started
    assert device.completed_moves == before and sender._current_line == line_number
    if unified_service is None:
        channel = PromptChannel(device.shared_frame_writer.write_prompt, generation)
        backend = PromptBackend(channel, printer_id='virtual-development', clock=time.time,
                                pump=receiver.drain_observed, close_transport=lambda: None)
        ownership = OperationService(journal, backend)
    else:
        backend = unified_service.backend
        channel = backend.prompt.channel
        ownership = nullcontext(unified_service)
    results = []
    with ownership as service:
        router = ToolRouter(service)
        for name in ('prompt.inspect', 'prompt.bind', 'prompt.respond'):
            context = backend.context()
            snapshot, _ = channel.snapshot()
            arguments = {'prompt': snapshot.prompt, 'choice': 0} if name == 'prompt.respond' else {}
            request = dict(request_id=name, printer_id=context.printer_id,
                connection_generation=context.generation, expected_state_version=context.state_version,
                deadline=time.time()+5, arguments=arguments)
            if name == 'prompt.respond':
                stale = {**request, 'request_id': 'stale-response',
                         'arguments': {'prompt': snapshot.prompt+1, 'choice': 0}}
                refused = router.call(name, stale)
                assert refused['status'] == 'rejected'
                assert len(device.prompt_writes) == 2 and not device.wait_released.is_set()
            result = router.call(name, request)
            end = time.monotonic()+5
            while result['status'] == 'running' and time.monotonic() < end:
                time.sleep(.005)
                result = router.call('operation.get', {'operation_id': result['operation_id']})
            assert result['status'] == 'completed', result
            assert router.call(name, request) == result
            if name != 'prompt.respond':
                assert sender._current_line == line_number and device.completed_moves == before
            results.append(result)
    assert len(device.prompt_writes) == 3 and device.wait_released.is_set()
    return {'receipts': results, 'blocked_moves': 10, 'blocked_interval_seconds': held_seconds,
            'prompt_frames': 3, 'ordinary_line_number_unchanged_during_inspect_bind': True,
            'stale_response_rejected_without_write': True,
            'native_handler_with_virtual_motion': True}
