import base64
from copy import deepcopy
import pytest
from printer_control.camera import CameraBroker, CameraTools
from printer_control.journal import RequestRejected
from printer_control.ownership import OwnershipError


# Actual 1x1 PNG byte envelope. Optical checks are intentionally separate.
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class CaptureOwner:
    def __init__(self):
        self.state = dict(camera_id='cam', assigned_printer_id='dev', capture_owner='existing-owner',
            connection_generation='g1', state_version=0,
            controls={'gain': {'minimum': 0, 'maximum': 100}}, values={'gain': 10})
        self.frame = dict(camera_id='cam', assigned_printer_id='dev', connection_generation='g1',
            frame_id='f1', captured_at=99, received_at=100, width=1, height=1,
            crop='full_encoded_frame', view_calibration_id=None, mount_revision='prototype',
            job_id='job', media_type='image/png', data=PNG)
        self.writes = []
        self.hook = lambda: None
        self.readback = True
        self.fail = False

    def inspect(self): return deepcopy(self.state)
    def snapshot(self, *, deadline):
        self.hook()
        return deepcopy(self.frame)
    def set_controls(self, values, *, deadline):
        self.writes.append(deepcopy(values))
        self.hook()
        if self.fail: raise OSError('lost response')
        if self.readback:
            self.state['values'].update(values)
            self.state['state_version'] += 1


@pytest.fixture
def setup(tmp_path):
    owner = CaptureOwner()
    broker = CameraBroker(tmp_path/'camera.sqlite', owner, camera_id='cam', printer_id='dev',
        capture_owner='existing-owner', clock=lambda:100)
    yield broker, owner, CameraTools(broker)
    broker.close()


def context():
    return dict(camera_id='cam', connection_generation='g1', expected_state_version=0, deadline=110)


def snap(): return {**context(), 'max_age_seconds': 3, 'job_id':'job'}
def controls(): return {**context(), 'request_id':'r1', 'controls':{'gain':20}}


def test_frame_provenance_and_monitor_never_claim_print_health(setup):
    broker, owner, tools = setup
    assert tools.call('monitor.inspect', {})['available'] is False
    frame = tools.call('camera.snapshot', snap())
    assert base64.b64decode(frame['data_base64']) == PNG
    import hashlib
    assert frame['sha256'] == hashlib.sha256(PNG).hexdigest()
    assert not frame['optical_quality_verified']
    monitor = tools.call('monitor.inspect', {})
    assert monitor['available'] and monitor['observations'] == []
    assert not monitor['automatic_printer_actions']
    assert owner.writes == []


@pytest.mark.parametrize('field,value', [
    ('camera_id','other'), ('connection_generation','g2'), ('assigned_printer_id','protected'),
    ('job_id','other'), ('captured_at',90), ('received_at',101), ('captured_at',float('nan')),
    ('width',True), ('height',0), ('crop','unknown'), ('frame_id',''),
    ('data',b'bad'), ('data',PNG+b'x'*65536), ('media_type','text/html'),
])
def test_reject_mismatched_stale_invalid_frame_and_clear_previous(setup,field,value):
    broker, owner, tools = setup
    tools.call('camera.snapshot', snap())
    owner.frame[field] = value
    with pytest.raises(RequestRejected): tools.call('camera.snapshot', snap())
    assert tools.call('monitor.inspect', {})['available'] is False


def test_stream_change_during_acquisition_invalidates_frame(setup):
    broker, owner, tools = setup
    owner.hook = lambda: owner.state.update(connection_generation='g2')
    with pytest.raises(RequestRejected): tools.call('camera.snapshot',snap())
    assert broker.latest_frame is None


def test_control_completion_requires_readback_and_duplicate_never_rewrites(setup):
    broker, owner, tools = setup
    receipt = tools.call('camera.set_controls', controls())
    assert receipt['status'] == 'completed'
    assert tools.call('camera.set_controls', controls()) == receipt
    assert owner.writes == [{'gain':20}]
    assert tools.call('camera.operation_get',{'operation_id':receipt['operation_id']}) == receipt


@pytest.mark.parametrize('change', [dict(camera_id='protected'), dict(connection_generation='old'),
    dict(expected_state_version=8), dict(deadline=100), dict(controls={'focus':1}),
    dict(controls={'gain':101}), dict(controls={}), dict(controls={'gain':True})])
def test_invalid_mutation_does_not_reach_capture_owner(setup,change):
    broker, owner, tools = setup
    receipt=tools.call('camera.set_controls',{**controls(),**change})
    assert receipt['status']=='rejected'
    assert owner.writes == []


@pytest.mark.parametrize('failure', ['no_readback','exception','generation','deadline'])
def test_uncertain_controls_latch_and_remain_durable_after_restart(setup,tmp_path,failure):
    broker, owner, tools = setup
    if failure=='no_readback': owner.readback=False
    if failure=='exception': owner.fail=True
    if failure=='generation': owner.hook=lambda:owner.state.update(connection_generation='g2')
    if failure=='deadline': owner.hook=lambda:setattr(broker,'clock',lambda:111)
    receipt=tools.call('camera.set_controls', controls())
    assert receipt['status']=='outcome_unknown'
    broker.clock=lambda:100
    broker.close()
    replacement=CameraBroker(tmp_path/'camera.sqlite', owner, camera_id='cam',printer_id='dev',
        capture_owner='existing-owner',clock=lambda:100)
    try:
        newtools=CameraTools(replacement)
        assert newtools.call('camera.set_controls',controls())==receipt
        owner.hook=lambda:None
        current=owner.state
        next_request={**controls(),'request_id':'r2','connection_generation':current['connection_generation'],
            'expected_state_version':current['state_version']}
        blocked=newtools.call('camera.set_controls',next_request)
        assert blocked['status']=='rejected'
        assert blocked['result']['reason']=='camera_reconciliation_required'
        assert len(owner.writes)==1
    finally: replacement.close()


def test_second_journal_owner_fails_without_capture_access(setup,tmp_path):
    broker, owner, tools=setup
    with pytest.raises(OwnershipError):
        CameraBroker(tmp_path/'camera.sqlite', owner,camera_id='cam',printer_id='dev',capture_owner='existing-owner')


def test_owner_reassignment_is_rejected_and_no_device_paths_are_tools(setup):
    broker, owner, tools=setup
    owner.state['capture_owner']='competing-process'
    with pytest.raises(RequestRejected):tools.call('camera.inspect',{})
    with pytest.raises(RequestRejected):tools.call('camera.open',{'path':'arbitrary'})


def test_freshness_expiry_and_generation_clear_monitor(setup):
    broker,owner,tools=setup
    tools.call('camera.snapshot',snap())
    broker.clock=lambda:106
    assert not tools.call('monitor.inspect',{})['available']
    owner.state['connection_generation']='g2'
    assert tools.call('camera.inspect',{})['latest_frame'] is None


def test_strict_tool_schemas_and_no_unknown_arguments(setup):
    from jsonschema import Draft202012Validator
    broker,owner,tools=setup
    for definition in tools.definitions():
        Draft202012Validator.check_schema(definition['inputSchema'])
        assert definition['inputSchema']['additionalProperties'] is False
    with pytest.raises(RequestRejected):tools.call('camera.snapshot',{**snap(),'url':'arbitrary'})
    with pytest.raises(RequestRejected):tools.call('camera.set_controls',{**controls(),'controls':{'gain':float('nan')}})
    with pytest.raises(RequestRejected):tools.call('camera.inspect',{'extra':1})


def test_canonical_journal_alias_does_not_allow_second_owner(setup,tmp_path):
    broker,owner,tools=setup
    alias=tmp_path/'alias.sqlite'
    alias.symlink_to(tmp_path/'camera.sqlite')
    with pytest.raises(OwnershipError):
        CameraBroker(alias,owner,camera_id='cam',printer_id='dev',capture_owner='existing-owner')


def test_cannot_recover_or_change_an_existing_printer_journal(tmp_path):
    from printer_control.journal import Journal
    path=tmp_path/'printer.sqlite'
    journal=Journal(path)
    request=dict(request_id='print',printer_id='dev',connection_generation='g1',
        expected_state_version=0,deadline=200,operation='job.pause',arguments={})
    before=journal.accept(request,now=100,printer_id='dev',generation='g1',state_version=0)
    with pytest.raises(RequestRejected,match='not_empty_or_bound'):
        CameraBroker(path,CaptureOwner(),camera_id='cam',printer_id='dev',capture_owner='existing-owner')
    assert journal.get(before['operation_id'])==before


def test_binding_cannot_be_reassigned_after_close(setup,tmp_path):
    broker,owner,tools=setup
    broker.close()
    with pytest.raises(RequestRejected,match='assignment_mismatch'):
        CameraBroker(tmp_path/'camera.sqlite',owner,camera_id='other',printer_id='dev',capture_owner='existing-owner')


def test_real_mcp_client_lists_camera_tools_and_validates_envelopes(setup):
    import asyncio,anyio
    from mcp import ClientSession
    from mcp.shared.memory import create_client_server_memory_streams
    from printer_control.mcp_adapter import make_server
    broker,owner,tools=setup
    server=make_server(tools)
    async def run():
        async with create_client_server_memory_streams() as (client_streams,server_streams):
            async with anyio.create_task_group() as group:
                async def serve():
                    await server.run(*server_streams,server.create_initialization_options())
                group.start_soon(serve)
                async with ClientSession(*client_streams) as client:
                    await client.initialize()
                    listed=await client.list_tools()
                    assert 'camera.snapshot' in [t.name for t in listed.tools]
                    called=await client.call_tool('camera.snapshot',snap())
                    assert not called.is_error
                    assert called.structured_content['sha256']
                    invalid=await client.call_tool('camera.snapshot',{**snap(),'job_id':False})
                    assert invalid.is_error
                    changed=await client.call_tool('camera.set_controls',controls())
                    assert changed.structured_content['status']=='completed'
                    duplicate=await client.call_tool('camera.set_controls',controls())
                    assert duplicate.structured_content==changed.structured_content
                    assert len(owner.writes)==1
                group.cancel_scope.cancel()
    asyncio.run(run())


def test_concurrent_duplicate_mutations_dispatch_once(setup):
    from concurrent.futures import ThreadPoolExecutor
    broker,owner,tools=setup
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts=list(pool.map(lambda _:tools.call('camera.set_controls',controls()),range(12)))
    assert all(r==receipts[0] for r in receipts)
    assert len(owner.writes)==1


def test_process_restart_recovers_unfinished_camera_receipt_without_dispatch(setup,tmp_path):
    broker,owner,tools=setup
    pending=broker.journal.accept(dict(request_id='pending',printer_id='cam',connection_generation='g1',
        expected_state_version=0,deadline=110,operation='camera.set_controls',arguments={'controls':{'gain':20}}),
        now=100,printer_id='cam',generation='g1',state_version=0)
    broker.journal.claim(pending['operation_id'],now=100)
    broker.close()
    restarted=CameraBroker(tmp_path/'camera.sqlite',owner,camera_id='cam',printer_id='dev',capture_owner='existing-owner',clock=lambda:100)
    try:
        assert restarted.journal.get(pending['operation_id'])['status']=='outcome_unknown'
        assert restarted.set_controls(controls())['status']=='rejected'
        assert owner.writes==[]
    finally:restarted.close()
