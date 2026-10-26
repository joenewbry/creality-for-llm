"""Bounded setup on the pinned software printer; no physical transport.

Homing here proves simulator coordinates only, never endstop/probe behavior.
The existing virtual backend owns the sender, lock, journal and receive hook.
"""
import math
import queue
import uuid

from .journal import RequestRejected
from .service import Outcome


class VirtualSetup:
    OPERATIONS = ('printer.home', 'printer.jog', 'printer.set_fan', 'printer.set_temperature')
    TEMPERATURE_LIMITS={'nozzle':250,'bed':90}
    LIMITS = {'x': (0., 220.), 'y': (0., 220.), 'z': (0., 270.)}

    def __init__(self, backend):
        self.backend = backend
        self.position = None
        self.pending = {}
        self.received = queue.SimpleQueue()
        self.revision = 0
        self.uncertain = False

    def observe(self, line):
        if line.startswith('echo:LLM_SETUP_'):
            self.received.put(line.strip()[5:])

    def actual_position(self):
        device = self.backend.sender._serial
        result = {axis: getattr(device, '_last' + axis.upper(), None) for axis in self.LIMITS}
        if not all(type(v) in (float, int) and math.isfinite(v) for v in result.values()):
            raise RequestRejected('setup_position_unknown')
        return result

    def validate(self, operation, arguments):
        backend = self.backend
        sender, device = backend.sender, backend.sender._serial
        if self.uncertain:
            raise RequestRejected('setup_outcome_requires_recovery')
        if sender.getState() != sender.STATE_OPERATIONAL or backend._started:
            raise RequestRejected('setup_requires_idle_before_first_job')
        if device._killed or device._unitModifier != 1.0:
            raise RequestRejected('setup_virtual_profile_unqualified')
        if operation!='printer.set_temperature' and (any(device.targetTemp) or device.bedTargetTemp or device.chamberTargetTemp):
            raise RequestRejected('setup_requires_heaters_off')
        with device.buffered.all_tasks_done:
            if device.buffered.unfinished_tasks:
                raise RequestRejected('setup_moves_not_drained')
        if operation=='printer.set_temperature':
            if (set(arguments)!={'heater','target_celsius'} or type(arguments['heater']) is not str
                    or arguments['heater'] not in self.TEMPERATURE_LIMITS
                    or type(arguments['target_celsius']) is not int
                    or not 0<=arguments['target_celsius']<=self.TEMPERATURE_LIMITS[arguments['heater']]):
                raise RequestRejected('invalid_temperature_arguments')
            if len(device.targetTemp)!=1 or device.chamberTargetTemp:
                raise RequestRejected('temperature_profile_unqualified')
            observed=self.temperatures()[arguments['heater']]
            if arguments['target_celsius'] and (observed['actual_celsius'] is None
                    or not 0<=observed['actual_celsius']<=self.TEMPERATURE_LIMITS[arguments['heater']]+30):
                raise RequestRejected('temperature_sensor_unqualified')
            return
        if operation=='printer.set_fan':
            if (set(arguments)!={'fan','pwm'} or type(arguments['fan']) is not int or arguments['fan']!=0
                    or type(arguments['pwm']) is not int or not 0<=arguments['pwm']<=255):
                raise RequestRejected('invalid_fan_arguments')
            if type(getattr(device,'_fan_pwm',None)) is not int:
                raise RequestRejected('fan_observation_unavailable')
            return
        if operation == 'printer.home':
            if arguments != {}:
                raise RequestRejected('unexpected_home_arguments')
            return
        if set(arguments) != {'axis', 'distance_mm', 'feedrate_mm_min'}:
            raise RequestRejected('invalid_jog_fields')
        axis, distance, feed = (arguments[k] for k in ('axis', 'distance_mm', 'feedrate_mm_min'))
        if type(axis) is not str or axis not in self.LIMITS:
            raise RequestRejected('invalid_jog_axis')
        if type(distance) not in (int, float) or not math.isfinite(distance) or not .001 <= abs(distance) <= 10:
            raise RequestRejected('jog_distance_out_of_range')
        if type(feed) not in (int, float) or not math.isfinite(feed) or not 1 <= feed <= 600:
            raise RequestRejected('jog_feedrate_out_of_range')
        if self.position is None or self.actual_position() != self.position:
            raise RequestRejected('setup_home_required_or_position_changed')
        low, high = self.LIMITS[axis]
        if not low <= self.position[axis] + distance <= high:
            raise RequestRejected('jog_target_out_of_bounds')

    def dispatch(self, operation_id, operation, arguments, deadline):
        self.validate(operation, arguments)
        fan=operation=='printer.set_fan'
        temperature=operation=='printer.set_temperature'
        setting=fan or temperature
        target = dict.fromkeys(self.LIMITS, 0.) if operation == 'printer.home' else (None if setting else dict(self.position))
        commands = [] if setting else ['G90']
        if temperature:
            code='M104' if arguments['heater']=='nozzle' else 'M140'
            commands.append(f"{code} S{arguments['target_celsius']}")
        elif fan:
            commands.append(f"M106 P0 S{arguments['pwm']}")
        elif operation == 'printer.home':
            commands.append('G28 X Y Z')
        else:
            axis = arguments['axis']
            target[axis] = round(target[axis] + arguments['distance_mm'], 6)
            commands.append(f"G1 {axis.upper()}{target[axis]:.6f} F{arguments['feedrate_mm_min']:.6f}")
        token = 'LLM_SETUP_' + uuid.uuid4().hex
        self.pending[operation_id] = dict(target=target, token=token, deadline=deadline, observed=False,
            fan_pwm=arguments['pwm'] if fan else None,
            temperature=dict(arguments) if temperature else None)
        if not setting:self.position = None
        self.revision += 1
        try:
            for command in commands + ['M400', 'M118 E1 ' + token]:
                if not self.backend.sender.sendCommand(command, tags={'source:llm-setup'}):
                    raise OSError('setup command not queued')
        except Exception:
            self.uncertain = True
            raise

    def poll(self, operation_id):
        while True:
            try:
                token = self.received.get_nowait()
            except queue.Empty:
                break
            for pending in self.pending.values():
                if token == pending['token']:
                    pending['observed'] = True
        pending = self.pending[operation_id]
        device = self.backend.sender._serial
        if pending['observed']:
            with device.buffered.all_tasks_done:
                drained = device.buffered.unfinished_tasks == 0
            if pending['temperature'] is not None:
                requested=pending['temperature']
                observed=self.temperatures()[requested['heater']]
                if drained and observed['target_celsius']==requested['target_celsius']:
                    self.revision+=1
                    return Outcome('completed',{'evidence_type':'virtual_temperature_target_barrier_and_state',
                        **requested,'actual_celsius':observed['actual_celsius'],
                        'temperature_readiness_verified':False,'physical_heating_qualified':False})
            elif pending['fan_pwm'] is not None:
                if drained and getattr(device,'_fan_pwm',None)==pending['fan_pwm']:
                    self.revision+=1
                    return Outcome('completed',{'evidence_type':'virtual_fan_barrier_and_state',
                        'fan':0,'pwm':pending['fan_pwm'],'physical_fan_qualified':False,
                'temperature_observations':self.temperatures(),
                'temperature_readiness_verified':False,
                'temperature_target_limits_celsius':self.TEMPERATURE_LIMITS})
            elif drained and self.actual_position() == pending['target']:
                self.position = dict(pending['target'])
                self.revision += 1
                return Outcome('completed', {'evidence_type': 'virtual_setup_barrier_echo_and_coordinates',
                    'position': dict(self.position), 'physical_motion_qualified': False,
                    'position_evidence': 'simulator_internal_coordinates', 'axis_mode': 'absolute'})
        if self.backend.clock() >= pending['deadline']:
            self.uncertain = True
            self.revision += 1
            return Outcome('outcome_unknown', {'reason': 'setup_temperature_target_unconfirmed' if pending['temperature'] is not None else 'setup_fan_state_unconfirmed' if pending['fan_pwm'] is not None else 'setup_barrier_or_position_unconfirmed',
                                               'requires_recovery': True})
        return None

    def temperatures(self):
        device=self.backend.sender._serial
        def number(value):
            return value if type(value) in (int,float) and math.isfinite(value) else None
        actual=getattr(device,'temp',[])
        targets=getattr(device,'targetTemp',[])
        return {'nozzle':{'actual_celsius':number(actual[0]) if actual else None,
                          'target_celsius':number(targets[0]) if targets else None},
                'bed':{'actual_celsius':number(getattr(device,'bedTemp',None)),
                       'target_celsius':number(getattr(device,'bedTargetTemp',None))}}

    def inspect(self):
        position = self.position if not self.backend._started and not self.backend._fault else None
        if position is not None:
            try:
                if self.actual_position() != position:
                    position = None
            except RequestRejected:
                position = None
        return {'scope': 'software_virtual_device_only', 'position': position,
                'position_evidence': 'simulator_internal_coordinates' if position else 'unknown',
                'physical_homing_qualified': False, 'outcome_uncertain': self.uncertain,
                'available_before_first_job_only': True, 'max_jog_mm': 10,
                'max_feedrate_mm_min': 600, 'axis_limits_mm': self.LIMITS,
                'heating_enabled':'printer.set_temperature' in getattr(self.backend,'supported_operations',()),
                'extrusion_enabled': False,
                'fan_pwm':getattr(self.backend.sender._serial,'_fan_pwm',None),
                'physical_fan_qualified':False,
                'temperature_observations':self.temperatures(),
                'temperature_readiness_verified':False,
                'temperature_target_limits_celsius':self.TEMPERATURE_LIMITS}
