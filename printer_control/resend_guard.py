"""Acknowledgment boundary for the pinned virtual serial profile.

Assumes ordered one-per-command acknowledgments and one `ok` after Resend.
Only a single outstanding command can be retried; wider windows fail closed.
Not a general Marlin/physical transport qualification.
"""
from collections import deque
import re
import threading


class VirtualResendGuard:
    def __init__(self, writer):
        self.writer = writer
        self.lock = threading.RLock()
        self.pending = deque()
        self.acknowledged = None
        self.skip_resend_ok = False
        self.fault = None

    def write(self, frame):
        with self.lock:
            if frame == b'M112\n':
                return self.writer(frame)
            if self.fault:
                raise OSError('host resend boundary invalidated')
            if frame.startswith(b'M876 V1 '):
                return self.writer(frame)  # token receipts do not acknowledge job lines
            if len(self.pending) >= 256:
                self.fault = 'acknowledgment_window_overflow'
                raise OSError(self.fault)
            match = re.match(rb'N(\d+)\s+', frame)
            number = int(match[1]) if match else None
            reset = bool(re.search(rb'(?:^|\s)M110(?:\s|\*)', frame))
            self.pending.append((number, reset))
            try:
                written = self.writer(frame)
                if type(written) is not int or written != len(frame):
                    raise OSError('unconfirmed_write')
                return written
            except BaseException:
                self.fault = 'unconfirmed_write'
                raise

    def received(self, line):
        with self.lock:
            text = line.strip()
            if text == 'start':
                self.pending.clear()
                self.acknowledged = None
                self.skip_resend_ok = False
                # A reset cannot erase an already latched transport fault.
                return line
            resend = re.fullmatch(r'(?:Resend:\s*|rs\s+)(\d+)', text, re.I)
            if resend:
                number = int(resend[1])
                candidates = [n for n, reset in self.pending if n is not None and not reset]
                if (self.fault or self.acknowledged is None or number <= self.acknowledged
                        or len(self.pending) != 1 or not candidates or number != candidates[0]):
                    self.fault = 'resend_outside_unacknowledged_window'
                    return 'Error:Host resend boundary violated; device state unknown\n'
                self.pending.clear()  # sender will replay the unacknowledged window
                self.skip_resend_ok = True
            elif re.match(r'^ok(?:\s|$)', text):
                if self.skip_resend_ok:
                    self.skip_resend_ok = False
                elif self.pending:
                    number, reset = self.pending.popleft()
                    if number is not None:
                        self.acknowledged = number if reset or self.acknowledged is None else max(self.acknowledged, number)
            return line
