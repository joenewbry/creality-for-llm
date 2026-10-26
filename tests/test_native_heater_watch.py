import json
import subprocess
import pytest
from native_thermal_protection_harness import build_thermal_protection_harness


@pytest.fixture(scope='module')
def executable(tmp_path_factory):
    return build_thermal_protection_harness(tmp_path_factory.mktemp('heater-watch'), heating_watch=True)


@pytest.fixture(params=['hotend', 'bed'])
def heater(request):
    return request.param


def run(executable, heater, scenario):
    return json.loads(subprocess.check_output([str(executable), scenario, heater], text=True, timeout=5))


@pytest.mark.parametrize('scenario', ['no_rise', 'wrap', 'zero_deadline', 'insufficient', 'later_stall', 'repeat'])
def test_initial_heating_failure_dispatches_native_shutdown(executable, heater, scenario):
    result = run(executable, heater, scenario)
    assert result['kills'] == 1 and result['before_kills'] == (1 if scenario == 'repeat' else 0)
    assert result['period_seconds'] == (180 if heater == 'bed' else 40)
    assert result['last_heater'] == (-1 if heater == 'bed' else 0)
    assert result['serial_message'] == 'HEATING FAILED'
    assert result['watchdogs'] == result['screen_fault_updates'] == (2 if scenario == 'repeat' else 1)
    assert not result['autotemp_enabled']
    for key in ('nozzle_target', 'bed_target', 'nozzle_pwm', 'bed_pwm', 'nozzle_pin', 'bed_pin'):
        assert result[key] == 0


@pytest.mark.parametrize('scenario', ['enough_rise', 'near_target', 'off'])
def test_watch_restarts_or_disarms_without_false_failure(executable, heater, scenario):
    result = run(executable, heater, scenario)
    assert result['kills'] == result['watchdogs'] == result['screen_fault_updates'] == 0
    if scenario == 'enough_rise':
        assert result['next_ms'] == result['now'] + result['period_seconds'] * 1000
        assert result['watch_target'] == 24
    else:
        assert result['next_ms'] == 0
