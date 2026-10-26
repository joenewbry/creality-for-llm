"""Software print scenario and independent, read-only success evaluator."""
JOB_INSTRUCTIONS = '''You control only an OctoPrint SOFTWARE printer through the provided tools. Stage the available fixed job, start it, pause mid-stream, inspect it twice to confirm it stays paused, then resume and observe job_state completed. You choose every mutating tool call. Read capabilities and job.inspect for identity, file hash, job id, connection generation and state version; never invent them. Submit each operation with a unique request_id, current context and deadline=current epoch plus 60 seconds. Poll running receipts using operation.get. A completed start/resume operation does not mean the whole job completed. Read job.inspect until job_state completed with all_moves_completed_once_in_order true. During the pause, verify resumable and unchanged completed_moves across two observations. On stale_state refresh job.inspect before a NEW request. Respect other refusals. No physical printer is connected; do not claim physical qualification. Keep final summary brief and evidence-based.'''


def evaluate_job(status, events, observations):
    evidence = status.get('simulation_evidence', {})
    kinds = [e['detail'].get('evidence_type') for e in events.get('events', []) if e['status'] == 'completed']
    required = ['virtual_file_selected', 'virtual_sender_started',
                'virtual_sender_pause_barrier_and_position', 'virtual_in_place_resume']
    if kinds != required:
        return False
    paused = [o for o in observations if o['status'].get('job_state') == 'paused' and o['status'].get('resumable')]
    if len(paused) < 2 or paused[-1]['at'] - paused[0]['at'] < .5:
        return False
    counts = [o['status']['simulation_evidence']['completed_moves'] for o in paused]
    if not 0 < counts[0] < 80 or len(set(counts)) != 1:
        return False
    return (status.get('job_state') == 'completed' and
            evidence == dict(completed_moves=80, expected_moves=80,
                moves_match_expected_prefix=True, all_moves_completed_once_in_order=True,
                physical_serial_open_attempts=0))


COMBINED_INSTRUCTIONS = JOB_INSTRUCTIONS + ''' This server additionally supports a generic firmware prompt injected at move 10. After starting, observe fixture_prompt_waiting=true before requesting job.pause. That pause may remain running because the generic wait blocks the ordinary stream. While it is pending, use prompt.inspect, then prompt.bind, then prompt.respond for the exact current token and choice zero; poll EACH running prompt operation before submitting the next prompt operation. Read nested prompt status and refresh state versions. Do not wait indefinitely for the pause receipt before resolving the blocking prompt. Prompt responses may overlap a pending job pause, but two prompt mutations cannot overlap. Never invent a token or submit a prompt probe before the fixture reports its wait. After response completion, poll the original pause operation until it completes. THEN call job.inspect twice and require resumable=true, job_state=paused and unchanged completed_moves in BOTH results before resuming. Earlier paused-state observations with resumable=false do not count: the checkpoint is only finalized when the pause receipt is polled. Then resume, and keep observing until all 80 moves finish. All operations are software-only; use the SAME tool server and journal. The fixture_wait flag is simulation evidence, not hardware telemetry.'''


def evaluate_combined(status, events, observations):
    completed = [e for e in events.get('events', []) if e['status'] == 'completed']
    jobs = {'events': [e for e in events.get('events', []) if e['detail'].get('evidence_type')]}
    resolved = [e for e in completed if e['detail'].get('resolution') == 'firmware_user_wait_cleared']
    snapshot = status.get('prompt', {}).get('prompt_snapshot') or {}
    waiting = [o for o in observations if o['status'].get('fixture_prompt_waiting')]
    return (evaluate_job(status, jobs, observations) and len(resolved) == 1 and
            status.get('fixture_prompt_waiting') is False and
            snapshot.get('active') is False and snapshot.get('waiting') is False and
            status.get('prompt', {}).get('channel_state') == 'ready' and
            len(waiting) >= 2 and waiting[-1]['at']-waiting[0]['at'] >= .5 and
            all(o['status']['simulation_evidence']['completed_moves'] == 10 for o in waiting))
