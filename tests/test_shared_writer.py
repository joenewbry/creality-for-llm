"""Concurrent frame ownership and uncertainty propagation, without serial I/O."""
import threading
import pytest
from printer_control.shared_writer import SharedFrameWriter
from printer_control.prompt_wire import PromptWireCommand
from printer_control.prompt_channel import PromptChannel

PROMPT = PromptWireCommand('inspect', 1).encode()
JOB = b'N10 G1 X20*54\n'


@pytest.mark.parametrize('command', [PromptWireCommand('inspect', 1),
    PromptWireCommand('bind', 2, 123), PromptWireCommand('respond', 3, 123, 4, 0)])
def test_canonical_command_roundtrip(command):
    assert PromptWireCommand.decode(command.encode()) == command


@pytest.mark.parametrize('data', [b'M876 S0\n', b'M112\n', PROMPT[:-1],
    PROMPT+b'G1 X1\n', b'N1 '+PROMPT, PROMPT.replace(b'\n', b'\r\n'),
    PROMPT.replace(b'00000001', b'00000000'), b'M876 V1 B'+b'0'*32+b' Q00000001\n'])
def test_prompt_lane_rejects_noncanonical_input_without_write(data):
    calls = []
    writer = SharedFrameWriter(lambda d: calls.append(d) or len(d))
    with pytest.raises(ValueError): writer.write_prompt(data)
    assert calls == [] and writer.fault is None


@pytest.mark.parametrize('data', [b'G1 X1', b'G1 X1\nG1 X2\n', b'\n', b'G1\r\n',
    b'G1 \xff\n', PROMPT, b'N12 M876 S0*12\n', b' m876 S0\n'])
def test_job_lane_requires_one_non_prompt_frame(data):
    calls = []
    writer = SharedFrameWriter(lambda d: calls.append(d) or len(d))
    with pytest.raises(ValueError): writer.write_job(data)
    assert not calls


def test_competing_writers_cannot_interleave_even_if_underlying_write_is_slow():
    entered, release, waiting = threading.Event(), threading.Event(), threading.Event()
    wire, outcomes = bytearray(), []
    def raw(data):
        wire.extend(data[:3])
        if data == JOB:
            entered.set()
            assert release.wait(5)
        wire.extend(data[3:])
        return len(data)
    writer = SharedFrameWriter(raw, lock_timeout=5.)
    first = threading.Thread(target=lambda: outcomes.append(writer.write_job(JOB)))
    def prompt():
        waiting.set()
        outcomes.append(writer.write_prompt(PROMPT))
    second = threading.Thread(target=prompt)
    first.start(); assert entered.wait(5)
    second.start(); assert waiting.wait(5)
    assert bytes(wire) == JOB[:3]
    release.set(); first.join(5); second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert bytes(wire) == JOB+PROMPT and sorted(outcomes) == sorted([len(JOB),len(PROMPT)])


@pytest.mark.parametrize('result', [None, True, 0, 3, -1, 10000])
def test_partial_or_unknown_write_poison_both_lanes_without_retry(result):
    calls = []
    writer = SharedFrameWriter(lambda d: calls.append(d) or result)
    with pytest.raises(OSError): writer.write_job(JOB)
    assert writer.fault == 'partial_or_unconfirmed_write'
    for send, data in [(writer.write_job, JOB), (writer.write_prompt, PROMPT)]:
        with pytest.raises(OSError): send(data)
    assert calls == [JOB]


def test_exception_after_prefix_poisoning_reaches_prompt_channel():
    wire = bytearray()
    def raw(data):
        wire.extend(data[:3])
        raise OSError('unknown write disposition')
    writer = SharedFrameWriter(raw)
    with pytest.raises(OSError): writer.write_job(JOB)
    channel = PromptChannel(writer.write_prompt, 'software')
    result = channel.dispatch('inspect', now=1., deadline=5.)
    assert result.status == 'outcome_unknown' and channel.state == 'uncertain'
    assert bytes(wire) == JOB[:3]


def test_reentrant_write_times_out_without_injecting_a_second_frame():
    calls = []
    writer = None
    def raw(data):
        calls.append(data)
        with pytest.raises(TimeoutError): writer.write_prompt(PROMPT)
        return len(data)
    writer = SharedFrameWriter(raw, lock_timeout=.01)
    assert writer.write_job(JOB) == len(JOB)
    assert calls == [JOB] and writer.fault is None
