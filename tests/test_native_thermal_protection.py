import json
import subprocess
import pytest
from native_thermal_protection_harness import build_thermal_protection_harness


@pytest.fixture(scope='module')
def executable(tmp_path_factory):
    return build_thermal_protection_harness(tmp_path_factory.mktemp('thermal-protection'))


@pytest.fixture(params=['hotend', 'bed'])
def heater(request):
    return request.param


def run(executable, heater, scenario):
    result = subprocess.run([str(executable), scenario, heater], check=True, capture_output=True, text=True, timeout=5)
    return json.loads(result.stdout)


@pytest.mark.parametrize('scenario', ['deadline', 'wrap', 'repeat_fault'])
def test_runaway_calls_shutdown_and_kill_at_deadline(executable, heater, scenario):
    result = run(executable, heater, scenario)
    assert result['state'] == 3 and result['kills'] == 1
    assert not result['autotemp_enabled']
    assert result['before_kills'] == (1 if scenario == 'repeat_fault' else 0)
    assert result['last_heater'] == (-1 if heater == 'bed' else 0)
    assert result['serial_error'] and result['serial_message'] == 'THERMAL RUNAWAY'
    assert result['watchdogs'] == result['screen_fault_updates'] == (2 if scenario == 'repeat_fault' else 1)
    for key in ('nozzle_target', 'bed_target', 'nozzle_pwm', 'bed_pwm', 'nozzle_pin', 'bed_pin'):
        assert result[key] == 0
    assert result['period_seconds'] == (180 if heater == 'bed' else 60)
    assert result['hysteresis'] == (2 if heater == 'bed' else 4)


@pytest.mark.parametrize('scenario,state', [('hysteresis_edge', 2), ('recovered', 2), ('off', 0),
    ('first_heating', 1), ('target_changed', 1), ('idle_timeout', 0)])
def test_nonfault_paths_and_separate_first_heating_watch(executable, heater, scenario, state):
    result = run(executable, heater, scenario)
    assert result['state'] == state and result['kills'] == 0
    assert not result['serial_error'] and result['watchdogs'] == result['screen_fault_updates'] == 0
    # Fixture calls the state machine directly: target updates / idle heater
    # shutdown outside these extracted functions are deliberately not modeled.
    assert result['nozzle_target'] == 200 and result['bed_target'] == 60
