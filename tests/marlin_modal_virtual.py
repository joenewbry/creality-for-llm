"""Opt-in test simulator extension, not upstream OctoPrint or physical firmware.

Covers one tool, millimeter linear moves, independent E modes and one fan.
Moves snapshot modes/feed overrides when queued. No arcs, volumetric extrusion,
kinematics, collision, temperature or real-time behavior is qualified here.
"""
from dataclasses import dataclass
import math
import re
import struct
import time


def f32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


def parameters(line, allowed):
    words = line.split(';', 1)[0].strip().split()
    result = {}
    for word in words[1:]:
        if re.fullmatch(r'[A-Z][+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', word) is None:
            raise ValueError('unsupported_simulator_parameter')
        key, value = word[0], float(word[1:])
        if key not in allowed or key in result or not math.isfinite(value):
            raise ValueError('unsupported_simulator_parameter')
        result[key] = f32(value)
    return result


@dataclass(frozen=True)
class QueuedLinearMove:
    line: str
    axes: tuple
    xyz_relative: bool
    e_relative: bool
    feedrate_mm_s: float
    feedrate_percent: float
    flow_percent: float


class MarlinModalMixin:
    def _kill(self):
        # Explicit test model of Marlin kill()->disable_all_heaters(). Stock
        # VirtualPrinter sets only a flag; neither model qualifies physical stop.
        super()._kill()
        if self._killed:
            self.targetTemp[:]=[0. for _ in self.targetTemp]
            self.bedTargetTemp=0.
            self.chamberTargetTemp=0.

    def __init__(self, *args, **kwargs):
        self._e_override = None
        self._fan_pwm = 0
        self._command_feedrate_mm_s = None
        self._executed_feedrate_mm_s = None
        self.modal_moves = []
        super().__init__(*args, **kwargs)

    @property
    def e_relative(self):
        return self._relative if self._e_override is None else self._e_override

    def _gcode_G90(self, data):
        parameters(data, '')
        self._relative, self._e_override = False, None

    def _gcode_G91(self, data):
        parameters(data, '')
        self._relative, self._e_override = True, None

    def _gcode_M82(self, data):
        parameters(data, '')
        self._e_override = False

    def _gcode_M83(self, data):
        parameters(data, '')
        self._e_override = True

    def _gcode_M106(self, data):
        values = parameters(data, 'PS')
        fan, speed = values.get('P', 0), values.get('S', 255)
        if fan != 0 or speed != int(speed) or not 0 <= speed <= 255:
            raise ValueError('unsupported_simulator_fan')
        self._fan_pwm = int(speed)

    def _gcode_M107(self, data):
        if parameters(data, 'P').get('P', 0) != 0:
            raise ValueError('unsupported_simulator_fan')
        self._fan_pwm = 0

    def _gcode_G0(self, data):
        values = parameters(data, 'XYZEF')
        if self.currentExtruder != 0 or self._unitModifier != 1:
            raise ValueError('unsupported_simulator_tool_or_units')
        feed = values.get('F')
        if feed is not None:
            if not 0 < feed <= 60000:
                raise ValueError('unsupported_simulator_feedrate')
            self._command_feedrate_mm_s = f32(feed / 60)
        if self._command_feedrate_mm_s is None:
            raise ValueError('simulator_feedrate_uninitialized')
        move = QueuedLinearMove(data, tuple((k, values[k]) for k in 'XYZE' if k in values),
            self._relative, self.e_relative, self._command_feedrate_mm_s,
            self._feedrate_multiplier, self._flowrate_multiplier)
        self.buffered.put(move)

    _gcode_G1 = _gcode_G0

    def _gcode_G2(self, data):
        raise ValueError('simulator_arcs_unqualified')

    _gcode_G3 = _gcode_G2

    def _performMove(self, move):
        if not isinstance(move, QueuedLinearMove):
            raise ValueError('simulator_move_missing_modal_snapshot')
        # Existing positions must be established by fixture homing/G92 before
        # moves; never infer a physical origin from an unknown coordinate.
        position = dict(X=self._lastX, Y=self._lastY, Z=self._lastZ, E=self._lastE[0])
        if any(v is None for v in position.values()):
            raise ValueError('simulator_position_uninitialized')
        for axis, value in move.axes:
            relative = move.e_relative if axis == 'E' else move.xyz_relative
            position[axis] = f32(position[axis] + value) if relative else value
        self._lastX, self._lastY, self._lastZ, self._lastE[0] = (position[k] for k in 'XYZE')
        self._executed_feedrate_mm_s = move.feedrate_mm_s
        self._lastF = move.feedrate_mm_s * 60
        self.modal_moves.append(move)
        time.sleep(getattr(self, 'model_move_delay', 0))
        self.completed_moves.append(move.line.strip())
