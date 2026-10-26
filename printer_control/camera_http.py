"""Pinned, bounded client for an existing capture owner's versioned camera API.

No device opens, discovery, redirects, proxies or DNS lookups. Configuration is
operator supplied; the LLM cannot choose an endpoint or destination address.
"""
import base64
import http.client
import ipaddress
import json
import math
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from .journal import Journal, RequestRejected


class CaptureOwnerHTTP:
    MAX_RESPONSE = 110000
    PREFIX = '/llm-camera/v1/'

    def __init__(self, origin, *, pinned_address, headers=None, timeout=5,
                 allow_loopback_http=False, clock=time.time):
        url = urlsplit(origin)
        address = ipaddress.ip_address(pinned_address)
        if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
                or url.path not in ('', '/') or url.query or url.fragment
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 10):
            raise ValueError('invalid_capture_owner_endpoint')
        if url.scheme == 'http' and not (allow_loopback_http and address.is_loopback):
            raise ValueError('capture_owner_tls_required')
        self.host, self.port = url.hostname, url.port or (443 if url.scheme == 'https' else 80)
        self.tls, self.address, self.timeout = url.scheme == 'https', address, timeout
        self.clock, self.headers = clock, dict(headers or {})
        # Avoid operator headers changing routing/framing or enabling upgrades.
        if any(k.lower() not in ('authorization', 'x-camera-token') or not isinstance(v, str)
               or '\r' in v or '\n' in v for k,v in self.headers.items()):
            raise ValueError('invalid_capture_owner_headers')
        self.lock, self.last_state = threading.RLock(), None

    def _request(self, endpoint, *, deadline, payload=None):
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= now):
            raise RequestRejected('capture_owner_deadline_expired')
        duration = min(self.timeout, deadline-now)
        end = time.monotonic()+duration
        connection = http.client.HTTPConnection(self.host, self.port, timeout=duration)
        active = [None]
        def remaining():
            left = end-time.monotonic()
            if left <= 0:
                raise TimeoutError('capture_owner_deadline_expired')
            return left
        def connect():
            sock = socket.socket(socket.AF_INET6 if self.address.version == 6 else socket.AF_INET, socket.SOCK_STREAM)
            connection.sock = active[0] = sock
            sock.settimeout(remaining())
            sock.connect((str(self.address), self.port))
            if self.tls:
                context = ssl.create_default_context()
                sock.settimeout(remaining())
                connection.sock = context.wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
                active[0] = connection.sock
                connection.sock.settimeout(remaining())
                connection.sock.do_handshake()
        connection.connect = connect
        def interrupt():
            sock = active[0]
            if sock is not None:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        timer = threading.Timer(duration, interrupt)
        timer.daemon = True
        timer.start()
        try:
            headers = {**self.headers, 'Accept': 'application/json', 'Accept-Encoding': 'identity'}
            body = None if payload is None else Journal._encode(payload).encode()
            if body is not None: headers['Content-Type'] = 'application/json'
            connection.request('GET' if body is None else 'POST', self.PREFIX+endpoint, body=body, headers=headers)
            connection.sock.settimeout(remaining())
            response = connection.getresponse()
            if response.status != 200:
                raise RequestRejected('capture_owner_http_rejected')
            if (response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json'
                    or response.getheader('Content-Encoding', 'identity').lower() != 'identity'):
                raise RequestRejected('capture_owner_metadata_required')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdecimal() or int(length)>self.MAX_RESPONSE):
                raise RequestRejected('capture_owner_response_too_large')
            data = bytearray()
            while not response.isclosed():
                active[0].settimeout(remaining())
                part = response.read1(min(4096,self.MAX_RESPONSE+1-len(data)))
                if not part: break
                data.extend(part)
                if len(data)>self.MAX_RESPONSE:
                    raise RequestRejected('capture_owner_response_too_large')
            remaining()
            if self.clock() >= deadline:
                raise RequestRejected('capture_owner_deadline_expired')
            def pairs(items):
                result = {}
                for key,value in items:
                    if key in result: raise ValueError('duplicate_json_key')
                    result[key]=value
                return result
            def constant(_): raise ValueError('nonfinite_json')
            result = json.loads(data,object_pairs_hook=pairs,parse_constant=constant)
            Journal._encode(result)
            if type(result) is not dict or result.get('protocol_version') != 1 or type(result.get('protocol_version')) is not int:
                raise RequestRejected('unsupported_capture_owner_protocol')
            return result
        except Exception:
            # Do not expose endpoint credentials, response bodies or internal routing.
            raise RequestRejected('capture_owner_response_unavailable_or_invalid') from None
        finally:
            timer.cancel()
            connection.close()

    def inspect(self):
        with self.lock:
            self.last_state = None
            result = self._request('state',deadline=self.clock()+self.timeout)
            if set(result) != {'protocol_version','state'} or type(result['state']) is not dict:
                raise RequestRejected('invalid_capture_owner_state')
            self.last_state = result['state']
            return json.loads(Journal._encode(self.last_state))

    def snapshot(self, *, deadline):
        with self.lock:
            result = self._request('snapshot',deadline=deadline)
            if set(result) != {'protocol_version','frame'} or type(result['frame']) is not dict:
                raise RequestRejected('invalid_capture_owner_frame')
            frame = result['frame']
            encoded = frame.pop('data_base64',None)
            if type(encoded) is not str or len(encoded)>87384 or 'data' in frame:
                raise RequestRejected('invalid_capture_owner_image')
            try: frame['data'] = base64.b64decode(encoded,validate=True)
            except ValueError: raise RequestRejected('invalid_capture_owner_image') from None
            return frame

    def set_controls(self, values, *, deadline):
        with self.lock:
            if self.last_state is None:
                raise RequestRejected('capture_owner_state_required')
            state = self.last_state
            result = self._request('controls',deadline=deadline,payload={
                'camera_id':state['camera_id'],'connection_generation':state['connection_generation'],
                'expected_state_version':state['state_version'],'deadline':deadline,'controls':values})
            if set(result) != {'protocol_version','accepted'} or result['accepted'] is not True:
                raise RequestRejected('capture_owner_controls_unconfirmed')
            # This acknowledges dispatch only. Broker completion requires a new state readback.
