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

    @classmethod
    def decode(cls, line):
        if type(line) is not str or len(line) > 384:
            raise ValueError('invalid_machine_state')
        match = re.fullmatch(
            r'//llmstate:v1 Q([0-9a-f]{8}) T([0-9a-f]{32}) N([0-9a-f]{8}) U([0-9a-f]{8})'
            r' F([0-9a-f]{2}) H([0-9a-f]{2}) K([0-9a-f]{2}) E([0-9a-f]{2})'
            r' B([0-9a-f]{2}) R([0-9a-f]{2}) A([0-9a-f]{2})'
            r' P([0-9a-f]{32}) O([0-9a-f]{24}) D([0-9a-f]{24}) V([0-9a-f]{72})', line)
        if not match:
            raise ValueError('invalid_machine_state')
        fields = match.groups()
        integers = tuple(int(value, 16) for value in fields[:11])
        request, session, sequence, _, flags, homed, trusted, enabled, _, relative, tool = integers
        if (not request or not session or not sequence or flags > 15 or homed > 7
                or trusted > 7 or enabled > 15 or relative > 15 or tool != 0):
            raise ValueError('invalid_machine_state')
        vectors = tuple(struct.unpack('>' + 'f' * (len(value)//8), bytes.fromhex(value)) for value in fields[11:])
        if not all(math.isfinite(value) for vector in vectors for value in vector):
            raise ValueError('nonfinite_machine_state')
        return cls(*integers, *vectors)

    def to_dict(self):
        axes = ('x', 'y', 'z', 'e')
        unit, feedrate, feed_percent, flow_percent, fan, nozzle, target, bed, bed_target = self.values
        return dict(request=self.request, session=self.session, sequence=self.sequence,
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
