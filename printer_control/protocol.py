"""Bounded Marlin serial observation. Parsing never authorizes a machine action.

Stock host prompts lack a firmware token: they are observable but not race-safe
for remote responses. The typed controller must negotiate token support first.
"""
from dataclasses import dataclass, asdict
import re
import math
import uuid
from .prompt_wire import PromptWireReceipt


@dataclass(frozen=True)
class Event:
    sequence: int
    generation: str
    kind: str
    observed_at: float
    data: dict


class ProtocolObserver:
    MAX_LINE = 2048
    MAX_BUTTONS = 8

    def __init__(self, generation: str | None = None):
        self.generation = generation or uuid.uuid4().hex
        self.sequence = 0
        self._buffer = bytearray()
        self._discard = False
        self._prompt = None
        self.capabilities: dict[str, bool] = {}

    def _event(self, kind, now, **data):
        self.sequence += 1
        return Event(self.sequence, self.generation, kind, now, data)

    def reset(self, now: float, reason: str = "disconnected") -> list[Event]:
        events = []
        if self._prompt:
            events.append(self._event("prompt_invalidated", now, prompt_id=self._prompt["id"], reason=reason))
        self._prompt = None
        self._buffer.clear()
        self._discard = False
        self.capabilities.clear()
        events.append(self._event("connection_invalidated", now, reason=reason, physical_state="unknown"))
        self.generation = uuid.uuid4().hex
        return events

    def end_capture(self, now: float) -> list[Event]:
        """Retire local observations without asserting a firmware disconnect/reset.

        Never parse a partial final line by inventing a newline. A new capture
        uses a new observer; this method only finalizes this capture's evidence.
        """
        events = []
        if self._buffer or self._discard:
            events.append(self._event("protocol_error", now, reason="truncated_line",
                                      buffered_bytes=len(self._buffer), discarding=self._discard))
        self._buffer.clear()
        self._discard = False
        self._invalidate_prompt(events, now, "capture_ended")
        self.capabilities.clear()
        return events

    def feed(self, data: bytes, now: float) -> list[Event]:
        events = []
        for byte in data:
            if byte in (10, 13):
                if self._discard:
                    self._discard = False
                    continue
                if self._buffer:
                    raw = bytes(self._buffer)
                    self._buffer.clear()
                    try:
                        line = raw.decode("ascii", errors="strict")
                    except UnicodeDecodeError:
                        events.append(self._event("protocol_error", now, reason="non_ascii_line"))
                        self._invalidate_prompt(events, now, "malformed_stream")
                        continue
                    events.extend(self._line(line if line.lstrip().startswith("//llm:") else line.strip(), now))
            elif not self._discard:
                self._buffer.append(byte)
                if len(self._buffer) > self.MAX_LINE:
                    self._buffer.clear()
                    self._discard = True
                    events.append(self._event("protocol_error", now, reason="line_too_long"))
                    self._invalidate_prompt(events, now, "malformed_stream")
        return events

    def _invalidate_prompt(self, events, now, reason):
        if self._prompt:
            events.append(self._event("prompt_invalidated", now, prompt_id=self._prompt["id"], reason=reason))
            self._prompt = None

    def _line(self, line, now):
        events = []
        if line.lstrip().startswith("//llm:"):
            try:
                receipt = PromptWireReceipt.decode(line)
            except ValueError:
                events.append(self._event("protocol_error", now, reason="invalid_prompt_receipt"))
                self._invalidate_prompt(events, now, "malformed_stream")
            else:
                events.append(self._event("prompt_receipt", now, **asdict(receipt)))
            return events
        if line == "start":
            return self.reset(now, "controller_reset")
        if re.match(r"^ok(?:\s|$)", line):
            events.append(self._event("ack", now, meaning="protocol_only"))
        cap = re.fullmatch(r"Cap:([A-Z_]+):([01])", line)
        if cap:
            self.capabilities[cap[1]] = cap[2] == "1"
            return [self._event("capability", now, name=cap[1], enabled=cap[2] == "1")]
        if line.startswith("FIRMWARE_NAME:"):
            return [self._event("firmware_identity", now, report=line)]
        temperatures = re.findall(r"(?:^|\s)(T\d*|B):\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", line)
        if temperatures:
            values = {key: {"actual": float(actual), "target": float(target)} for key, actual, target in temperatures}
            if any(not math.isfinite(v) for pair in values.values() for v in pair.values()):
                return [self._event('protocol_error',now,reason='nonfinite_temperature')]
            events.append(self._event("temperature", now, values=values))
            # Negative ambient values on this bench are diagnostic, not hidden by clamping.
            if any(v["actual"] < 0 for v in values.values()):
                events.append(self._event("sensor_suspect", now, reason="negative_temperature", heating_permitted=False))
        number=r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))'
        position=re.fullmatch(r'(?:ok\s+)?X:'+number+r'\s+Y:'+number+r'\s+Z:'+number+r'\s+E:'+number+r'(?:\s+Count\b.*)?',line)
        if position:
            values=dict(zip(('x','y','z','e'),map(float,position.groups())))
            if not all(math.isfinite(v) for v in values.values()):
                return [self._event('protocol_error',now,reason='nonfinite_reported_position')]
            events.append(self._event('reported_position',now,position=values,units=None,
                position_kind='controller_report',independent_measurement=False,homing_state=None,planner_completion=None))
        resend = re.fullmatch(r"(?:Resend:\s*|rs\s+)(\d+)", line, re.I)
        if resend:
            return [self._event("resend_requested", now, line_number=int(resend[1]))]
        if line.startswith("Error:") or re.search(r"thermal runaway|heating failed|printer halted|kill\(\) called", line, re.I):
            # Checksum errors are recoverable transport events, not thermal faults.
            kind = "transport_error" if re.search(r"checksum|Line Number", line, re.I) else "fault"
            events.append(self._event(kind, now, message=line))
            if kind == "fault":
                self._invalidate_prompt(events, now, "fault")
            return events
        if line.startswith("echo:busy:") or line.startswith("busy:"):
            return [self._event("busy", now, message=line.split("busy:", 1)[1].strip())]
        if line.startswith("//action:"):
            action = line[len("//action:"):]
            command, _, text = action.partition(" ")
            if command == "prompt_end":
                self._invalidate_prompt(events, now, "firmware_end")
                # Ending a prompt does not prove that job motion resumed.
            elif command == "prompt_begin":
                self._invalidate_prompt(events, now, "superseded")
                self._prompt = {"id": f"{self.generation}:{self.sequence + 1}", "message": text, "buttons": [], "shown": False}
                events.append(self._event("prompt_assembling", now, prompt_id=self._prompt["id"], message=text))
            elif command == "prompt_button":
                if not self._prompt or self._prompt["shown"] or len(self._prompt["buttons"]) >= self.MAX_BUTTONS:
                    events.append(self._event("protocol_error", now, reason="unexpected_prompt_button"))
                    self._invalidate_prompt(events, now, "malformed_prompt")
                else:
                    self._prompt["buttons"].append(text)
            elif command == "prompt_show":
                if not self._prompt or self._prompt["shown"]:
                    events.append(self._event("protocol_error", now, reason="unexpected_prompt_show"))
                    self._invalidate_prompt(events, now, "malformed_prompt")
                else:
                    self._prompt["shown"] = True
                    events.append(self._event("prompt_open", now, prompt_id=self._prompt["id"],
                                              message=self._prompt["message"], buttons=list(self._prompt["buttons"]),
                                              firmware_token=None, remote_response_safe=False))
            else:
                # These are requests/notifications, not proof of completed host actions.
                events.append(self._event("host_action", now, action=command, message=text, verified=False))
            return events
        if not events:
            events.append(self._event("message", now, text=line))
        return events


def event_dict(event: Event) -> dict:
    return asdict(event)

