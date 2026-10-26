import json
from itertools import product
import shutil
import subprocess

import pytest

from native_restore_harness import ROOT, build_restore_harness
from printer_control.checkpoint import PauseCheckpoint
from printer_control.pause_restore import InPlaceRestorePlan
from test_checkpoint import data


@pytest.fixture(scope='module')
def native_restore(tmp_path_factory):
    if not (ROOT / 'firmware/Marlin/src/gcode/gcode.cpp').exists() or not shutil.which('clang++'):
        pytest.skip('Pinned firmware source and clang++ required')
    return build_restore_harness(tmp_path_factory.mktemp('native-restore'))


def run(binary, commands):
    return json.loads(subprocess.check_output([str(binary), *commands], text=True))


@pytest.mark.parametrize('xyz,e', list(product((False, True), repeat=2)))
@pytest.mark.parametrize('fan,feed,flow,rate', [(0,1,1,1), (128,95,105,1200.25), (255,300,300,60000)])
def test_emitted_restore_sequence_matches_extracted_handlers_without_coordinate_motion(native_restore,xyz,e,fan,feed,flow,rate):
    original = data()
    original['modal'].update(xyz_relative=xyz,e_relative=e,fan_pwm=fan,
                             feedrate_percent=feed,flow_percent=flow,feedrate=rate)
    saved = PauseCheckpoint(original)
    current = data()
    current['created_at'] = 20
    plan = InPlaceRestorePlan(saved, PauseCheckpoint(current), now=20, max_observation_age=1)
    result = run(native_restore, plan.commands)
    assert result['xyz_relative'] == xyz
    assert result['e_relative'] == e
    assert result['fan_pwm'] == fan
    assert result['feedrate_percent'] == feed
    assert result['flow_percent'] == flow
    assert result['e_factor'] == pytest.approx(flow/100)
    assert result['feedrate_mm_s'] == pytest.approx(rate/60)
    assert result['position'] == pytest.approx([10,20,.2,3])
    assert result['prepare_calls'] == 1
    assert result['moved_calls'] == 0


def test_motion_observer_detects_coordinate_change_and_stopped_handler_does_not_apply_feedrate(native_restore):
    moved = run(native_restore, ['G90', 'G1 X11 F1200'])
    assert moved['moved_calls'] == 1 and moved['position'][0] == 11
    stopped = run(native_restore, ['STOPPED', 'G1 F1200'])
    assert stopped['prepare_calls'] == 0
    assert stopped['feedrate_mm_s'] == 5
