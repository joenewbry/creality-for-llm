"""Fixed configuration query interval on the actual owned source primitives."""
import threading
import pytest
from test_assigned_preflight import cold_context
from test_assigned_inspection import service_for
from test_assigned_observations import deliver,wait_for

pytestmark=pytest.mark.parametrize('cold_context',['configuration'],indirect=True)
REPORT=('FIRMWARE_NAME:SyntheticMarlin SOURCE_CODE_URL:example.invalid MACHINE_TYPE:Fixture\n'
        'Cap:EEPROM:1\necho:  G21 ; millimeters\necho:  M92 X80 Y80 Z400 E100\n'
        'echo:  M203 X100 Y100 Z10 E10\necho:  M201 X1000 Y1000 Z100 E100\n'
        'echo:  M204 P500 R500 T500\necho:  M205 X8 Y8 Z0.4 E5\n')


def setup(context):
    b=context[0];writes=[]
    b.sender.sendCommand=lambda command,**kw:writes.append(command) or bool(context[3][1].write(command.encode()+b'\n'))
    return b,service_for(context),writes


def request(service):
    c=service.backend.context()
    return dict(request_id='configuration',printer_id=c.printer_id,connection_generation=c.generation,
        expected_state_version=c.state_version,deadline=20.,operation='printer.configuration.inspect',arguments={'job_id':'job'})


def emit(context,lines):
    for line in lines.splitlines(True):deliver(context[3],line.encode())
    expected=context[3][1].read_sequence
    wait_for(lambda:any(row['event']['data'].get('receive_provenance',{}).get('source_sequence')==expected
        and row['event']['kind']=='serial_line_received' for row in context[0].machine_events.read(limit=1000)['events']))


def response(context,body=REPORT,*,end=True):
    token=context[0].completion_barrier._active['token']
    emit(context,'echo:LLM_BARRIER_'+token+'\n'+body+('echo:LLM_CONFIG_END_'+token+'\n' if end else ''))


def test_owned_queries_complete_only_with_bracketed_actual_reports(cold_context):
    b,s,writes=setup(cold_context);req=request(s);r=s.submit(req);op=r['operation_id']
    assert r['status']=='running' and [writes[i] for i in (0,2,3)]==['M400','M115','M503'] and len(writes)==5
    assert s.submit(req)==r and len(writes)==5
    assert s.poll(op)['status']=='running'
    response(cold_context);done=s.poll(op)
    assert done['status']=='completed',done
    data=done['result']
    assert data['identity']==b.identity() and data['reported_settings'] and len(data['reported_settings_sha256'])==64
    assert len(data['write_evidence'])==5 and all(w['confirmed'] for w in data['write_evidence'])
    assert not data['configuration_identity_complete'] and not data['firmware_binary_identity_verified']
    assert not data['physical_readiness_qualified'] and not data['resume_authorized']
    assert b.completion_barrier._active is None
    assert s.poll(op)==done and len(writes)==5
    # Retiring the finite read interval allows later pre-job prompt traffic.
    b.prompt.frame_writer.write_prompt(b'M876 V1 I Q00000004\n')
    assert not b.prompt.frame_writer.fault


@pytest.mark.parametrize('case',['missing_end','missing_settings','stale','extra_write','duplicate_marker','wrong_order','partial','changed_job'])
def test_invalid_intervals_cannot_become_configuration_identity(cold_context,case):
    b,s,writes=setup(cold_context)
    if case=='partial':b.prompt.frame_writer._writer=lambda data:2
    r=s.submit(request(s));op=r['operation_id']
    if case=='partial':
        assert r['status']=='outcome_unknown';return
    if case=='wrong_order':
        token=b.completion_barrier._active['token'];emit(cold_context,'echo:LLM_CONFIG_END_'+token+'\n')
    else:
        response(cold_context,body=REPORT.replace('echo:  M92 X80 Y80 Z400 E100\n','') if case=='missing_settings' else REPORT,end=case!='missing_end')
    if case=='missing_end':
        assert s.poll(op)['status']=='running';cold_context[3][3][0]=20.
    elif case=='stale':cold_context[3][3][0]+=1.1
    elif case=='extra_write':cold_context[3][1].write(b'M105\n')
    elif case=='duplicate_marker':
        token=b.completion_barrier._active['token'];emit(cold_context,'echo:LLM_CONFIG_END_'+token+'\n')
    elif case=='changed_job':b.source_path.write_bytes(b'changed')
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and b._fault and not result['result']['physical_readiness_qualified'],result


def test_no_active_or_paused_job_can_dispatch_settings_query(cold_context):
    b,s,writes=setup(cold_context);b._started=True
    result=s.submit(request(s))
    assert result['status']=='rejected' and not writes


def test_unsolicited_reports_before_begin_marker_do_not_supply_missing_settings(cold_context):
    b,s,writes=setup(cold_context);emit(cold_context,REPORT)
    r=s.submit(request(s));response(cold_context,body='FIRMWARE_NAME:SyntheticMarlin\n')
    assert s.poll(r['operation_id'])['status']=='outcome_unknown'


def test_provider_loop_finishes_report_without_model_poll(cold_context):
    b,s,writes=setup(cold_context);r=s.submit(request(s));op=r['operation_id']
    b._pump_thread=threading.Thread(target=b._pump_prompts);b._pump_thread.start()
    try:
        response(cold_context);wait_for(lambda:op in b._configuration_results)
        cold_context[3][3][0]=15.
        assert s.poll(op)['status']=='completed' and len(writes)==5
    finally:
        b._pump_stop.set();b._pump_thread.join(2)
        assert not b._pump_thread.is_alive()


def test_schema_exposes_only_job_identity_and_durable_context(cold_context):
    from printer_control.tools import ToolRouter
    b,s,writes=setup(cold_context);router=ToolRouter(s)
    schema=next(t['inputSchema'] for t in router.definitions() if t['name']=='printer.configuration.inspect')
    assert set(schema['properties']['arguments']['properties'])=={'job_id'}
    req=request(s);args={k:v for k,v in req.items() if k!='operation'};args['arguments']['command']='M500'
    result=router.call('printer.configuration.inspect',args)
    assert result['status']=='rejected' and not writes


def test_numbered_five_frame_group_preserves_exact_write_evidence(cold_context):
    b,s,writes=setup(cold_context);number=[7]
    def send(command,**kw):
        writes.append(command);raw=f'N{number[0]} {command}'.encode();number[0]+=1
        checksum=0
        for byte in raw:checksum^=byte
        cold_context[3][1].write(raw+f'*{checksum}\n'.encode());return True
    b.sender.sendCommand=send
    op=s.submit(request(s))['operation_id'];response(cold_context);result=s.poll(op)
    assert result['status']=='completed'
    assert [w['line_number'] for w in result['result']['write_evidence']]==list(range(7,12))


def test_volatile_temperature_line_changes_transcript_not_reported_settings(cold_context):
    b,s,writes=setup(cold_context);req=request(s);op=s.submit(req)['operation_id']
    response(cold_context,REPORT+'ok T:25 /0 B:24 /0\n');first=s.poll(op)
    req=request(s);req['request_id']='next';op=s.submit(req)['operation_id']
    response(cold_context,REPORT+'ok T:26 /0 B:24 /0\n');second=s.poll(op)
    assert first['status']==second['status']=='completed'
    assert first['result']['reported_settings_sha256']==second['result']['reported_settings_sha256']
    assert first['result']['transcript_sha256']!=second['result']['transcript_sha256']


def test_slow_persistence_cannot_refresh_marker_arrival(cold_context,monkeypatch):
    b,s,writes=setup(cold_context);op=s.submit(request(s))['operation_id'];response(cold_context)
    original=b.machine_events.journal.record_observation
    def slow(*a,**k):
        result=original(*a,**k);cold_context[3][3][0]+=1.1;return result
    monkeypatch.setattr(b.machine_events.journal,'record_observation',slow)
    assert s.poll(op)['status']=='outcome_unknown'


def test_query_intent_is_durable_before_any_of_the_five_frames(cold_context):
    b,s,writes=setup(cold_context);original=b.prompt.frame_writer._writer;observed=[]
    def check(data):
        with b.machine_events.journal._db() as db:
            row=db.execute('SELECT operation_id,status,intent FROM assigned_barriers').fetchone()
            import json
            assert json.loads(row['intent'])['commands'][2:4]==['M115','M503']
            assert row['status']=='dispatching' and b.machine_events.journal.get(row['operation_id'])['status']=='running'
        observed.append(data);return original(data)
    b.prompt.frame_writer._writer=check
    result=s.submit(request(s));assert result['status']=='running' and len(observed)==5


def test_official_protocol_client_can_collect_only_the_fixed_report(cold_context):
    import asyncio,anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from printer_control.mcp_adapter import make_server
    from printer_control.tools import ToolRouter
    b,s,writes=setup(cold_context);server=make_server(ToolRouter(s))
    async def scenario():
        async with create_client_server_memory_streams() as (client_streams,server_streams):
            async with anyio.create_task_group() as group:
                async def serve():await server.run(*server_streams,server.create_initialization_options())
                group.start_soon(serve)
                async with ClientSession(*client_streams) as client:
                    await client.initialize();tools=await client.list_tools()
                    assert 'printer.configuration.inspect' in {t.name for t in tools.tools}
                    req=request(s);args={k:v for k,v in req.items() if k!='operation'}
                    response1=await client.call_tool('printer.configuration.inspect',args)
                    assert not response1.is_error and response1.structured_content['status']=='running'
                    op=response1.structured_content['operation_id'];response(cold_context)
                    done=await client.call_tool('operation.get',{'operation_id':op})
                    assert not done.is_error and done.structured_content['status']=='completed'
                    assert not done.structured_content['result']['configuration_identity_complete']
                    args['arguments']['raw']='M500'
                    denied=await client.call_tool('printer.configuration.inspect',args)
                    assert denied.is_error
                group.cancel_scope.cancel()
    asyncio.run(scenario());assert len(writes)==5


def test_lost_workflow_tokens_cannot_replay_claimed_query(cold_context):
    b,s,writes=setup(cold_context);op=s.submit(request(s))['operation_id'];before=list(writes)
    b._barrier_tokens.clear()
    result=s.poll(op)
    assert result['status']=='outcome_unknown' and writes==before


def test_control_bytes_cannot_qualify_a_report(cold_context):
    b,s,writes=setup(cold_context);op=s.submit(request(s))['operation_id']
    response(cold_context,REPORT.replace('X80','X80\x00',1))
    assert s.poll(op)['status']=='outcome_unknown'
