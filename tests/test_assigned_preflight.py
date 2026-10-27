"""Synthetic cold-ready packets through actual capture, channel, journal and tools."""
from pathlib import Path
from types import SimpleNamespace
import struct
import pytest
from printer_control.assigned_job import AssignedJobBackend
from printer_control.assigned_sender import AssignedSender
from printer_control.assigned_observations import AssignedMachineEventRecorder
from printer_control.prompt_backend import PromptBackend
from printer_control.prompt_channel import PromptChannel
from printer_control.shared_receiver import ObservedBytes
from printer_control.journal import Journal,RequestRejected
from printer_control.ownership import ControllerLease
from printer_control.tools import ToolRouter
from test_assigned_observations import make_endpoint,deliver,wait_for
from test_machine_state import LINE,change
from test_cold_preflight import policy,PREFIX

JOB=PREFIX+b'G1 F600\nG1 X20 Y20 Z10\nM400\n'


def inactive(request,session):
    return (f'//llm:v1 Q{request:08x} C00 D0 T{session:032x} P00000000 A0 H0 B00 R00 W0\n').encode()


def cold_line():
    line=LINE
    for key,value in {'F':'01','H':'07','K':'07','E':'07','B':'00','R':'00',
        'P':struct.pack('>ffff',10,10,10,0).hex(),'O':'0'*24,'D':'0'*24,
        'V':struct.pack('>fffffffff',1,10,100,100,0,25,0,25,0).hex()}.items():line=change(key,value,line)
    return line


@pytest.fixture
def cold_context(tmp_path,request):
    now=[10.];chunks=[];endpoint=make_endpoint(now,chunks);wire=[]
    endpoint.backend.frame_writer._writer=lambda data:wire.append(data) or len(data)
    journal=Journal(tmp_path/'source.sqlite');job=tmp_path/'cold.gcode';job.write_bytes(JOB);selected=[];cursor=[None]
    with ControllerLease(str(journal.path)+'.owner') as lease:
        prompt=endpoint.backend;prompt.controller_lease=lease;prompt.transport=SimpleNamespace(fault=None)
        channel=PromptChannel(prompt.frame_writer.write_prompt,'g1',session=1)
        PromptBackend.__init__(prompt,channel,printer_id='fixture',clock=lambda:now[0],
            pump=lambda:ObservedBytes(b'',None),close_transport=lambda:None)
        sender=AssignedSender.__new__(AssignedSender);sender._assigned_endpoint=endpoint;sender._current_line=0
        sender._callback=SimpleNamespace(**{name:(lambda *a,**k:None) for name in (
            'on_comm_state_change','on_comm_print_job_done','on_comm_print_job_cancelled','on_comm_print_job_failed','on_comm_error')})
        sender.getState=lambda:sender.STATE_OPERATIONAL;sender.getStateString=lambda:'Operational'
        sender.getFilePosition=lambda:cursor[0]
        def select(path,sd):selected.append(path);cursor[0]={'origin':'local','filename':path,'pos':0}
        sender.selectFile=select;sender.close=lambda:None
        provider=SimpleNamespace(kind='software_fixture_only',preflight=lambda *a:True,start_blockers=lambda *a:[],
            **{name:(lambda *a:None) for name in ('checkpoint','started','resumed','completed','cancelled')})
        backend=AssignedJobBackend(sender,job_path=job,job_id='job',witness=SimpleNamespace(invalidate_resume=lambda:None),
            evidence=provider,clock=lambda:now[0])
        recorder=AssignedMachineEventRecorder(journal,endpoint=endpoint,lease=lease,clock=lambda:now[0])
        backend.attach_machine_events(recorder)
        mode=getattr(request,"param",False)
        backend.attach_cold_preflight(policy(),automatic_inspection=mode is True)
        if mode in ('checkpoint','configuration','checkpoint-configuration','physical-cold'):
            if mode=='checkpoint':provider.checkpoint_from_controller=lambda *a:None
            if mode in ('checkpoint-configuration','physical-cold'):provider.checkpoint_from_sources=lambda *a:None
            backend.attach_completion_barrier(checkpoint_inspection=mode in ('checkpoint','checkpoint-configuration','physical-cold'),
                checkpoint_configuration=mode in ('checkpoint-configuration','physical-cold'))
            if mode in ('configuration','physical-cold'):backend.attach_configuration_report()
        endpoint.claim();capture=(recorder,endpoint,SimpleNamespace(_serial=endpoint),now,chunks)
        channel.dispatch('inspect',now=8,deadline=20);channel.feed(inactive(1,0),now=8.1)
        channel.dispatch('bind',now=9,deadline=20);channel.feed(inactive(2,1),now=9.1)
        request=dict(request_id='inspect',printer_id='fixture',connection_generation='g1',expected_state_version=0,
            deadline=20.,operation='prompt.inspect',arguments={})
        operation=journal.accept(request,now=10,printer_id='fixture',generation='g1',state_version=0)['operation_id']
        assert journal.claim(operation,now=10)
        prompt.dispatch(operation,'prompt.inspect',{},deadline=20);channel.feed(inactive(3,1),now=10.)
        result=prompt.poll(operation);assert result.status=='completed';journal.finish(operation,result.status,result.detail,now=10.)
        now[0]=10.1;line=cold_line()
        if mode=='physical-cold':line=line.replace('llmstate:v1','llmstate:v2')+' I000004b00000000000000000 C07 J01'
        deliver(capture,(line+'\n').encode());wait_for(lambda:'machine_state' in recorder.snapshot()['reports'])
        router=ToolRouter(SimpleNamespace(backend=backend,lease=lease,journal=journal))
        yield backend,router,operation,capture,wire,selected,provider
        backend.close()


def preview(context):return context[1].call('job.cold_preflight',{'job_id':'job','operation_id':context[2]})


def test_read_only_tool_has_no_policy_or_raw_command_input_and_no_physical_authority(cold_context):
    backend,router,operation,capture,wire,selected,_=cold_context;before=list(wire)
    result=preview(cold_context)
    assert result['software_checks_passed'] and result['plan']['moves']==1
    assert result['source_observed_at']==10.1 and result['source_capture_id']==capture[0].capture_id
    assert not result['physical_readiness_qualified'] and not result['dispatch_authorized']
    assert result['remaining_qualification'] and wire==before and not selected
    schema=next(r for r in router.definitions() if r['name']=='job.cold_preflight')['inputSchema']
    assert set(schema['properties'])=={'job_id','operation_id'} and not schema['additionalProperties']
    with pytest.raises(RequestRejected):router.call('job.cold_preflight',{'job_id':'job','operation_id':operation,'bounds':{}})


def test_cold_screening_is_required_and_resampled_before_stage_dispatch(cold_context):
    backend,_,_,capture,_,selected,_=cold_context
    arguments={'job_id':'job','file_sha256':backend.sha256}
    with pytest.raises(RequestRejected,match='current_cold_preflight_required'):backend.validate('job.stage',arguments)
    preview(cold_context);backend.validate('job.stage',arguments)
    capture[3][0]=11.2
    with pytest.raises(RequestRejected):backend.dispatch('stage','job.stage',arguments,deadline=20.)
    assert not selected


def test_source_gate_does_not_replace_external_preflight(cold_context):
    backend,_,_,_,_,selected,provider=cold_context;preview(cold_context);provider.preflight=lambda *a:False
    with pytest.raises(RequestRejected,match='job_preflight_unqualified'):
        backend.dispatch('stage','job.stage',{'job_id':'job','file_sha256':backend.sha256},deadline=20.)
    assert not selected


def test_staging_uses_same_hash_after_software_and_external_checks(cold_context):
    backend,_,_,_,wire,selected,_=cold_context;preview(cold_context);before=list(wire)
    backend.dispatch('stage','job.stage',{'job_id':'job','file_sha256':backend.sha256},deadline=20.)
    assert selected==[str(backend.path)] and backend.path.read_bytes()==JOB and wire==before
    receipt=backend.poll('stage')
    assert receipt.status=='completed' and receipt.detail['cold_preflight']['plan']['file_sha256']==backend.sha256
    assert not backend.capabilities()['physical_control_qualified']


def test_slow_external_provider_cannot_refresh_inspect_time(cold_context):
    backend,_,_,capture,_,selected,provider=cold_context;preview(cold_context)
    def delayed(*args):capture[3][0]=11.2;return True
    provider.preflight=delayed
    with pytest.raises(RequestRejected):backend.dispatch('stage','job.stage',{'job_id':'job','file_sha256':backend.sha256},deadline=20.)
    assert not selected


@pytest.mark.parametrize('field,value',[('F','03'),('H','00'),('R','01'),('O',struct.pack('>fff',1,0,0).hex()),
    ('B','01'),('V',struct.pack('>fffffffff',1,10,150,100,0,25,0,25,0).hex())])
def test_inconsistent_logical_state_refuses_preflight(cold_context,field,value):
    backend,_,_,capture,_,selected,_=cold_context
    line=change(field,value,change('N','00000002',cold_line()))
    deliver(capture,(line+'\n').encode());wait_for(lambda:capture[0].snapshot()['reports']['machine_state']['data']['sequence']==2)
    with pytest.raises(RequestRejected):preview(cold_context)
    assert not selected and backend.cold_preflight._cached is None


def test_file_and_job_identity_cannot_reuse_a_cached_plan(cold_context):
    backend,router,operation,_,_,selected,_=cold_context;preview(cold_context)
    with pytest.raises(RequestRejected):router.call('job.cold_preflight',{'job_id':'other','operation_id':operation})
    backend.source_path.write_bytes(PREFIX+b'G1 X20 E1 F600\n')
    with pytest.raises(RequestRejected):preview(cold_context)
    assert backend.cold_preflight._cached is None and not selected


def test_reading_with_a_moving_clock_preserves_the_original_source_timestamp(cold_context,monkeypatch):
    backend,_,_,capture,_,_,_=cold_context;counter=[0]
    def clock():counter[0]+=1;return 10.1+counter[0]*.001
    monkeypatch.setattr(capture[0],'clock',clock)
    result=preview(cold_context)
    assert result['source_observed_at']==10.1 and result['software_checks_passed']



def test_official_model_context_client_can_only_read_the_configured_preflight(cold_context):
    import asyncio,anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from printer_control.mcp_adapter import make_server
    backend,router,operation,_,wire,selected,_=cold_context
    server=make_server(router);before=list(wire)
    async def scenario():
        async with create_client_server_memory_streams() as (client_streams,server_streams):
            async with anyio.create_task_group() as group:
                async def serve():await server.run(*server_streams,server.create_initialization_options())
                group.start_soon(serve)
                async with ClientSession(*client_streams) as client:
                    await client.initialize();tools=await client.list_tools()
                    assert 'job.cold_preflight' in {tool.name for tool in tools.tools}
                    result=await client.call_tool('job.cold_preflight',{'job_id':'job','operation_id':operation})
                    assert not result.is_error and result.structured_content['software_checks_passed']
                    assert not result.structured_content['physical_readiness_qualified']
                    invalid=await client.call_tool('job.cold_preflight',{'job_id':'job','operation_id':operation,'max_feedrate':99999})
                    assert invalid.is_error
                group.cancel_scope.cancel()
    asyncio.run(scenario())
    assert wire==before and not selected
