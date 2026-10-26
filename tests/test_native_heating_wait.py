import json
import subprocess
import pytest
from native_heating_harness import build_heating_harness


@pytest.fixture(scope='module',params=['hotend','bed'])
def binary(tmp_path_factory,request):
    return build_heating_harness(tmp_path_factory.mktemp('native-heating')),request.param


def run(binary,scenario):
    executable,heater=binary
    result=subprocess.run([str(executable),scenario,heater],capture_output=True,text=True,check=True,timeout=5)
    return json.loads(result.stdout)


def test_wait_uses_configured_residency_and_observability(binary):
    result=run(binary,'heat_ready')
    assert result['returned'] and result['actual']==result['target']==(60 if binary[1]=='bed' else 200)
    assert result['loops']>=13 and result['reports']>=13
    assert result['stepper_resets']==result['loops'] and not result['wait_active']
    assert result['screen_ready_updates']==(0 if binary[1]=='bed' else 1)
    assert result['ui_status_resets']==1


@pytest.mark.parametrize('scenario',['m108','sd_abort'])
def test_interrupted_wait_does_not_claim_ready_or_disable_target(binary,scenario):
    result=run(binary,scenario)
    assert not result['returned'] and result['loops']==3
    assert result['actual']==20 and result['target']==(60 if binary[1]=='bed' else 200)
    assert result['screen_ready_updates']==result['ui_status_resets']==0 and not result['wait_active']


def test_token_mode_does_not_accept_legacy_m108(binary):
    result=run(binary,'token_m108')
    assert not result['returned'] and result['loops']==5  # SD abort, not M108 at loop3
    assert result['target']==(60 if binary[1]=='bed' else 200)


@pytest.mark.parametrize('scenario,loops',[('cool_skip',0),('cool_stalled',61),('target_lowered',3)])
def test_wait_return_true_is_not_proof_of_target_temperature(binary,scenario,loops):
    result=run(binary,scenario)
    assert result['returned'] and result['loops']==loops
    assert result['actual']!=result['target']
    assert result['screen_ready_updates']==(0 if binary[1]=='bed' else 1)
    assert result['ui_status_resets']==1 and not result['wait_active']


def test_temperature_excursion_restarts_residency(binary):
    result=run(binary,'heat_hysteresis')
    assert result['returned'] and result['actual']==result['target']
    assert result['loops']>=17 and result['reports']==result['loops']
    assert result['stepper_resets']==result['loops']
