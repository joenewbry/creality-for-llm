"""Active-stream disconnect experiment, using only the software serial device."""
import time


def exercise(sender, device, observer, call_tool, submit, request, start_request, start_receipt, expected_moves, *, reset=False, old_resend=False):
    before = call_tool('job.inspect', {})
    assert before['job_state'] == 'running'
    completed_before = list(device.completed_moves)
    assert 0 < len(completed_before) < len(expected_moves)
    stale_resume = request('job.resume', 'resume-old-generation')
    injected_at = time.monotonic()
    device._debugTrigger('trigger_resend_checksum' if old_resend else ('reset' if reset else 'drop_connection'))
    deadline = time.monotonic()+10
    while ((not any('External reset?' in line for line in observer.logs)) if reset else
           sender.getState() not in (sender.STATE_CLOSED, sender.STATE_CLOSED_WITH_ERROR)):
        assert time.monotonic() < deadline, 'sender failed to observe disconnect'
        time.sleep(.02)
    detected_after_seconds = time.monotonic() - injected_at
    after = call_tool('job.inspect', {})
    assert after['job_state'] == 'outcome_unknown'
    assert after['connection_generation'] != before['connection_generation']
    assert not after['resumable'] and not observer.done
    if old_resend:
        assert device.resend_guard.fault == 'resend_outside_unacknowledged_window'
        assert 'Host resend boundary violated' in after['blocking_fault']['message']
    stale = submit(stale_resume)
    assert stale['status'] == 'rejected'
    assert stale['result']['reason'] == 'stale_connection'
    if reset:
        # Let the sender finish its own reset handshake/cancellation before
        # measuring whether our retries cause additional dispatch.
        time.sleep(.3)
    send_count = sum(line.startswith('Send:') for line in observer.logs)
    assert send_count > 0, 'serial-write observer must have seen the active stream'
    assert submit(start_request) == start_receipt  # historical completion, not another start
    refused = []
    for name in ('job.start', 'job.pause', 'job.resume', 'job.cancel'):
        result = submit(request(name, name+'-after-active-loss'))
        assert result['status'] == 'rejected', result
        refused.append(result)
    time.sleep(.2)
    assert sum(line.startswith('Send:') for line in observer.logs) == send_count
    completed_after = list(device.completed_moves)
    if not reset:
        assert completed_after == expected_moves[:len(completed_after)]
    else:
        # Reset clears the device buffer; surviving execution need not be a
        # contiguous prefix. Never use this cursor for automatic continuation.
        assert completed_after[:len(completed_before)] == completed_before
        indices = [expected_moves.index(line) for line in completed_after]
        assert indices == sorted(set(indices)), 'executed moves were duplicated or reordered'
    assert len(completed_after) < len(expected_moves)
    history = call_tool('printer.observations', {'after': 0, 'limit': 1000})
    assert history['observations'][-1]['snapshot']['job_state'] == 'outcome_unknown'
    return dict(evidence_type='old-line resend refused' if old_resend else ('active virtual controller reset' if reset else 'active virtual stream disconnect'), starting_state=before,
                ending_state=after, completed_moves_before=len(completed_before),
                completed_moves_observed_after=len(completed_after),
                completed_moves_match_prefix=completed_after == expected_moves[:len(completed_after)],
                executed_move_indices=[expected_moves.index(line) for line in completed_after],
                execution_reconciliation_required=True, stale_request=stale,
                rejected_operations=refused, original_start_receipt_preserved=True,
                new_serial_writes_after_disconnect=0, serial_writes_before_retries=send_count,
                disconnect_detected_after_seconds=detected_after_seconds,
                stop_confirmed=False, reconnect_attempted=False,
                limits=['Already buffered device work may continue after communication loss',
                        'No physical printer or reconnect recovery qualification'])
