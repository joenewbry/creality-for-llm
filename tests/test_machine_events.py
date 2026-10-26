import time
import pytest
from printer_control.journal import Journal, RequestRejected
from printer_control.machine_events import MachineEventRecorder


def wait_for(predicate):
    end = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < end
        time.sleep(.005)


@pytest.fixture
def recorder(tmp_path):
    capture = MachineEventRecorder(Journal(tmp_path/'journal.sqlite'), generation='first')
    yield capture
    capture.close()


def test_capture_without_polling_preserves_input_and_survives_restart(tmp_path):
    journal = Journal(tmp_path/'journal.sqlite')
    recorder = MachineEventRecorder(journal, generation='first')
    line = 'ok T:20 /0 B:18 /0\nError:Heating failed\nstart\n'
    assert recorder.received_hook(None, line) is line
    recorder.close()  # drains without any tool reads during capture
    page = recorder.read()
    kinds = [r['event']['kind'] for r in page['events']]
    assert kinds == ['capture_started','ack','temperature','fault','connection_invalidated','capture_closed']
    assert page['events'][-2]['event']['generation'] == 'first'
    assert page['events'][-1]['event']['generation'] != 'first'
    assert not page['health']['capturing'] and page['health']['fault'] is None
    assert not page['recovery_authority']
    later = MachineEventRecorder(journal, generation='second')
    try:
        historical = later.read(after=page['events'][0]['sequence'], limit=2)
        assert historical['events'] == page['events'][1:3]
        assert later.capture_id != recorder.capture_id
    finally:
        later.close()


def test_retention_has_explicit_cursor_gap_and_no_pagination_omissions(tmp_path):
    recorder = MachineEventRecorder(Journal(tmp_path/'journal.sqlite'), generation='g', retention=3)
    recorder.received_hook(None, 'ok\n'*8)
    recorder.close()
    first = recorder.read(limit=1)
    rest = recorder.read(after=first['next_after'])
    assert first['history_gap'] and not rest['history_gap']
    assert [r['sequence'] for r in first['events']+rest['events']] == [8,9,10]
    assert first['retained_from'] == 8 and first['retained_through'] == 10
    assert recorder.read(after=100)['cursor_ahead']


def test_queue_overflow_latches_loss_without_changing_sender_reply(recorder):
    recorder.capacity = 128
    with recorder._condition:
        recorder.received_hook(None, 'ok\n'*40)
        line = 'ok\n'*40
        assert recorder.received_hook(None, line) is line
    wait_for(lambda: recorder.read()['health']['fault'] is not None)
    recorder.close()
    page = recorder.read()
    assert page['health']['fault'] == 'receive_capture_overflow'
    assert not page['health']['capturing']
    assert page['events'][-1]['event']['kind'] == 'capture_lost'


def test_durable_write_failure_is_visible_and_not_retried(recorder):
    writes = []
    def fail(events):
        writes.append(events)
        raise OSError('disk unavailable')
    recorder._persist = fail
    recorder.received_hook(None, 'ok\n')
    wait_for(lambda: recorder.read()['health']['fault'] is not None)
    recorder.close()
    assert len(writes) == 1
    assert recorder.read()['health']['fault'] == 'event_capture_failed'
    assert not recorder.read()['health']['capturing']


@pytest.mark.parametrize('line', [None, 1])
def test_invalid_hook_input_is_returned_unchanged_and_marks_loss(recorder, line):
    assert recorder.received_hook(None, line) is line
    recorder.close()
    assert recorder.read()['health']['fault'] == 'invalid_receive_observation'


@pytest.mark.parametrize('arguments', [dict(after=True),dict(after=-1),dict(limit=0),dict(limit=1001)])
def test_invalid_event_queries(recorder, arguments):
    with pytest.raises(RequestRejected):
        recorder.read(**arguments)


def test_malformed_firmware_lines_are_observed_not_silently_dropped(recorder):
    recorder.received_hook(None, 'garbage\N{SNOWMAN}\n'+'x'*2100+'\nok\n')
    recorder.close()
    kinds = [r['event']['kind'] for r in recorder.read()['events']]
    assert kinds == ['capture_started','protocol_error','protocol_error','ack','capture_closed']


def test_page_byte_limit_preserves_next_cursor(recorder):
    recorder._persist([dict(kind='fixture',generation='g',observed_at=1,data={'blob':'x'*100000,'index':i}) for i in range(3)])
    first = recorder.read(after=1, limit=1000)
    rest = recorder.read(after=first['next_after'], limit=1000)
    assert len(first['events']) == 2 and len(rest['events']) == 1
    assert [row['event']['data']['index'] for row in first['events']+rest['events']] == [0,1,2]


def test_close_does_not_claim_worker_stopped_while_persistence_is_blocked(recorder):
    import threading
    entered, release = threading.Event(), threading.Event()
    persist = recorder._persist
    def blocked(events):
        entered.set()
        assert release.wait(2)
        persist(events)
    recorder._persist = blocked
    recorder.received_hook(None, 'ok\n')
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError):
            recorder.close(timeout=.01)
        assert recorder._worker.is_alive()
        assert not recorder.read()['health']['capturing']
    finally:
        release.set()
        recorder.close()


def test_close_reports_partial_reply_as_loss_without_inventing_ack(recorder):
    recorder.received_hook(None, 'ok T:20 /0')
    recorder.close()
    page = recorder.read()
    assert [row['event']['kind'] for row in page['events']] == [
        'capture_started', 'protocol_error', 'capture_lost']
    assert page['events'][1]['event']['data']['reason'] == 'truncated_line'
    assert page['health']['fault'] == 'incomplete_serial_line'


def test_close_invalidates_observed_prompt_without_claiming_firmware_end(recorder):
    recorder.received_hook(None, '//action:prompt_begin Wait\n//action:prompt_show\n')
    recorder.close()
    events = [row['event'] for row in recorder.read()['events']]
    assert [event['kind'] for event in events] == [
        'capture_started', 'prompt_assembling', 'prompt_open',
        'prompt_invalidated', 'capture_closed']
    assert events[-2]['data']['reason'] == 'capture_ended'
    assert events[-2]['data']['prompt_id'] == events[2]['data']['prompt_id']


def test_overlong_unterminated_reply_reports_incomplete_capture(recorder):
    recorder.received_hook(None, 'x' * 2100)
    recorder.close()
    page = recorder.read()
    assert page['health']['fault'] == 'incomplete_serial_line'
    assert [row['event']['data'].get('reason') for row in page['events']
            if row['event']['kind'] == 'protocol_error'] == ['line_too_long', 'truncated_line']


def test_fragmented_reply_completed_before_close_is_not_lost(recorder):
    recorder.received_hook(None, 'ok T:20')
    recorder.received_hook(None, ' /0\r\n')
    recorder.close()
    page = recorder.read()
    assert page['health']['fault'] is None
    assert [row['event']['kind'] for row in page['events']] == [
        'capture_started', 'ack', 'temperature', 'capture_closed']


def test_overflow_invalidates_prior_prompt_and_keeps_original_loss_reason(recorder):
    recorder.received_hook(None, '//action:prompt_begin Wait\n//action:prompt_show\n')
    wait_for(lambda: any(row['event']['kind'] == 'prompt_open' for row in recorder.read()['events']))
    recorder.received_hook(None, 'x' * (recorder.capacity + 1))
    recorder.close()
    page = recorder.read()
    assert page['health']['fault'] == 'receive_capture_overflow'
    assert [row['event']['kind'] for row in page['events']][-2:] == [
        'prompt_invalidated', 'capture_lost']
