#!/usr/bin/env python3
"""Local JSON prompt-tool console backed only by the compiled native harness."""
import argparse
from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_harness import build_native_harness
from printer_control.prompt_backend import PromptBackend
from printer_control.prompt_channel import PromptChannel
from printer_control.service import OperationService
from printer_control.tools import ToolRouter, serve


class NativeProcessTransport:
    """Pipe-only transport. The executable comes from our builder, not tool input."""
    def __init__(self, binary):
        self.process = subprocess.Popen([str(binary), 'runtime'], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.closed = False
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)

    def write(self, data):
        if self.closed or self.process.poll() is not None:
            raise OSError('native transport closed')
        # The final byte schedules the test harness's main idle function.
        # It is not exposed in the JSON interface or used on a physical UART.
        written = os.write(self.process.stdin.fileno(), data + b'~')
        return len(data) if written == len(data) + 1 else 0

    def pump(self):
        if self.closed or self.process.poll() is not None:
            # Reuse the firmware-reset observation path to invalidate the channel.
            return b'start\n'
        if not select.select([self.process.stdout], [], [], 0)[0]:
            return b''
        return os.read(self.process.stdout.fileno(), 4096) or b'start\n'

    def close(self):
        if self.closed:
            return
        self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()
        self.closed = True


class NativePromptBackend(PromptBackend):
    def capabilities(self):
        return {**super().capabilities(), 'physical_printer': False,
                'evidence_type': 'native_firmware_with_stubbed_physical_dependencies',
                'fixture': 'one_generic_user_wait', 'firmware_harness_sha256': self.harness_sha256}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp', action='store_true', help='Use official-SDK MCP stdio instead of the JSON envelope transport')
    parser.add_argument('--journal', type=Path, default=ROOT / 'evidence/native-prompt-console.sqlite')
    options = parser.parse_args()
    options.journal.parent.mkdir(parents=True, exist_ok=True)
    # No serial path, G-code, arbitrary binary or network-listener option.
    with ExitStack() as stack:
        directory = stack.enter_context(tempfile.TemporaryDirectory(prefix='native-prompt-'))
        binary = build_native_harness(Path(directory))
        transport = NativeProcessTransport(binary)
        stack.callback(transport.close)
        channel = PromptChannel(transport.write, uuid.uuid4().hex)
        backend = NativePromptBackend(channel, printer_id='native-prompt', clock=time.time,
                                      pump=transport.pump, close_transport=transport.close)
        backend.harness_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
        service = stack.enter_context(OperationService(options.journal, backend))
        router = ToolRouter(service)
        if options.mcp:
            import asyncio
            from printer_control.mcp_adapter import serve_mcp
            asyncio.run(serve_mcp(router))
        else:
            serve(router, sys.stdin, sys.stdout)


if __name__ == '__main__':
    main()
