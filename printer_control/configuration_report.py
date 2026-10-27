"""Collect reported firmware/settings bytes, never complete physical identity."""
import hashlib
import json
import re
from .journal import RequestRejected


def collect_configuration_report(backend,proof):
    b=backend;r=b.machine_events;operation_id=proof['operation_id']
    if proof['identity']!=b.identity() or not b._file_valid():raise RequestRejected('configuration_scope_or_job_changed')
    start,end=proof['receipt'],proof['end_receipt']
    page=r.read(after=start['event_sequence'],limit=1000)
    if page['history_gap'] or page['cursor_ahead'] or page['next_after']<end['event_sequence']:
        raise RequestRejected('configuration_history_incomplete')
    rows=[row for row in page['events'] if row['sequence']<end['event_sequence'] and row['event']['kind']=='serial_line_received']
    transcript=[];raw_transcript=[];firmware=[];settings=[];commands=[]
    total=0
    for row in rows:
        event=row['event'];data=event['data'];raw=data['text'].encode('ascii');p=data.get('receive_provenance',{})
        if (row['capture_id']!=proof['capture_id'] or event['generation']!=b.generation
                or p.get('capture_id')!=proof['capture_id'] or p.get('printer_id')!=b.printer_id
                or p.get('connection_generation')!=b.generation or p.get('sha256')!=hashlib.sha256(raw).hexdigest()
                or not start['observed_at']<=event['observed_at']<=end['observed_at']):
            raise RequestRejected('configuration_source_identity_or_time_mismatch')
        total+=len(raw)
        if total>16384 or len(transcript)>=256:raise RequestRejected('configuration_report_exceeds_bounds')
        if any(not 32<=byte<=126 for byte in raw.rstrip(b'\r\n')):raise RequestRejected('configuration_line_not_printable')
        text=raw.rstrip(b'\r\n').decode('ascii')
        transcript.append(text);raw_transcript.append(raw)
        if text.startswith(('FIRMWARE_NAME:','Cap:')):firmware.append(text)
        match=re.fullmatch(r'(?:echo:)?\s*((G(?:20|21)|M[0-9]{1,3})(?:\s.*)?)',text)
        if match:settings.append(match[1]);commands.append(match[2])
    if (len([s for s in firmware if s.startswith('FIRMWARE_NAME:')])!=1
            or not {'M92','M201','M203','M204','M205'}.issubset(commands)
            or not ({'G20','G21'} & set(commands))):
        raise RequestRejected('required_configuration_reports_missing')
    encoded=json.dumps(dict(firmware=firmware,settings=settings),sort_keys=True,separators=(',',':')).encode()
    result=dict(operation_id=operation_id,identity=b.identity(),capture_id=proof['capture_id'],
        firmware_session=b.prompt.channel.session,source_observed_at=end['observed_at'],
        first_event_sequence=start['event_sequence'],last_event_sequence=end['event_sequence'],
        barrier_token=proof['token'],begin_receipt=start,end_receipt=end,
        firmware_declaration=firmware,reported_settings=settings,reported_commands=sorted(set(commands)),
        reported_settings_sha256=hashlib.sha256(encoded).hexdigest(),
        transcript=transcript,transcript_sha256=hashlib.sha256(b''.join(raw_transcript)).hexdigest(),
        transcript_hash_scope='exact_interior_source_bytes',
        write_evidence=proof['writes'],scope='owned_M115_M503_report_interval',
        configuration_identity_complete=False,firmware_binary_identity_verified=False,
        physical_readiness_qualified=False,resume_authorized=False,
        uncovered=['installed_binary_and_target_identity','runtime_idle_timeouts','physical_geometry_and_clearance',
            'profile_specific_configuration_coverage','motor_sensor_and_part_continuity'])
    return result


class AssignedConfigurationReport:
    def __init__(self,backend):
        self.backend=backend
        self.pending=set();self.bindings={}

    def dispatch(self,operation_id):
        b=self.backend
        self.pending.add(operation_id)
        self.bindings[operation_id]=(b.identity(),b.machine_events.capture_id,b.prompt.channel.session)
        b._barrier_evidence(operation_id,'configuration')

    def poll(self,operation_id):
        b=self.backend;r=b.machine_events
        proof=b._barrier_evidence(operation_id,'configuration')
        if proof is None:return None
        if self.bindings[operation_id]!=(b.identity(),r.capture_id,b.prompt.channel.session) or proof['identity']!=b.identity() or not b._file_valid():
            raise RequestRejected('configuration_scope_or_job_changed')
        result=collect_configuration_report(b,proof)
        r.journal.record_observation(dict(kind='configuration_report_observed',**result),source='job.inspect',now=b.clock())
        if self.bindings[operation_id]!=(b.identity(),r.capture_id,b.prompt.channel.session) or not b._file_valid():
            raise RequestRejected('configuration_scope_changed_during_persistence')
        # Persistence must not extend either original marker's age or authority.
        b.completion_barrier.retire_configuration(operation_id)
        self.pending.remove(operation_id)
        return result
