"""Single-flight experimental prompt transport, with no device discovery/open.

The caller supplies an exclusively owned writer and must journal mutations
before dispatch. This component reports protocol acceptance, never recovery.
"""
from dataclasses import dataclass
import math
import secrets
import threading
from typing import Callable

from .protocol import ProtocolObserver
from .prompt_wire import PromptWireCommand, PromptWireReceipt, assess_prompt_receipt


@dataclass(frozen=True)
class PromptTransaction:
    command: PromptWireCommand
    status: str
    reason: str
    receipt: PromptWireReceipt | None = None


class PromptChannel:
    def __init__(self, writer: Callable[[bytes], int], generation: str, *, session: int | None = None,
                 max_age: float = 5.0):
        if type(generation) is not str or not generation:
            raise ValueError('connection generation required')
        self.session = session if session is not None else secrets.randbits(128) or 1
        PromptWireCommand('bind', 1, self.session).encode()
        if type(max_age) not in (int, float) or not math.isfinite(max_age) or max_age <= 0:
            raise ValueError('max_age must be positive and finite')
        self.writer, self.max_age = writer, max_age
        self.observer = ProtocolObserver(generation)
        self.generation = generation
        self._lock = threading.RLock()
        self._sequence = 0
        self._pending = None
        self._snapshot = None
        self._observed_at = None
        self._last_time = None
        self.bound = False
        self.state = 'ready'
        self.invalidation_reason = None
        self.blocker = None
        self.latest: PromptTransaction | None = None

    def snapshot(self):
        """Immutable last accepted firmware snapshot and its observation time."""
        with self._lock:
            return self._snapshot, self._observed_at

    def _time(self, now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError('finite observation time required')
        if self._last_time is not None and now < self._last_time:
            self._uncertain('clock_moved_backward')
            raise ValueError('observation time moved backward')
        self._last_time = now

    def _uncertain(self, reason, command=None):
        self.invalidation_reason = reason
        if self._pending:
            command, _ = self._pending
            self.latest = PromptTransaction(command, 'outcome_unknown', reason)
        elif command is not None:
            self.latest = PromptTransaction(command, 'outcome_unknown', reason)
        self._pending = None
        self._snapshot = self._observed_at = None
        self.bound = False
        self.state = 'uncertain'

    def dispatch(self, kind: str, *, now: float, deadline: float, prompt=0, choice=0) -> PromptTransaction:
        with self._lock:
            self._time(now)
            if self.state != 'ready' or self._pending:
                raise ValueError('channel is not ready')
            if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= now:
                raise ValueError('future finite deadline required')
            if self._sequence == 0xffffffff:
                raise ValueError('request sequence exhausted')
            if kind in ('bind', 'respond'):
                if self._snapshot is None or now - self._observed_at > self.max_age:
                    raise ValueError('fresh protocol inspection required')
                if kind == 'bind':
                    if self.bound or self._snapshot.session:
                        raise ValueError('fresh unbound firmware required; reconnect is unqualified')
                else:
                    if not self.bound or self._snapshot.session != self.session:
                        raise ValueError('session is not bound')
                    if (not self._snapshot.active or not self._snapshot.shown
                            or prompt != self._snapshot.prompt or type(choice) is not int
                            or not 0 <= choice < self._snapshot.buttons):
                        raise ValueError('prompt or choice is not current')
                    if self._snapshot.reason not in (1, 2, 4, 5):
                        raise ValueError('unsupported prompt reason')
            command = PromptWireCommand(kind, self._sequence + 1,
                                        0 if kind == 'inspect' else self.session, prompt, choice)
            payload = command.encode()
            self._sequence += 1
            self._pending = command, deadline
            self.latest = PromptTransaction(command, 'running', 'awaiting_receipt')
            self.state = 'busy'
            try:
                written = self.writer(payload)
                if type(written) is not int or written != len(payload):
                    self._uncertain('partial_or_unconfirmed_write', command)
            except Exception:
                self._uncertain('write_exception', command)
            return self.latest

    def tick(self, now: float):
        with self._lock:
            self._time(now)
            if self._pending and now >= self._pending[1]:
                self._uncertain('receipt_deadline_expired')
            return self.latest

    def disconnect(self, now: float):
        with self._lock:
            self._time(now)
            self._uncertain('disconnected')
            self.observer.reset(now)
            self.state = 'disconnected'
            return self.latest

    def invalidate(self, reason: str, *, now: float):
        """Invalidate authority after transport evidence loss without claiming USB loss."""
        if type(reason) is not str or not 1 <= len(reason) <= 128:
            raise ValueError('bounded invalidation reason required')
        with self._lock:
            self._time(now)
            self._uncertain(reason)
            return self.latest

    def feed(self, data: bytes, *, now: float):
        with self._lock:
            self.tick(now)
            if self.state in ('uncertain', 'disconnected'):
                return self.latest
            for event in self.observer.feed(data, now):
                if event.kind in ('fault', 'sensor_suspect'):
                    # A protocol receipt cannot establish fault recovery. Latch
                    # the observation and require enclosing recovery qualification;
                    # later acknowledgments or normal temperatures cannot clear it.
                    self.blocker = event
                    self._uncertain('firmware_fault' if event.kind == 'fault' else 'sensor_suspect',
                                    self.latest.command if self.latest else None)
                    break
                if event.kind in ('protocol_error', 'connection_invalidated'):
                    self._uncertain(event.kind)
                    break
                if event.kind != 'prompt_receipt':
                    continue
                receipt = PromptWireReceipt(**event.data)
                if receipt.request == 0:
                    self._uncertain('uncorrelated_transport_error')
                    break
                if not self._pending:
                    continue
                command, deadline = self._pending
                result = assess_prompt_receipt(command, receipt, expected_connection=self.generation,
                    current_connection=event.generation, deadline=deadline, observed_at=now)
                if result.outcome == 'unrelated':
                    continue
                if result.outcome == 'outcome_unknown':
                    self._uncertain(result.reason)
                    break
                if self.bound and receipt.session != self.session:
                    self._uncertain('session_changed')
                    break
                self.latest = PromptTransaction(command, result.outcome, result.reason, receipt)
                self._pending = None
                self.state = 'ready'
                if result.outcome == 'accepted':
                    self._snapshot, self._observed_at = receipt, now
                    if command.kind == 'bind':
                        self.bound = True
                else:
                    self._snapshot = self._observed_at = None
            return self.latest
