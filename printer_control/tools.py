"""Typed, transport-independent LLM tools. No raw G-code or device-path tool."""
import json
from .journal import Journal, RequestRejected


class ToolRouter:
    READS = {'printer.describe', 'printer.status', 'job.inspect', 'job.pause_status', 'operation.get', 'printer.events', 'printer.observations'}

    def __init__(self, service):
        self.service = service
        self.reads = frozenset(getattr(service.backend, 'supported_reads', self.READS))

    def definitions(self):
        context = {
            'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
            'printer_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
            'connection_generation': {'type': 'string', 'minLength': 1, 'maxLength': 128},
            'expected_state_version': {'type': 'integer', 'minimum': 0},
            'deadline': {'type': 'number'},
            'arguments': {'type': 'object', 'properties': {'job_id': {'type': 'string'}},
                          'required': ['job_id'], 'additionalProperties': False},
        }
        definitions = []
        for name in self.service.backend.supported_operations:
            properties = json.loads(json.dumps(context))
            if name in ('printer.stop', 'printer.home'):
                properties['arguments'] = dict(type='object', properties={}, required=[], additionalProperties=False)
            if name in ('calibration.set_temporary','calibration.persist'):
                fields={'expected_settings_sha256':{'type':'string','pattern':'^[0-9a-f]{64}$'}}
                if name=='calibration.set_temporary':
                    fields.update(axis={'type':'string','enum':['x','y','z','e']},steps_per_mm={'type':'number','exclusiveMinimum':0})
                properties['arguments']=dict(type='object',properties=fields,required=list(fields),additionalProperties=False)
            if name=='printer.set_temperature':
                properties['arguments']=dict(type='object',properties={
                    'heater':{'type':'string','enum':['nozzle','bed']},
                    'target_celsius':{'type':'integer','minimum':0,'maximum':250}},
                    required=['heater','target_celsius'],additionalProperties=False)
            if name=='printer.set_fan':
                properties['arguments']=dict(type='object',properties={
                    'fan':{'type':'integer','const':0},'pwm':{'type':'integer','minimum':0,'maximum':255}},
                    required=['fan','pwm'],additionalProperties=False)
            if name == 'printer.jog':
                fields = {'axis': {'type': 'string', 'enum': ['x', 'y', 'z']},
                          'distance_mm': {'type': 'number', 'minimum': -10, 'maximum': 10},
                          'feedrate_mm_min': {'type': 'number', 'minimum': 1, 'maximum': 600}}
                properties['arguments'] = dict(type='object', properties=fields,
                    required=list(fields), additionalProperties=False)
            if name.startswith('prompt.'):
                fields = {} if name != 'prompt.respond' else {
                    'prompt': {'type': 'integer', 'minimum': 1, 'maximum': 0xffffffff},
                    'choice': {'type': 'integer', 'const': 0},
                }
                properties['arguments'] = dict(type='object', properties=fields,
                                               required=list(fields), additionalProperties=False)
            if name == 'job.stage':
                properties['arguments']['properties']['file_sha256'] = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
                properties['arguments']['required'].append('file_sha256')
            definitions.append(dict(name=name, description='Submit ' + name + '; returns a durable operation receipt, not immediate physical completion.',
                                    inputSchema=dict(type='object', properties=properties, required=list(context), additionalProperties=False)))
        for name in sorted(self.reads):
            properties = {}
            if name in ('operation.get','printer.machine_state'):
                properties = {'operation_id': {'type': 'string', 'minLength': 1, 'maxLength': 128}}
            elif name in ('printer.events', 'printer.observations', 'printer.machine_events'):
                properties = {'after': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 1000}}
            description=('Inspect pause phase, durable evidence and resume blockers; does not advance the workflow or authorize resume.'
                         if name=='job.pause_status' else 'Read ' + name + ' without issuing printer commands.')
            definitions.append(dict(name=name, description=description,
                                    inputSchema=dict(type='object', properties=properties, required=list(properties), additionalProperties=False)))
        return definitions

    def call(self, name, arguments):
        if type(name) is not str or type(arguments) is not dict:
            raise RequestRejected('invalid_tool_call')
        # Copy and bound JSON before any backend action. Reject NaN/Infinity.
        arguments = json.loads(Journal._encode(arguments))
        service = self.service
        if name in service.backend.supported_operations:
            if set(arguments) != Journal.REQUIRED - {'operation'}:
                raise RequestRejected('missing_or_unknown_tool_fields')
            return service.submit({**arguments, 'operation': name})
        if name not in self.reads:
            raise RequestRejected('unknown_tool')
        with service.backend.guard():
            service.lease.check()
            if name == 'operation.get':
                if set(arguments) != {'operation_id'} or type(arguments['operation_id']) is not str or not 1 <= len(arguments['operation_id']) <= 128:
                    raise RequestRejected('invalid_operation_id')
                return service.poll(arguments['operation_id'])
            if name == 'printer.machine_events':
                if set(arguments) != {'after', 'limit'}:
                    raise RequestRejected('invalid_machine_event_query')
                return service.backend.machine_events.read(**arguments)
            if name == 'printer.machine_state':
                if set(arguments) != {'operation_id'}:
                    raise RequestRejected('invalid_machine_state_query')
                return service.backend.state_reader.read(arguments['operation_id'])
            if name == 'printer.observations':
                if set(arguments) != {'after', 'limit'}:
                    raise RequestRejected('invalid_observation_query')
                events = service.journal.observations(**arguments)
                return {'scope': 'polled_state_observations', 'continuous_capture': False,
                        'observations': events,
                        'next_after': events[-1]['sequence'] if events else arguments['after']}
            if name == 'printer.events':
                if set(arguments) != {'after', 'limit'}:
                    raise RequestRejected('invalid_event_query')
                events = service.journal.events(**arguments)
                return {'scope': 'operation_receipts', 'events': [{**e, 'detail': json.loads(e['detail'])} for e in events],
                        'next_after': events[-1]['sequence'] if events else arguments['after']}
            if arguments:
                raise RequestRejected('unexpected_read_arguments')
            if name == 'printer.describe':
                return {'interface_version': 1, 'tools': self.definitions(), **service.backend.capabilities()}
            if name == 'job.pause_status':
                from .pause_status import inspect_pause
                snapshot=inspect_pause(service)
                service.journal.record_observation(snapshot,source=name,now=service.clock())
                return snapshot
            snapshot = service.backend.calibration.inspect() if name=='calibration.inspect' else service.backend.inspect()
            service.journal.record_observation(snapshot, source=name, now=service.clock())
            return snapshot


def serve(router, incoming, outgoing, *, max_line_bytes=131072):
    """Bounded newline JSON transport, deliberately not advertised as MCP.

    Envelope: {id, tool, arguments}. No listener, authentication change or USB
    connection is created here. The launcher owns process/input access control.
    """
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result

    for_line = incoming.readline
    while True:
        line = for_line(max_line_bytes + 1)
        if not line:
            return
        identifier = None
        try:
            if len(line.encode('utf-8')) > max_line_bytes:
                while line and not line.endswith('\n'):
                    line = for_line(max_line_bytes + 1)
                raise RequestRejected('request_too_large')
            request = json.loads(line, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite_json')))
            if type(request) is not dict or request.keys() != {'id', 'tool', 'arguments'}:
                raise RequestRejected('invalid_envelope')
            identifier = request['id']
            if type(identifier) not in (str, int) or (type(identifier) is str and not 1 <= len(identifier) <= 128):
                identifier = None
                raise RequestRejected('invalid_call_id')
            response = {'id': identifier, 'result': router.call(request['tool'], request['arguments'])}
        except (ValueError, TypeError, RecursionError) as exc:
            response = {'id': identifier, 'error': {'code': 'invalid_request', 'message': str(exc)[:256]}}
        except Exception:
            # Do not leak tracebacks, filesystem paths or exception contents.
            response = {'id': identifier, 'error': {'code': 'internal_error', 'message': 'Operation unavailable; inspect its durable receipt before retrying.'}}
        outgoing.write(json.dumps(response, allow_nan=False, separators=(',', ':')) + '\n')
        outgoing.flush()
