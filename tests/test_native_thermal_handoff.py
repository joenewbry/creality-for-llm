import json
import subprocess
import pytest
from native_thermal_protection_harness import build_thermal_protection_harness


@pytest.fixture(scope='module')
def executable(tmp_path_factory):
    return build_thermal_protection_harness(tmp_path_factory.mktemp('thermal-handoff'), heating_watch='handoff')


@pytest.mark.parametrize('heater', ['hotend', 'bed'])
@pytest.mark.parametrize('scenario,message', [('stall','HEATING FAILED'), ('drop','THERMAL RUNAWAY'),
    ('above_drop','THERMAL RUNAWAY'), ('boundary_hold',''), ('normal','')])
def test_native_watch_to_runaway_handoff(executable, heater, scenario, message):
    result = json.loads(subprocess.check_output([str(executable), scenario, heater], text=True, timeout=5))
    assert result['serial_message'] == message
    assert result['kills'] == bool(message)
    if message:
        assert result['now'] <= (360000 if heater == 'bed' else 100000)
        assert result['nozzle_target'] == result['bed_target'] == result['nozzle_pin'] == result['bed_pin'] == 0
    else:
        assert result['state'] == 2 and result['now'] > 600000
    if scenario in ('drop', 'above_drop', 'boundary_hold'):
        assert result['before_drop_state'] == 2 and not result['before_drop_watch']
