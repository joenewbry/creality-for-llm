"""Experimental prompt command encoder; not yet connected to physical transport.

Lines use a strict unnumbered M876 extension. A sender must wait for the
versioned protocol receipt, never treat ordinary G-code ``ok`` as success.
"""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PromptWireCommand:
    kind: Literal['inspect', 'bind', 'respond']
    request: int
    session: int = 0
    prompt: int = 0
    choice: int = 0

    @classmethod
    def decode(cls, data: bytes) -> 'PromptWireCommand':
        """Accept only canonical complete frames, for the priority write boundary."""
        import re
        if type(data) is not bytes or len(data) > 66:
            raise ValueError('invalid prompt command frame')
        match = re.fullmatch(rb'M876 V1 (I|B[0-9a-f]{32}|R[0-9a-f]{32} P[0-9a-f]{8} S[0-9a-f]{2}) Q([0-9a-f]{8})\n', data)
        if not match:
            raise ValueError('invalid prompt command frame')
        body, request = match.groups()
        if body == b'I':
            command = cls('inspect', int(request, 16))
        elif body.startswith(b'B'):
            command = cls('bind', int(request, 16), int(body[1:], 16))
        else:
            session, prompt, choice = body.split(b' ')
            command = cls('respond', int(request, 16), int(session[1:], 16), int(prompt[1:], 16), int(choice[1:], 16))
        if command.encode() != data:
            raise ValueError('noncanonical prompt command')
        return command

    def encode(self) -> bytes:
        def integer(name: str, value: int, minimum: int, maximum: int) -> None:
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{name} is outside its wire range')

        integer('request', self.request, 1, 0xffffffff)
        integer('session', self.session, 0, (1 << 128) - 1)
        integer('prompt', self.prompt, 0, 0xffffffff)
        integer('choice', self.choice, 0, 255)
        if self.kind == 'inspect':
            if self.session or self.prompt or self.choice:
                raise ValueError('inspect has no session, prompt or choice fields')
            body = 'I'
        elif self.kind in ('bind', 'respond'):
            if not self.session:
                raise ValueError('session must be nonzero')
            if self.kind == 'bind':
                if self.prompt or self.choice:
                    raise ValueError('bind has no prompt or choice fields')
                body = f'B{self.session:032x}'
            else:
                if not self.prompt:
                    raise ValueError('prompt must be nonzero')
                body = f'R{self.session:032x} P{self.prompt:08x} S{self.choice:02x}'
        else:
            raise ValueError('unknown command kind')
        return f'M876 V1 {body} Q{self.request:08x}\n'.encode('ascii')


@dataclass(frozen=True)
class PromptWireReceipt:
    request: int
    result: int
    duplicate: bool
    session: int
    prompt: int
    active: bool
    shown: bool
    buttons: int
    reason: int
    waiting: bool

    @classmethod
    def decode(cls, line: str) -> 'PromptWireReceipt':
        import re
        # Decode a complete line without its transport delimiter. Do not strip
        # or normalize bytes into an otherwise valid protocol receipt.
        if type(line) is not str or len(line) > 128:
            raise ValueError('invalid prompt receipt')
        match = re.fullmatch(
            r'//llm:v1 Q([0-9a-f]{8}) C([0-9a-f]{2}) D([01]) '
            r'T([0-9a-f]{32}) P([0-9a-f]{8}) A([01]) H([01]) '
            r'B([0-9a-f]{2}) R([0-9a-f]{2}) W([01])', line, flags=re.ASCII)
        if not match:
            raise ValueError('invalid prompt receipt')
        q, c, d, t, p, a, h, b, r, w = (int(value, 16) for value in match.groups())
        if c not in {*range(10), 0x80, 0x81, 0x82, 0x83} or r > 5:
            raise ValueError('unsupported prompt receipt value')
        if (q == 0) != (c in (0x80, 0x81)) or (d and c in (0x80, 0x81, 0x82, 0x83)):
            raise ValueError('inconsistent receipt correlation')
        if (not a and (h or b or r)) or (a and (not p or not r)):
            raise ValueError('inconsistent prompt snapshot')
        return cls(q, c, bool(d), t, p, bool(a), bool(h), b, r, bool(w))


@dataclass(frozen=True)
class PromptReceiptAssessment:
    outcome: Literal['accepted', 'rejected', 'outcome_unknown', 'unrelated']
    reason: str


def assess_prompt_receipt(command: PromptWireCommand, receipt: PromptWireReceipt,
                          *, expected_connection: str, current_connection: str,
                          deadline: float, observed_at: float) -> PromptReceiptAssessment:
    """Correlate a receipt, never claim physical completion or replay a command.

    The operation journal/transport must supply the original command, connection
    and deadline. This function does not open a device or authorize dispatch.
    """
    import math
    command.encode()  # validate the original command before correlating
    if (type(expected_connection) is not str or not expected_connection
            or type(current_connection) is not str or expected_connection != current_connection):
        return PromptReceiptAssessment('outcome_unknown', 'connection_changed')
    if (type(deadline) not in (int, float) or type(observed_at) not in (int, float)
            or not math.isfinite(deadline) or not math.isfinite(observed_at)
            or observed_at >= deadline):
        return PromptReceiptAssessment('outcome_unknown', 'expired_or_invalid_observation')
    if receipt.request == 0:
        return PromptReceiptAssessment('outcome_unknown', 'uncorrelated_transport_error')
    if receipt.request != command.request:
        return PromptReceiptAssessment('unrelated', 'different_request')
    # A stale/conflicting request may refer to an earlier execution. Rejection
    # of this retry is not proof that the intended operation never happened.
    if receipt.result in (0x82, 0x83):
        return PromptReceiptAssessment('outcome_unknown', 'request_history_conflict')
    if receipt.result:
        return PromptReceiptAssessment('rejected', f'firmware_rejection_{receipt.result:02x}')
    if command.kind != 'inspect' and receipt.session != command.session:
        return PromptReceiptAssessment('outcome_unknown', 'session_mismatch')
    return PromptReceiptAssessment('accepted', 'protocol_acceptance_only')
