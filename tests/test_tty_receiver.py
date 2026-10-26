"""Single-reader fanout with bounded bytes, timestamps and authority fences."""
import threading
import pytest
from printer_control.tty_receiver import TTYReceiveHub


def make(chunks,*,copy_jobs=True,capacity=65536):
    reads=[];now=[10.];valid=[True]
    def read():reads.append(1);return chunks.pop(0) if chunks else b''
    def check():
        if not valid[0]:raise OSError('assignment lost')
    hub=TTYReceiveHub(read,check,lambda:now[0],copy_jobs=copy_jobs,capacity=capacity)
    return hub,reads,now,valid


def test_sender_read_copies_prompt_without_an_extra_descriptor_read_or_new_timestamp():
    hub,reads,now,_=make([b'ok\n//llm:fixture\n'])
    assert hub.job_readline()==b'ok\n'
    now[0]=16.;batch=hub.prompt_pump()
    assert batch.data==b'ok\n//llm:fixture\n' and batch.observed_at==10.
    assert hub.job_readline()==b'//llm:fixture\n' and len(reads)==1


def test_prompt_read_preserves_every_sender_line_and_fragment_without_ack_injection():
    hub,reads,_,_=make([b'ok\r\nRes',b'end: 42\n'])
    assert hub.prompt_pump().data==b'ok\r\nRes'
    assert hub.job_readline()==b'ok\r\n'
    assert hub.job_readline()==b'Resend: 42\n'
    assert hub.prompt_pump().data==b'end: 42\n' and len(reads)==2


def test_incomplete_line_survives_poll_timeout_without_fabricated_newline():
    hub,reads,_,_=make([b'part',b'',b'ial\n'])
    assert hub.job_readline()==b''
    assert hub.job_readline()==b''
    assert hub.job_readline()==b'partial\n' and len(reads)==3


@pytest.mark.parametrize('lane',['prompt','job'])
def test_assignment_loss_refuses_cached_evidence_and_never_reads_or_replays(lane):
    hub,reads,_,valid=make([b'ok\n'])
    if lane=='prompt':assert hub.job_readline()==b'ok\n'
    else:assert hub.prompt_pump().data==b'ok\n'
    valid[0]=False
    with pytest.raises(OSError):(hub.prompt_pump if lane=='prompt' else hub.job_readline)()
    valid[0]=True
    with pytest.raises(OSError):hub.prompt_pump()
    assert hub.fault=='authority_lost' and len(reads)==1


@pytest.mark.parametrize('data',[None,'ok\n',b'x'*4097,b'\xff\n',b'x'*2049+b'\n'])
def test_malformed_or_oversized_input_permanently_faults_both_lanes(data):
    hub,reads,_,_=make([data,b'ok\n'])
    with pytest.raises((ValueError,OSError,UnicodeDecodeError)):hub.job_readline()
    with pytest.raises(OSError):hub.prompt_pump()
    assert len(reads)==1 and hub.fault


def test_unconsumed_job_copy_overflow_faults_prompt_lane_too():
    hub,reads,_,_=make([b'x'*100+b'\n',b'y'*100+b'\n'],capacity=128)
    assert hub.prompt_pump().data
    with pytest.raises(ValueError):hub.prompt_pump()
    with pytest.raises(OSError):hub.job_readline()
    assert len(reads)==2


def test_prompt_only_mode_does_not_accumulate_unused_job_copy():
    hub,reads,_,_=make([b'ok\n']*100,copy_jobs=False,capacity=128)
    for _ in range(100):assert hub.prompt_pump().data==b'ok\n'
    with pytest.raises(ValueError):hub.job_readline()
    assert len(reads)==100 and not hub.fault


def test_authority_loss_during_read_cannot_release_received_bytes():
    valid=[True]
    def check():
        if not valid[0]:raise OSError('lost during receive')
    def read():valid[0]=False;return b'ok\n'
    hub=TTYReceiveHub(read,check,lambda:10.,copy_jobs=True)
    with pytest.raises(OSError):hub.job_readline()
    assert hub.fault=='authority_lost'


def test_close_discards_both_queues_and_cannot_restart():
    hub,reads,_,_=make([b'ok\n'])
    hub.prompt_pump();hub.close();hub.close()
    with pytest.raises(OSError):hub.job_readline()
    with pytest.raises(OSError):hub.prompt_pump()
    assert len(reads)==1


def test_concurrent_consumers_never_enter_the_descriptor_reader_together():
    guard=threading.Lock();entered=threading.Event();release=threading.Event();reads=[];results=[]
    def read():
        assert guard.acquire(blocking=False)
        try:
            reads.append(1);entered.set();assert release.wait(.1)
            return b'ok\n'
        finally:guard.release()
    hub=TTYReceiveHub(read,lambda:None,lambda:10.,copy_jobs=True)
    first=threading.Thread(target=lambda:results.append(hub.prompt_pump().data));first.start()
    assert entered.wait(1)
    second=threading.Thread(target=lambda:results.append(hub.job_readline()));second.start()
    release.set();first.join(1);second.join(1)
    assert not first.is_alive() and not second.is_alive()
    assert results==[b'ok\n',b'ok\n'] and len(reads)==1


def test_authority_check_delay_does_not_renew_captured_byte_age():
    now=[10.];received=[False]
    def read():received[0]=True;return b'ok\n'
    def check():
        if received[0]:now[0]=16.
    hub=TTYReceiveHub(read,check,lambda:now[0],copy_jobs=True)
    batch=hub.prompt_pump()
    assert batch.data==b'ok\n' and batch.observed_at==10. and now[0]==16.


def test_failed_receive_fences_a_concurrent_queued_write_before_it_can_emit():
    entered=threading.Event();release=threading.Event();writes=[];errors=[]
    def read():
        entered.set();assert release.wait(.1)
        raise OSError('receive lost')
    hub=TTYReceiveHub(read,lambda:None,lambda:10.,copy_jobs=True)
    def attempt(fn):
        try:fn()
        except OSError as exc:errors.append(exc)
    reader=threading.Thread(target=lambda:attempt(hub.prompt_pump))
    writer=threading.Thread(target=lambda:attempt(lambda:hub.write_frame(lambda d:writes.append(d) or len(d),b'M400\n')))
    reader.start();assert entered.wait(1);writer.start();release.set()
    reader.join(1);writer.join(1)
    assert not reader.is_alive() and not writer.is_alive()
    assert len(errors)==2 and not writes and hub.fault


@pytest.mark.parametrize('written',[None,1])
def test_partial_or_unconfirmed_write_discards_both_receive_lanes(written):
    hub,reads,_,_=make([b'ok\n'])
    hub.prompt_pump()
    with pytest.raises(OSError):hub.write_frame(lambda _:written,b'M400\n')
    with pytest.raises(OSError):hub.job_readline()
    assert len(reads)==1 and hub.fault=='write_interrupted'


def test_authority_lost_during_write_leaves_one_attempt_and_no_replay():
    hub,_,_,valid=make([]);writes=[]
    def write(data):writes.append(data);valid[0]=False;return len(data)
    with pytest.raises(OSError):hub.write_frame(write,b'M400\n')
    valid[0]=True
    with pytest.raises(OSError):hub.write_frame(write,b'M400\n')
    assert len(writes)==1 and hub.fault=='authority_lost'


def test_claimed_job_reader_leaves_prompt_poll_passive_and_cannot_be_claimed_twice():
    hub,reads,now,_=make([b'ok\n'])
    hub.claim_job_reader()
    assert hub.prompt_pump().data==b'' and not reads
    assert hub.job_readline()==b'ok\n'
    now[0]=16.;batch=hub.prompt_pump()
    assert batch.observed_at==10. and batch.data==b'ok\n' and len(reads)==1
    with pytest.raises(ValueError):hub.claim_job_reader()


def test_expired_gate_waiter_does_not_block_a_successor():
    from printer_control.tty_receiver import _OrderedGate
    gate=_OrderedGate();assert gate.acquire(timeout=.1)
    results=[]
    first=threading.Thread(target=lambda:results.append(gate.acquire(timeout=.01)))
    first.start();first.join(1);assert not first.is_alive() and results==[False]
    acquired=threading.Event()
    def successor():
        if gate.acquire(timeout=.5):acquired.set();gate.release()
    second=threading.Thread(target=successor);second.start();gate.release()
    second.join(1);assert not second.is_alive() and acquired.is_set()
