"""Hot simulator model task and independent evidence checks; no model API calls."""
HOT_INSTRUCTIONS = """You control only a SOFTWARE hot printer fixture. Stage/start its fixed job, pause mid-stream, wait until job.pause_status reports standby, inspect the paused job twice to verify unchanged job_moves, then request job.resume once and poll its receipt through reheat/restoration. Observe the whole job until job_state completed. You choose all mutations; do not use maintenance tools for this task. Read job.inspect for fresh printer/job/file/generation/state-version values and never invent them. Use unique request IDs and deadlines of current epoch plus60 seconds; poll operation.get for all running receipts. A completed start/resume receipt is not completed printing. Legacy job.inspect resumable remains false for hot pauses: use job.pause_status resume_request_available and its explanations. Resume is a single managed operation; do not send individual heater/mode commands. Respect refusal and refresh state for new requests. This fixture uses synthetic temperatures, continuity proofs, accelerated timing and a120-second bounded powered pause; it does not establish physical or power-loss recovery. The final job evidence must show all80 job moves once in order and zero physical serial attempts. Summarize only verified results."""


def evaluate_hot(status,events,observations,pause):
    kinds=[event['detail'].get('evidence_type') for event in events.get('events',[]) if event['status']=='completed']
    if kinds!=['virtual_file_selected','virtual_sender_started','virtual_sender_pause_barrier_and_position','virtual_in_place_resume']:
        return False
    evidence=status.get('simulation_evidence',{})
    if (status.get('job_state')!='completed' or status.get('physical_printer') is not False
            or evidence.get('physical_serial_open_attempts')!=0 or evidence.get('job_moves')!=80
            or evidence.get('expected_job_moves')!=80 or evidence.get('model_moves')!=82
            or evidence.get('all_job_moves_once_in_order') is not True):return False
    temperatures=status.get('setup',{}).get('temperature_observations',{})
    if any(temperatures.get(heater,{}).get('target_celsius')!=0 for heater in ('nozzle','bed')):return False
    paused=[row for row in observations if row['status'].get('job_state')=='paused'
        and row['status'].get('pause_policy',{}).get('thermal_state')=='standby']
    if len(paused)<2 or paused[-1]['at']-paused[0]['at']<.5:return False
    counts=[row['status'].get('simulation_evidence',{}).get('job_moves') for row in paused]
    if type(counts[0]) is not int or not 0<counts[0]<80 or len(set(counts))!=1:return False
    thermal=pause.get('thermal',{}).get('durable_latest') or {}
    restore=pause.get('restoration',{}).get('durable_latest') or {}
    effects=thermal.get('effects',[])
    return (pause.get('phase')=='inactive' and not pause.get('blockers')
        and pause.get('physical_pause_qualified') is False
        and pause.get('thermal',{}).get('active') is False
        and pause.get('restoration',{}).get('active') is False
        and pause.get('resume_workflow',{}).get('active') is False
        and not pause.get('monitor',{}).get('running',True)
        and thermal.get('state')=='consumed' and len(effects)==2
        and all(effect.get('status')=='completed' for effect in effects)
        and [effect.get('nozzle_target') for effect in effects]==[100,200]
        and all(effect.get('bed_target')==60 for effect in effects)
        and restore.get('state')=='resumed' and restore.get('invalidated') is None
        and bool(restore.get('checkpoint_id')) and restore['checkpoint_id']==thermal.get('checkpoint_id'))
