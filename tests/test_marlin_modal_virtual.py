import json
import queue
import subprocess
from itertools import product

import pytest

from marlin_modal_virtual import MarlinModalMixin
from test_native_restore import native_restore  # shared compiled extracted handlers


class BaseFixture:
    def __init__(self):
        self.buffered = queue.Queue()
        self._relative = False
        self.currentExtruder, self._unitModifier = 0, 1
        self._lastX, self._lastY, self._lastZ, self._lastE = 10.,20.,.2,[3.]
        self._feedrate_multiplier, self._flowrate_multiplier = 100.,100.
        self.completed_moves = []


class Fixture(MarlinModalMixin, BaseFixture):
    pass


def execute(device, commands):
    for command in commands:
        getattr(device, '_gcode_' + command.split()[0])(command)
    while not device.buffered.empty():
        device._performMove(device.buffered.get_nowait())
        device.buffered.task_done()


@pytest.mark.parametrize('xyz,e', list(product((False, True), repeat=2)))
def test_separate_modes_and_position_match_native_handlers(native_restore, xyz, e):
    commands = ['G91' if xyz else 'G90', 'M83' if e else 'M82',
                'M106 P0 S128', 'G1 X12 Y21 Z0.5 E2 F1200.25']
    expected = json.loads(subprocess.check_output([str(native_restore), *commands], text=True))
    device = Fixture()
    execute(device, commands)
    assert device._relative == expected['xyz_relative']
    assert device.e_relative == expected['e_relative']
    assert device._fan_pwm == expected['fan_pwm']
    assert [device._lastX,device._lastY,device._lastZ,device._lastE[0]] == pytest.approx(expected['position'])
    assert device._executed_feedrate_mm_s == pytest.approx(expected['feedrate_mm_s'])


def test_queued_moves_keep_modes_and_feedrate_from_queue_time():
    device = Fixture()
    execute(device, ['G90','M83','G1 X12 E2 F600','G91','M82','G1 X1 E9 F1200','G90'])
    assert device._lastX == 13 and device._lastE[0] == 9
    assert [(m.xyz_relative,m.e_relative,m.feedrate_mm_s) for m in device.modal_moves] == [(False,True,10),(True,False,20)]
    assert not device.e_relative  # final G90 cleared the M82/M83 override
    assert len(device.completed_moves) == 2


def test_feed_only_restore_and_fan_off_do_not_move_coordinates():
    device = Fixture()
    execute(device, ['G91','M83','M106 S255','M107','G1 F1200'])
    assert [device._lastX,device._lastY,device._lastZ,device._lastE[0]] == [10,20,.2,3]
    assert device._fan_pwm == 0


@pytest.mark.parametrize('command', ['G2 X1','G1 Xnan F100','G1 F0','G1 X1 X2 F100',
    'M106 P1 S100','M106 S1.5','M82 X1'])
def test_unsupported_simulation_forms_fail_explicitly(command):
    device = Fixture()
    with pytest.raises(ValueError):
        execute(device, [command])
    assert not device.completed_moves
