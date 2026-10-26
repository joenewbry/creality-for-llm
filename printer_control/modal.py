"""Track modal commands in *transmitted* order for the pinned Marlin dialect.

This is commanded state, not proof of execution. A checkpoint additionally needs
a completed barrier, firmware position/configuration evidence and continuity.
Unknown or malformed commands latch uncertainty until explicit requalification.
"""
from dataclasses import dataclass, field
import math
import re

_COMMAND = re.compile(r'([GMT])(\d+)(.*)', re.ASCII)
_PARAMETER = re.compile(r'([A-Z])([+-]?(?:\d+(?:\.\d*)?|\.\d+))', re.ASCII)


@dataclass
class ModalTracker:
    units: str | None = None
    xyz_relative: bool | None = None
    e_override: bool | None = None
    feedrate: float | None = None
    tool: int | None = None
    fan_pwm: float | None = None
    nozzle_target: float | None = None
    bed_target: float | None = None
    feedrate_percent: int | None = None
    flow_percent: int | None = None
    uncertainty: set[str] = field(default_factory=set)
    commands_observed: int = 0

    @property
    def e_relative(self):
        return self.xyz_relative if self.e_override is None else self.e_override

    def snapshot(self):
        return dict(units=self.units, xyz_relative=self.xyz_relative,
                    e_relative=self.e_relative, e_override=self.e_override,
                    feedrate=self.feedrate, tool=self.tool, fan_pwm=self.fan_pwm,
                    nozzle_target=self.nozzle_target, bed_target=self.bed_target,
                    feedrate_percent=self.feedrate_percent, flow_percent=self.flow_percent,
                    uncertainty=sorted(self.uncertainty), commands_observed=self.commands_observed)

    def blockers(self):
        required = ('units', 'xyz_relative', 'e_relative', 'feedrate', 'tool',
                    'fan_pwm', 'nozzle_target', 'bed_target')
        values = self.snapshot()
        return sorted(self.uncertainty) + ['unknown_' + k for k in required if values[k] is None]

    def observe(self, line):
        if type(line) is not str or len(line) > 1024 or not line.isascii() or any(ord(c) < 32 for c in line):
            self.uncertainty.add('malformed_command')
            return
        line = line.split(';', 1)[0].strip().upper()
        if not line:
            return
        self.commands_observed += 1
        match = _COMMAND.fullmatch(line)
        if not match:
            self.uncertainty.add('unparsed_command')
            return
        letter, number, tail = match.groups()
        code = letter + str(int(number))
        if code in ('M117', 'M118'):
            return  # display/host text is not a numeric parameter list
        params = {}
        tail = tail.strip()
        while tail:
            token = _PARAMETER.match(tail)
            if not token or token[1] in params:
                self.uncertainty.add('malformed_parameters')
                return
            value = float(token[2])
            if not math.isfinite(value):
                self.uncertainty.add('nonfinite_parameter')
                return
            params[token[1]] = value
            tail = tail[token.end():].lstrip()

        allowed = {
            'G20': '', 'G21': '', 'G90': '', 'G91': '', 'M82': '', 'M83': '',
            'G0': 'XYZEF', 'G1': 'XYZEF', 'G2': 'XYZEFIJKR', 'G3': 'XYZEFIJKR',
            'G4': 'PS', 'G92': 'XYZE', 'T0': '', 'M106': 'SP', 'M107': 'P',
            'M104': 'ST', 'M109': 'SRT', 'M140': 'S', 'M190': 'SR',
            'M220': 'S', 'M221': 'ST',
            'M105': '', 'M114': '', 'M115': '', 'M400': '', 'M27': '', 'M503': '',
        }
        if code not in allowed:
            self.uncertainty.add('unsupported_' + code)
            return
        if set(params) - set(allowed[code]):
            self.uncertainty.add('unsupported_parameters_' + code)
            return
        if code in ('G90', 'G91'):
            self.xyz_relative = code == 'G91'
            self.e_override = None  # exact pinned GcodeSuite::set_relative_mode semantics
        elif code in ('M82', 'M83'):
            self.e_override = code == 'M83'
        elif code in ('G20', 'G21'):
            # Feedrate remains in its earlier units in firmware until reset.
            # Require a fresh F observation instead of guessing the conversion.
            new_units = 'mm' if code == 'G21' else 'inch'
            if self.units != new_units:
                self.feedrate = None
            self.units = new_units
        elif code == 'T0':
            self.tool = 0
        elif code in ('M220', 'M221'):
            # Pinned firmware consumes an int16 percentage; reject truncation
            # and unsupported tools instead of tracking a different value.
            value = params.get('S')
            if value is None or value != int(value) or not 1 <= value <= 300 or params.get('T', 0) != 0:
                self.uncertainty.add('unsupported_override')
            elif code == 'M220':
                self.feedrate_percent = int(value)
            else:
                self.flow_percent = int(value)
        elif code in ('G0', 'G1', 'G2', 'G3') and 'F' in params:
            if params['F'] <= 0:
                self.uncertainty.add('invalid_feedrate')
            else:
                self.feedrate = params['F']
        elif code in ('M106', 'M107'):
            value = params.get('S', 255) if code == 'M106' else 0
            if params.get('P', 0) != 0 or not 0 <= value <= 255:
                self.uncertainty.add('unsupported_fan')
            else:
                self.fan_pwm = value
        elif code in ('M104', 'M109', 'M140', 'M190'):
            targets = set(params) & {'S', 'R'}
            if len(targets) != 1 or params.get('T', 0) != 0:
                self.uncertainty.add('ambiguous_temperature_target')
                return
            value = params[next(iter(targets))]
            if value < 0:
                self.uncertainty.add('invalid_temperature_target')
            elif code in ('M104', 'M109'):
                self.nozzle_target = value
            else:
                self.bed_target = value

    def mode_restore_commands(self):
        """Restore modes only; never emits movement, extrusion or heating.

        G90/G91 must precede M82/M83 or the pinned firmware clears the E override.
        Caller must separately qualify offsets, position, targets and feedrate.
        """
        if self.uncertainty or self.units is None or self.xyz_relative is None or self.e_relative is None:
            raise ValueError('Modal state is not qualified')
        return [('G21' if self.units == 'mm' else 'G20'),
                ('G91' if self.xyz_relative else 'G90'),
                ('M83' if self.e_relative else 'M82')]
