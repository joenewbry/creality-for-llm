"""ASGI endpoint adapter to embed behind the existing acquisition owner's gate.

No listener, native camera, credentials, worker or service is created here.
"""
import base64
import asyncio
import json
import time
from .journal import Journal, RequestRejected


class OwnerHTTPError(Exception):
    def __init__(self, status, code):
        self.status,self.code=status,code


class CaptureOwnerAPI:
    PREFIX='/llm-camera/v1/'

    def __init__(self, owner, *, authorize, fallback=None, clock=time.time):
        if not callable(authorize): raise ValueError('capture_owner_authorization_required')
        self.owner,self.authorize,self.fallback,self.clock=owner,authorize,fallback,clock

    async def __call__(self, scope, receive, send):
        if scope.get('type')!='http' or not scope.get('path','').startswith(self.PREFIX):
            if self.fallback is not None: return await self.fallback(scope,receive,send)
            if scope.get('type')!='http': raise ValueError('unsupported_asgi_scope')
            return await self._reply(send,404,{'error':'not_found'})
        # Reject before reading body or touching owner metadata/callbacks.
        try: allowed=self.authorize(scope) is True
        except Exception: allowed=False
        if not allowed: return await self._reply(send,403,{'error':'forbidden'})
        path,method=scope['path'][len(self.PREFIX):],scope.get('method')
        status=200
        try:
            if path=='state' and method=='GET':
                result={'protocol_version':1,'state':await asyncio.to_thread(self.owner.inspect)}
            elif path=='snapshot' and method=='GET':
                frame=await asyncio.to_thread(self.owner.snapshot,deadline=self.clock()+2)
                frame['data_base64']=base64.b64encode(frame.pop('data')).decode('ascii')
                result={'protocol_version':1,'frame':frame}
            elif path=='controls' and method=='POST':
                headers={k.lower():v for k,v in scope.get('headers',[])}
                if headers.get(b'content-type',b'').split(b';')[0].strip().lower()!=b'application/json':
                    raise RequestRejected('owner_json_required')
                raw=bytearray()
                end=time.monotonic()+2
                while True:
                    message=await asyncio.wait_for(receive(),timeout=max(0,end-time.monotonic()))
                    if message['type']!='http.request': raise RequestRejected('owner_body_interrupted')
                    raw.extend(message.get('body',b''))
                    if len(raw)>8192: raise OwnerHTTPError(413,'body_too_large')
                    if not message.get('more_body'): break
                def pairs(items):
                    result={}
                    for key,value in items:
                        if key in result: raise ValueError('duplicate_json_key')
                        result[key]=value
                    return result
                def constant(_): raise ValueError('nonfinite_json')
                request=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
                result=await asyncio.to_thread(self.owner.apply_controls,request)
            else:
                raise OwnerHTTPError(404,'unsupported_owner_route')
        except OwnerHTTPError as exc:
            status,result=exc.status,{'error':exc.code}
        except TimeoutError:
            status,result=408,{'error':'body_deadline_expired'}
        except (RequestRejected,ValueError,TypeError,KeyError,RecursionError):
            status,result=409,{'error':'owner_request_unavailable_or_invalid'}
        except Exception:
            status,result=503,{'error':'owner_operation_unconfirmed'}
        await self._reply(send,status,result)

    @staticmethod
    async def _reply(send, status, body):
        data=Journal._encode(body).encode()
        if len(data)>110000:
            status,data=503,b'{"error":"owner_response_too_large"}'
        await send({'type':'http.response.start','status':status,'headers':[
            (b'content-type',b'application/json'),(b'content-length',str(len(data)).encode()),
            (b'cache-control',b'private, no-store'),(b'x-content-type-options',b'nosniff')]})
        await send({'type':'http.response.body','body':data})
