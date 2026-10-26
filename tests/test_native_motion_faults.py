import json
import subprocess
import pytest
from native_motion_fault_harness import build_motion_fault_harness


@pytest.fixture(scope='module')
def binary(tmp_path_factory):
    return build_motion_fault_harness(tmp_path_factory.mktemp('motion-faults'))


def run(binary, scenario):
    return json.loads(subprocess.run([str(binary), scenario], check=True,
                     capture_output=True, text=True, timeout=5).stdout)


def test_missing_homing_trigger_calls_terminal_kill_and_sets_screen_fault(binary):
    result = run(binary, 'home_miss')
    assert result['kills'] == 1 and not result['returned']
    assert result['hits'] == result['waitway'] == 0
    assert result['errorway'] == 2 and result['kill_message'] == 'Homing failed'


def test_homing_trigger_clears_hit_without_fault(binary):
    result = run(binary, 'home_hit')
    assert result['returned'] and result['hits'] == 1
    assert result['kills'] == result['errorway'] == 0
    assert result['waitway'] == 7


@pytest.mark.parametrize('scenario,probes,stows', [
    ('deploy_error', 0, 1), ('measurement_error', 1, 1), ('stow_error', 1, 2)])
def test_probe_errors_report_invalid_measurement_and_screen_fault_without_kill(binary, scenario, probes, stows):
    result = run(binary, scenario)
    assert result['returned'] and result['invalid_measurement']
    assert result['kills'] == 0
    assert result['waitway'] == 0 and result['errorway'] == 3
    assert result['page'] == 41 and result['screen_calls'] == 2
    assert result['serial_message'] == result['lcd_message'] == 'Probing failed'
    assert result['probes'] == probes and result['stows'] == stows
    assert result['moves'] == result['deploys'] == 1


def test_unreachable_point_returns_invalid_without_motion_or_fault_message(binary):
    result = run(binary, 'unreachable')
    assert result['returned'] and result['invalid_measurement']
    for key in ('moves', 'deploys', 'probes', 'stows', 'kills', 'errorway', 'screen_calls'):
        assert result[key] == 0
    assert result['serial_message'] == result['lcd_message'] == ''


@pytest.mark.parametrize('scenario,resets', [('success', 0), ('triggered_success', 1)])
def test_valid_probe_and_initial_trigger_reset_do_not_report_fault(binary, scenario, resets):
    result = run(binary, scenario)
    assert result['returned'] and not result['invalid_measurement']
    assert result['resets'] == resets
    assert result['moves'] == result['deploys'] == result['probes'] == result['stows'] == 1
    assert result['kills'] == result['errorway'] == result['screen_calls'] == 0
