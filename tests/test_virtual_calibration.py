import json
from types import SimpleNamespace
import pytest
from printer_control.virtual_calibration import VirtualCalibration,digest
from printer_control.journal import Journal,RequestRejected
from printer_control.service import Context
from test_virtual_setup import fixture


def calibration(tmp_path):
    setup,device,commands=fixture()
    defaults={'steps':{'params':{'X':80.,'Y':80.,'Z':800.,'E':90.}},'other':{'retained':True}}
    path=tmp_path/'settings.json';path.write_text(json.dumps(defaults))
    eeprom=SimpleNamespace(eeprom=json.loads(json.dumps(defaults)),_eeprom_file_path=path,
        get_default_settings=lambda:json.loads(json.dumps(defaults)))
    device._virtual_eeprom=eeprom
    backend=setup.backend;backend.setup=setup
    backend.context=lambda:Context('test','generation',1,1.)
    return VirtualCalibration(backend),commands,path


def temporary(cal,value=84.):
    args={'axis':'x','steps_per_mm':value,'expected_settings_sha256':digest(cal.current())}
    cal.dispatch('temporary','calibration.set_temporary',args,10.)
    cal.eeprom.eeprom['steps']['params']['X']=value
    assert cal.poll('temporary') is None
    cal.observe('echo:'+cal.pending['temporary']['marker'])
    assert cal.poll('temporary').status=='completed'


def test_temporary_and_persist_have_distinct_ram_and_disk_proofs(tmp_path):
    cal,commands,path=calibration(tmp_path)
    original=path.read_bytes();temporary(cal)
    assert commands[0]=='M92 X84.000000' and 'M500' not in commands
    assert path.read_bytes()==original and cal.inspect()['temporary_changes']
    args={'expected_settings_sha256':digest(cal.current())}
    cal.dispatch('persist','calibration.persist',args,10.)
    assert commands[-3]=='M500'
    cal.observe('echo:'+cal.pending['persist']['marker'])
    assert cal.poll('persist') is None  # marker cannot substitute for saved settings
    path.write_text(json.dumps(cal.current()))
    assert cal.poll('persist').status=='completed'
    # Independent reconstructed model sees the saved values, not just old RAM.
    cal.eeprom.eeprom=json.loads(path.read_text())
    restarted=VirtualCalibration(cal.backend)
    assert restarted.inspect()['saved_steps_per_mm']['X']==84.
    assert restarted.inspect()['allowed_steps_per_mm']['X']==[72.,88.]
    assert restarted.baseline['X']==80.  # bounds cannot grow with each save/restart
    assert not restarted.inspect()['temporary_changes']


@pytest.mark.parametrize('value',[0,71.,89.,True,float('nan'),84.0000001])
def test_out_of_profile_calibration_cannot_emit_commands(tmp_path,value):
    cal,commands,path=calibration(tmp_path)
    with pytest.raises(RequestRejected):
        cal.dispatch('x','calibration.set_temporary',{'axis':'x','steps_per_mm':value,
            'expected_settings_sha256':digest(cal.current())},10.)
    assert not commands


@pytest.mark.parametrize('change',['ram','disk','hash'])
def test_unreviewed_settings_cannot_be_saved(tmp_path,change):
    cal,commands,path=calibration(tmp_path)
    if change=='ram':cal.eeprom.eeprom['other']['retained']=False
    if change=='disk':path.write_text('{}')
    expected='0'*64 if change=='hash' else digest(cal.current())
    with pytest.raises(RequestRejected):cal.dispatch('save','calibration.persist',{'expected_settings_sha256':expected},10.)
    assert not commands


def test_ambiguous_save_times_out_and_blocks_later_setup(tmp_path):
    cal,commands,path=calibration(tmp_path);temporary(cal)
    cal.dispatch('save','calibration.persist',{'expected_settings_sha256':digest(cal.current())},10.)
    cal.observe('echo:'+cal.pending['save']['marker'])
    cal.backend.clock=lambda:10.
    assert cal.poll('save').status=='outcome_unknown'
    assert cal.uncertain and cal.backend.setup.uncertain
    with pytest.raises(RequestRejected):cal.validate('calibration.persist',{'expected_settings_sha256':digest(cal.current())})


def test_restart_requires_recovery_for_unfinished_calibration(tmp_path):
    cal,commands,path=calibration(tmp_path)
    journal=Journal(tmp_path/'journal.sqlite')
    request=dict(request_id='persist',printer_id='test',connection_generation='generation',
        expected_state_version=1,deadline=10.,operation='calibration.persist',
        arguments={'expected_settings_sha256':digest(cal.current())})
    receipt=journal.accept(request,now=1.,printer_id='test',generation='generation',state_version=1)
    journal.claim(receipt['operation_id'],now=1.)
    journal.recover_after_restart(now=2.)
    cal.attach(SimpleNamespace(journal=journal))
    assert cal.uncertain and cal.backend.setup.uncertain and not commands


@pytest.mark.parametrize('failure',['missing','corrupt'])
def test_unreadable_saved_settings_still_have_a_bounded_unknown_outcome(tmp_path,failure):
    cal,commands,path=calibration(tmp_path);temporary(cal)
    cal.dispatch('save','calibration.persist',{'expected_settings_sha256':digest(cal.current())},10.)
    cal.observe('echo:'+cal.pending['save']['marker'])
    if failure=='missing':path.unlink()
    else:path.write_text('{unfinished')
    assert cal.poll('save') is None
    cal.backend.clock=lambda:10.
    assert cal.poll('save').status=='outcome_unknown'
    assert cal.uncertain and cal.backend.setup.uncertain
