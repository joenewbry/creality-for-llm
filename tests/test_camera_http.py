"""Actual local HTTP exchanges; no camera devices or external routes."""
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from printer_control.camera import CameraBroker, CameraTools
from printer_control.camera_http import CaptureOwnerHTTP
from printer_control.journal import RequestRejected

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


@pytest.fixture
def owner():
    state=dict(camera_id='cam',assigned_printer_id='dev',capture_owner='owner',connection_generation='g1',
        state_version=0,controls={'gain':{'minimum':0,'maximum':100}},values={'gain':10})
    fixture={'state':state,'posts':[],'mode':'normal','paths':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            fixture['paths'].append(self.path)
            if fixture['mode']=='legacy':
                self.send_response(200);self.send_header('Content-Type','image/jpeg');self.end_headers();self.wfile.write(b'\xff\xd8\xff');return
            if fixture['mode']=='redirect':
                self.send_response(302);self.send_header('Location','http://never.invalid/');self.end_headers();return
            if fixture['mode']=='drip':
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                try:
                    for _ in range(30):self.wfile.write(b' ');self.wfile.flush();time.sleep(.04)
                except (BrokenPipeError,ConnectionResetError): pass
                return
            if self.path.endswith('state'): result={'protocol_version':1,'state':state}
            else:
                frame=dict(camera_id='cam',assigned_printer_id='dev',connection_generation='g1',job_id='job',
                    frame_id='f1',captured_at=time.time()-.01,received_at=time.time(),width=1,height=1,
                    crop='full_encoded_frame',view_calibration_id=None,mount_revision='proto',
                    media_type='image/png',data_base64=base64.b64encode(PNG).decode())
                result={'protocol_version':1,'frame':frame}
            data=json.dumps(result).encode()
            if fixture['mode']=='duplicate':data=b'{"protocol_version":1,"protocol_version":1,"state":{}}'
            if fixture['mode']=='nan':data=b'{"protocol_version":1,"state":{"x":NaN}}'
            if fixture['mode']=='oversize':data=b'x'*110001
            if fixture['mode']=='bad_base64' and 'frame' in result:
                result['frame']['data_base64']='!!!';data=json.dumps(result).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            fixture['posts'].append(data)
            assert self.path=='/llm-camera/v1/controls'
            assert data['camera_id']=='cam' and data['connection_generation']=='g1'
            assert data['expected_state_version']==state['state_version']
            if fixture['mode']!='no_readback':
                state['values'].update(data['controls']);state['state_version']+=1
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"protocol_version":1,"accepted":true}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    fixture['origin']='http://camera-owner.invalid:'+str(server.server_port)
    fixture['handler']=Handler
    try:yield fixture
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def adapter(owner,**kwargs):
    return CaptureOwnerHTTP(owner['origin'],pinned_address='127.0.0.1',allow_loopback_http=True,**kwargs)


def test_actual_http_owner_to_broker_frame_and_control_receipts(owner,tmp_path):
    backend=adapter(owner)
    broker=CameraBroker(tmp_path/'camera.sqlite',backend,camera_id='cam',printer_id='dev',capture_owner='owner')
    tools=CameraTools(broker)
    try:
        state=tools.call('camera.inspect',{})
        ctx=dict(camera_id='cam',connection_generation='g1',expected_state_version=0,deadline=time.time()+5)
        frame=tools.call('camera.snapshot',{**ctx,'job_id':'job','max_age_seconds':2})
        assert base64.b64decode(frame['data_base64'])==PNG
        req={**ctx,'request_id':'r1','controls':{'gain':20}}
        receipt=tools.call('camera.set_controls',req)
        assert receipt['status']=='completed'
        assert tools.call('camera.set_controls',req)==receipt
        assert len(owner['posts'])==1
        assert all(p.startswith('/llm-camera/v1/') for p in owner['paths'])
    finally:broker.close()


@pytest.mark.parametrize('mode',['legacy','redirect','duplicate','nan','oversize'])
def test_metadata_and_format_failures_are_closed_without_response_leaks(owner,mode):
    owner['mode']=mode
    with pytest.raises(RequestRejected,match='capture_owner_response_unavailable_or_invalid'):
        adapter(owner).inspect()
    assert owner['posts']==[]
    assert owner['paths']==['/llm-camera/v1/state']


def test_dripping_response_cannot_extend_total_timeout(owner):
    owner['mode']='drip'
    start=time.monotonic()
    with pytest.raises(RequestRejected):adapter(owner,timeout=.15).inspect()
    assert time.monotonic()-start < .8


def test_invalid_base64_never_reaches_broker(owner):
    owner['mode']='bad_base64'
    with pytest.raises(RequestRejected):adapter(owner).snapshot(deadline=time.time()+2)


def test_missing_owner_readback_stays_unknown_and_never_retries(owner,tmp_path):
    owner['mode']='no_readback'
    broker=CameraBroker(tmp_path/'camera.sqlite',adapter(owner),camera_id='cam',printer_id='dev',capture_owner='owner')
    request=dict(camera_id='cam',connection_generation='g1',expected_state_version=0,
                 deadline=time.time()+5,request_id='r1',controls={'gain':20})
    try:
        receipt=broker.set_controls(request)
        assert receipt['status']=='outcome_unknown'
        assert broker.set_controls(request)==receipt
        assert len(owner['posts'])==1
    finally:broker.close()


@pytest.mark.parametrize('origin,pin,options',[
    ('http://external.invalid','192.0.2.1',{'allow_loopback_http':True}),
    ('http://local.invalid','127.0.0.1',{}),
    ('https://user:secret@owner.invalid','127.0.0.1',{}),
    ('https://owner.invalid/other','127.0.0.1',{}),
    ('https://owner.invalid?token=secret','127.0.0.1',{}),
])
def test_endpoint_rejects_unencrypted_remote_routes_and_embedded_secrets(origin,pin,options):
    with pytest.raises(ValueError):CaptureOwnerHTTP(origin,pinned_address=pin,**options)


def test_no_controls_without_an_inspected_owner_state(owner):
    with pytest.raises(RequestRejected):adapter(owner).set_controls({'gain':20},deadline=time.time()+1)
    assert owner['posts']==[]


def test_real_tls_certificate_trust_and_hostname_checks(owner,tmp_path,monkeypatch):
    import ssl,subprocess,shutil
    openssl=shutil.which('openssl')
    if not openssl:pytest.skip('openssl required for ephemeral TLS fixture')
    key,cert=tmp_path/'key.pem',tmp_path/'cert.pem'
    subprocess.run([openssl,'req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
        '-out',str(cert),'-days','1','-subj','/CN=camera-owner.invalid',
        '-addext','subjectAltName=DNS:camera-owner.invalid'],check=True,capture_output=True)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
    server=ThreadingHTTPServer(('127.0.0.1',0),owner['handler'])
    server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        origin='https://camera-owner.invalid:'+str(server.server_port)
        monkeypatch.delenv('SSL_CERT_FILE',raising=False)
        with pytest.raises(RequestRejected):CaptureOwnerHTTP(origin,pinned_address='127.0.0.1').inspect()
        monkeypatch.setenv('SSL_CERT_FILE',str(cert))
        assert CaptureOwnerHTTP(origin,pinned_address='127.0.0.1').inspect()['camera_id']=='cam'
        with pytest.raises(RequestRejected):
            CaptureOwnerHTTP('https://wrong.invalid:'+str(server.server_port),pinned_address='127.0.0.1').inspect()
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_proxy_environment_cannot_reroute_pinned_owner(owner,monkeypatch):
    monkeypatch.setenv('HTTP_PROXY','http://never.invalid:1')
    monkeypatch.setenv('HTTPS_PROXY','http://never.invalid:1')
    assert adapter(owner).inspect()['capture_owner']=='owner'
