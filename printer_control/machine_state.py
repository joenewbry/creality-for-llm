"""Strict controller-variable reports. No physical readiness or queue barrier."""
from dataclasses import dataclass
import math
import re
import struct


@dataclass(frozen=True)
class MachineStateReport:
    request: int
    session: int
    sequence: int
    uptime_ms: int
    flags: int
    homed: int
    trusted: int
    enabled: int
    planner_blocks: int
    relative: int
    tool: int
    position: tuple
    workspace: tuple
    home: tuple
    values: tuple
    version: int = 1
    inactivity: tuple | None = None
    idle_axes: int | None = None
    timeout_flags: int | None = None

    @classmethod
    def decode(cls, line):
        if type(line) is not str or len(line) > 384:
            raise ValueError('invalid_machine_state')
        version = 2 if line.startswith('//llmstate:v2 ') else 1
        match = re.fullmatch(
            rf'//llmstate:v{version} Q([0-9a-f]{{8}}) T([0-9a-f]{{32}}) N([0-9a-f]{{8}}) U([0-9a-f]{{8}})'
            r' F([0-9a-f]{2}) H([0-9a-f]{2}) K([0-9a-f]{2}) E([0-9a-f]{2})'
            r' B([0-9a-f]{2}) R([0-9a-f]{2}) A([0-9a-f]{2})'
            r' P([0-9a-f]{32}) O([0-9a-f]{24}) D([0-9a-f]{24}) V([0-9a-f]{72})'
            + (r' I([0-9a-f]{24}) C([0-9a-f]{2}) J([0-9a-f]{2})' if version == 2 else ''), line)
        if not match:
            raise ValueError('invalid_machine_state')
        fields = match.groups()
        integers = tuple(int(value, 16) for value in fields[:11])
        request, session, sequence, _, flags, homed, trusted, enabled, _, relative, tool = integers
        if (not request or not session or not sequence or flags > 15 or homed > 7
                or trusted > 7 or enabled > 15 or relative > 15 or tool != 0):
            raise ValueError('invalid_machine_state')
        vectors = tuple(struct.unpack('>' + 'f' * (len(value)//8), bytes.fromhex(value)) for value in fields[11:15])
        if not all(math.isfinite(value) for vector in vectors for value in vector):
            raise ValueError('nonfinite_machine_state')
        if version == 1:
            return cls(*integers, *vectors)
        timers = tuple(int(fields[15][i:i+8], 16) for i in range(0,24,8))
        idle_axes, timeout_flags = int(fields[16],16), int(fields[17],16)
        elapsed = (integers[3] - timers[0]) & 0xffffffff
        expected = (int(elapsed >= timers[1]) if idle_axes else 0) | (2 if timers[2] and elapsed >= timers[2] else 0)
        if (idle_axes > 15 or timeout_flags > 3 or timeout_flags != expected
                or (not idle_axes and timers[1])):
            raise ValueError('invalid_machine_inactivity')
        return cls(*integers, *vectors, version=2, inactivity=timers,
                   idle_axes=idle_axes, timeout_flags=timeout_flags)

    def to_dict(self):
        axes = ('x', 'y', 'z', 'e')
        unit, feedrate, feed_percent, flow_percent, fan, nozzle, target, bed, bed_target = self.values
        result = dict(request=self.request, session=self.session, sequence=self.sequence,
            uptime_ms=self.uptime_ms, firmware_running=bool(self.flags & 1), waiting_for_user=bool(self.flags & 2),
            waiting_for_heatup=bool(self.flags & 4), leveling_active=bool(self.flags & 8),
            homed={axis:bool(self.homed & (1<<i)) for i,axis in enumerate(axes[:3])},
            trusted={axis:bool(self.trusted & (1<<i)) for i,axis in enumerate(axes[:3])},
            enabled_logical={axis:bool(self.enabled & (1<<i)) for i,axis in enumerate(axes)},
            planner_blocks=self.planner_blocks, relative={axis:bool(self.relative & (1<<i)) for i,axis in enumerate(axes)},
            tool=self.tool, position=dict(zip(axes,self.position)), workspace_offset=dict(zip(axes[:3],self.workspace)),
            home_offset=dict(zip(axes[:3],self.home)), linear_unit_factor=unit, feedrate_mm_s=feedrate,
            feedrate_percent=feed_percent, flow_percent=flow_percent, fan_pwm=fan,
            temperatures={'T':dict(actual=nozzle,target=target),'B':dict(actual=bed,target=bed_target)},
            scope='controller_variables', position_kind='commanded_native', independent_measurement=False,
            planner_completion_proven=False, sensor_health_proven=False, physical_readiness_qualified=False)
        result['state_schema_version'] = self.version
        result['inactivity'] = None if self.inactivity is None else dict(
            previous_move_ms=self.inactivity[0], stepper_interval_ms=self.inactivity[1],
            max_interval_ms=self.inactivity[2],
            elapsed_modulo_ms=(self.uptime_ms-self.inactivity[0]) & 0xffffffff,
            disable_idle_axes={axis: bool(self.idle_axes & (1<<i)) for i,axis in enumerate(axes)},
            stepper_predicate=bool(self.timeout_flags & 1), max_predicate=bool(self.timeout_flags & 2),
            counter_bits=32, actual_shutdown_proven=False, motor_power_proven=False,
            complete_runtime_policy=False)
        return result
