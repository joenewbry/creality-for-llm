"""Owner-side code, ASGI API and actual HTTP broker/client integration."""
import asyncio
import base64
from copy import deepcopy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from printer_control.camera import CameraBroker
from printer_control.camera_http import CaptureOwnerHTTP
from printer_control.camera_owner import CaptureOwner
from printer_control.camera_owner_api import CaptureOwnerAPI
from printer_control.journal import RequestRejected

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
PROFILE=dict(width=1,height=1,media_type='image/png',crop='full_encoded_frame')


@pytest.fixture
def owner():
    values={'gain':10};writes=[]
    def write(change,*,deadline):writes.append(deepcopy(change));values.update(change)
    obj=CaptureOwner(camera_id='cam',printer_id='dev',capture_owner='owner',profile=PROFILE,
        mount_revision='prototype',controls={'gain':{'minimum':0,'maximum':100}},
        read_controls=lambda:dict(values),write_controls=write)
    return obj,values,writes


def publish(owner):
    state=owner.inspect();now=time.time()
    owner.publish(connection_generation=state['connection_generation'],expected_state_version=state['state_version'],
        data=PNG,captured_at=now-.01,received_at=now,width=1,height=1)
    return state


def request(owner):
    state=owner.inspect()
    return dict(camera_id='cam',connection_generation=state['connection_generation'],
        expected_state_version=state['state_version'],deadline=time.time()+3,controls={'gain':20})


async def call(api,path,*,method='GET',data=b'',allowed=True,content_type=b'application/json'):
    messages=[];sent=False
    async def receive():
        nonlocal sent
        assert not sent;sent=True
        return {'type':'http.request','body':data,'more_body':False}
    async def send(message):messages.append(message)
    await api({'type':'http','path':'/llm-camera/v1/'+path,'method':method,
               'headers':[(b'content-type',content_type)],'state':{'authorized':allowed}},receive,send)
    assert [m['type'] for m in messages]==['http.response.start','http.response.body']
    return messages[0]['status'],json.loads(messages[1]['body'])


def api_for(obj):
    return CaptureOwnerAPI(obj,authorize=lambda scope:scope.get('state',{}).get('authorized') is True)


def test_owner_generation_profile_job_and_control_changes_invalidate_frames(owner):
    obj,values,writes=owner
    first=publish(obj)
    assert obj.snapshot(deadline=time.time()+1)['data']==PNG
    obj.configure_view(profile=PROFILE,job_id='job',mount_revision='prototype',view_calibration_id='cal')
    with pytest.raises(RequestRejected):obj.snapshot(deadline=time.time()+1)
    with pytest.raises(RequestRejected):
        obj.publish(connection_generation=first['connection_generation'],expected_state_version=first['state_version'],
            data=PNG,captured_at=time.time()-.02,received_at=time.time()-.01,width=1,height=1)
    publish(obj)
    assert obj.snapshot(deadline=time.time()+1)['job_id']=='job'
    values['gain']=30
    with pytest.raises(RequestRejected):obj.snapshot(deadline=time.time()+1)
    previous=obj.inspect()['connection_generation']
    obj.begin_capture()
    assert obj.inspect()['connection_generation']!=previous
    with pytest.raises(RequestRejected):obj.snapshot(deadline=time.time()+1)


@pytest.mark.parametrize('change',[
    {'camera_id':'protected'},{'connection_generation':'old'},{'expected_state_version':-1},
    {'deadline':0},{'controls':{'gain':101}},{'controls':{'focus':2}},{'controls':{'gain':True}},
])
def test_owner_rejects_invalid_control_context_without_writes(owner,change):
    obj,values,writes=owner
    with pytest.raises(RequestRejected):obj.apply_controls({**request(obj),**change})
    assert writes==[]


def test_owner_duplicate_control_fence_and_uncertain_readback(owner):
    obj,values,writes=owner
    req=request(obj)
    assert obj.apply_controls(req)['accepted'] is True
    with pytest.raises(RequestRejected):obj.apply_controls(req)
    assert writes==[{'gain':20}]
    obj.writer=lambda change,**kw:writes.append(change)
    req={**request(obj),'controls':{'gain':25}}
    with pytest.raises(RequestRejected):obj.apply_controls(req)
    with pytest.raises(RequestRejected):obj.inspect()
    with pytest.raises(RequestRejected):obj.apply_controls(req)
    assert len(writes)==2


def test_owner_readonly_profile_has_no_fake_controls():
    obj=CaptureOwner(camera_id='cam',printer_id='dev',capture_owner='owner',profile=PROFILE,mount_revision='m')
    assert obj.inspect()['controls']=={}
    with pytest.raises(RequestRejected):obj.apply_controls(request(obj))


@pytest.mark.parametrize('change',[
    {'connection_generation':'old'},{'expected_state_version':-1},{'data':b'bad'},
    {'data':PNG+b'x'*65536},{'width':2},{'height':True},{'captured_at':float('nan')},
    {'received_at':time.time()+1000},
])
def test_owner_invalid_frame_never_publishes(owner,change):
    obj,_,_=owner
    state=obj.inspect();now=time.time()
    args=dict(connection_generation=state['connection_generation'],expected_state_version=state['state_version'],
        data=PNG,captured_at=now-.02,received_at=now-.01,width=1,height=1)
    with pytest.raises(RequestRejected):obj.publish(**{**args,**change})
    with pytest.raises(RequestRejected):obj.snapshot(deadline=time.time()+1)


def test_authorization_precedes_owner_or_body_access(owner):
    obj,values,writes=owner
    obj.reader=lambda:(_ for _ in ()).throw(AssertionError('must not read'))
    for path in ('state','snapshot','controls'):
        status,body=asyncio.run(call(api_for(obj),path,method='POST' if path=='controls' else 'GET',allowed=False))
        assert status==403 and body=={'error':'forbidden'}
    assert writes==[]


@pytest.mark.parametrize('data',[
    b'{"deadline":1,"deadline":2}',b'{"x":NaN}',b'[]',b'{',b'x'*8193,
])
def test_asgi_invalid_body_never_writes(owner,data):
    obj,values,writes=owner
    status,_=asyncio.run(call(api_for(obj),'controls',method='POST',data=data))
    assert status in (409,413)
    assert writes==[]


def test_asgi_core_roundtrip_and_send_failure_is_not_retried(owner):
    obj,values,writes=owner;publish(obj);api=api_for(obj)
    status,body=asyncio.run(call(api,'snapshot'))
    assert status==200 and base64.b64decode(body['frame']['data_base64'])==PNG
    async def failure():
        calls=[]
        async def send(message):calls.append(message);raise OSError('client left')
        async def receive():raise AssertionError('read body')
        with pytest.raises(OSError):
            await api({'type':'http','path':'/llm-camera/v1/state','method':'GET',
                       'state':{'authorized':True}},receive,send)
        assert len(calls)==1
    asyncio.run(failure())


def test_actual_http_client_broker_to_real_owner_api(owner,tmp_path):
    obj,values,writes=owner;publish(obj);api=api_for(obj)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def handle_request(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            status,body=asyncio.run(call(api,self.path.removeprefix('/llm-camera/v1/'),method=self.command,data=raw,
                allowed=self.headers.get('Authorization')=='Bearer fixture'))
            payload=json.dumps(body).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        do_GET=do_POST=handle_request
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    backend=CaptureOwnerHTTP('http://owner.invalid:'+str(server.server_port),pinned_address='127.0.0.1',
        allow_loopback_http=True,headers={'Authorization':'Bearer fixture'})
    broker=CameraBroker(tmp_path/'cam.sqlite',backend,camera_id='cam',printer_id='dev',capture_owner='owner')
    try:
        state=broker.inspect()
        context=dict(camera_id='cam',connection_generation=state['connection_generation'],
            expected_state_version=state['state_version'],deadline=time.time()+3)
        assert broker.snapshot({**context,'job_id':None,'max_age_seconds':2})['bytes']==len(PNG)
        receipt=broker.set_controls({**context,'request_id':'r1','controls':{'gain':20}})
        assert receipt['status']=='completed' and writes==[{'gain':20}]
    finally:broker.close();server.shutdown();server.server_close();thread.join(timeout=2)


def test_asgi_body_deadline_is_absolute_and_does_not_touch_owner(owner):
    obj,values,writes=owner
    api=api_for(obj)
    async def run():
        messages=[]
        async def receive():
            await asyncio.sleep(.35)
            return {'type':'http.request','body':b' ','more_body':True}
        async def send(message):messages.append(message)
        start=time.monotonic()
        await api({'type':'http','path':'/llm-camera/v1/controls','method':'POST',
            'headers':[(b'content-type',b'application/json')],'state':{'authorized':True}},receive,send)
        assert 1.8<=time.monotonic()-start<2.8
        assert messages[0]['status']==408
    asyncio.run(run())
    assert writes==[]


def test_owner_restart_changes_generation_even_with_identical_assignment():
    a=CaptureOwner(camera_id='cam',printer_id='dev',capture_owner='owner',profile=PROFILE,mount_revision='m')
    b=CaptureOwner(camera_id='cam',printer_id='dev',capture_owner='owner',profile=PROFILE,mount_revision='m')
    assert a.inspect()['connection_generation']!=b.inspect()['connection_generation']


def test_asgi_preserves_existing_application_routes(owner):
    obj,_,_=owner;calls=[]
    async def fallback(scope,receive,send):calls.append(scope['path'])
    api=CaptureOwnerAPI(obj,authorize=lambda _:False,fallback=fallback)
    asyncio.run(api({'type':'http','path':'/existing'},None,None))
    assert calls==['/existing']


def test_control_callback_does_not_block_existing_application_event_loop(owner):
    obj,values,writes=owner
    entered,release=threading.Event(),threading.Event()
    def blocked(change,*,deadline):
        entered.set();release.wait(timeout=1);writes.append(change);values.update(change)
    obj.writer=blocked
    fallback_calls=[]
    async def fallback(scope,receive,send):fallback_calls.append(scope['path'])
    api=CaptureOwnerAPI(obj,authorize=lambda _:True,fallback=fallback)
    async def run():
        task=asyncio.create_task(call(api,'controls',method='POST',data=json.dumps(request(obj)).encode()))
        start=time.monotonic()
        try:
            while not entered.is_set() and time.monotonic()-start<.5:await asyncio.sleep(.01)
            assert entered.is_set() and time.monotonic()-start<.5
            await api({'type':'http','path':'/existing'},None,None)
            assert fallback_calls==['/existing']
        finally:release.set()
        status,_=await task
        assert status==200
    asyncio.run(run())


@pytest.mark.parametrize('method',['inspect','snapshot','apply_controls','publish','begin_capture','configure_view'])
def test_inherited_owner_is_rejected_before_locks_or_callbacks(owner,method):
    obj,values,writes=owner
    obj._pid=-1
    obj.reader=lambda:(_ for _ in ()).throw(AssertionError('must not call inherited driver'))
    args={'inspect':{},'snapshot':{'deadline':time.time()+1},'apply_controls':{'request':{}},
        'publish':dict(connection_generation='old',expected_state_version=0,data=PNG,
            captured_at=0,received_at=0,width=1,height=1),
        'begin_capture':{},'configure_view':dict(profile=PROFILE,job_id=None,mount_revision='m',view_calibration_id=None)}
    with pytest.raises(RequestRejected,match='capture_owner_process_changed'):
        getattr(obj,method)(**args[method])
    assert writes==[]


def test_oversized_asgi_chunk_is_rejected_before_copying_into_buffer(owner):
    import tracemalloc
    obj,_,writes=owner
    api=api_for(obj)
    payload=b'x'*(2*1024*1024)
    tracemalloc.start()
    try:
        status,_=asyncio.run(call(api,'controls',method='POST',data=payload))
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    assert status==413 and writes==[]
    assert peak<256*1024
