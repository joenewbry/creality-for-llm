import queue
from types import SimpleNamespace

import pytest

from printer_control.journal import RequestRejected
from printer_control.virtual_setup import VirtualSetup


def fixture():
    commands = []
    device = SimpleNamespace(_lastX=0., _lastY=0., _lastZ=0., _unitModifier=1.,
        buffered=queue.Queue(), _killed=False, targetTemp=[0.], bedTargetTemp=0., chamberTargetTemp=0.)
    sender = SimpleNamespace(_serial=device, STATE_OPERATIONAL=1, getState=lambda:1,
        sendCommand=lambda command, **kw: commands.append(command) or True)
    backend = SimpleNamespace(sender=sender, _started=False, _fault=None, clock=lambda:1.)
    return VirtualSetup(backend), device, commands


def test_home_requires_matching_barrier_and_drained_moves():
    setup, device, commands = fixture()
    setup.dispatch('home', 'printer.home', {}, 10.)
    assert commands[:3] == ['G90', 'G28 X Y Z', 'M400']
    setup.observe('echo:LLM_SETUP_stale')
    assert setup.poll('home') is None and setup.position is None
    device.buffered.put('move')
    setup.observe('echo:' + setup.pending['home']['token'])
    assert setup.poll('home') is None and setup.position is None
    device.buffered.get_nowait()
    device.buffered.task_done()
    assert setup.poll('home').status == 'completed'


def test_unconfirmed_position_expires_and_cannot_authorize_later_commands():
    setup, device, commands = fixture()
    setup.dispatch('home', 'printer.home', {}, 10.)
    setup.observe('echo:' + setup.pending['home']['token'])
    device._lastX = 5.
    assert setup.poll('home') is None
    setup.backend.clock = lambda:10.
    assert setup.poll('home').status == 'outcome_unknown'
    device._lastX = 0.
    with pytest.raises(RequestRejected, match='setup_outcome_requires_recovery'):
        setup.validate('printer.home', {})
    assert setup.position is None


def test_partial_dispatch_latches_uncertainty():
    setup, device, commands = fixture()
    def send(command, **kw):
        commands.append(command)
        return len(commands) < 2
    setup.backend.sender.sendCommand = send
    with pytest.raises(OSError):
        setup.dispatch('home', 'printer.home', {}, 10.)
    assert commands == ['G90', 'G28 X Y Z'] and setup.uncertain
    with pytest.raises(RequestRejected):
        setup.validate('printer.home', {})


@pytest.mark.parametrize('change', ['heat', 'inch', 'killed', 'active_job', 'buffered'])
def test_setup_rejects_incompatible_machine_state(change):
    setup, device, commands = fixture()
    if change == 'heat': device.targetTemp = [1.]
    if change == 'inch': device._unitModifier = 1/2.54
    if change == 'killed': device._killed = True
    if change == 'active_job': setup.backend._started = True
    if change == 'buffered': device.buffered.put('pending move')
    with pytest.raises(RequestRejected):
        setup.validate('printer.home', {})
    assert commands == []


@pytest.mark.parametrize('axis,maximum', [('x', 220.), ('y', 220.), ('z', 270.)])
def test_jog_cannot_cross_absolute_envelope(axis, maximum):
    setup, device, commands = fixture()
    setattr(device, '_last' + axis.upper(), maximum)
    setup.position = setup.actual_position()
    with pytest.raises(RequestRejected, match='jog_target_out_of_bounds'):
        setup.validate('printer.jog', dict(axis=axis, distance_mm=.001, feedrate_mm_min=600))
    setup.validate('printer.jog', dict(axis=axis, distance_mm=-1, feedrate_mm_min=600))
    assert commands == []


def test_inspection_does_not_present_changed_coordinates_as_verified_home():
    setup, device, commands = fixture()
    setup.position = setup.actual_position()
    device._lastX = 1.
    assert setup.inspect()['position'] is None
    assert setup.inspect()['position_evidence'] == 'unknown'


@pytest.mark.parametrize('pwm',[0,128,255])
def test_fan_requires_marker_and_independent_state_without_changing_position(pwm):
    setup,device,commands=fixture();device._fan_pwm=64
    setup.position={'x':0.,'y':0.,'z':0.}
    setup.dispatch('fan','printer.set_fan',{'fan':0,'pwm':pwm},10.)
    assert commands[:2]==[f'M106 P0 S{pwm}','M400']
    assert setup.poll('fan') is None
    setup.observe('echo:'+setup.pending['fan']['token'])
    assert setup.poll('fan') is None
    device._fan_pwm=pwm
    outcome=setup.poll('fan')
    assert outcome.status=='completed' and outcome.detail['pwm']==pwm
    assert not outcome.detail['physical_fan_qualified']
    assert setup.position=={'x':0.,'y':0.,'z':0.}


@pytest.mark.parametrize('args',[{'fan':1,'pwm':0},{'fan':False,'pwm':0},
    {'fan':0,'pwm':True},{'fan':0,'pwm':256},{'fan':0,'pwm':-1},
    {'fan':0,'pwm':1.5},{'fan':0,'pwm':0,'raw':'M106'}])
def test_fan_rejects_invalid_arguments_without_writes(args):
    setup,device,commands=fixture();device._fan_pwm=0
    with pytest.raises(RequestRejected):setup.dispatch('fan','printer.set_fan',args,10.)
    assert not commands


def test_missing_fan_state_times_out_and_latches_recovery():
    setup,device,commands=fixture();device._fan_pwm=0
    setup.dispatch('fan','printer.set_fan',{'fan':0,'pwm':128},10.)
    setup.observe('echo:'+setup.pending['fan']['token'])
    setup.backend.clock=lambda:10.
    outcome=setup.poll('fan')
    assert outcome.status=='outcome_unknown' and outcome.detail['reason']=='setup_fan_state_unconfirmed'
    with pytest.raises(RequestRejected):setup.validate('printer.set_fan',{'fan':0,'pwm':0})


@pytest.mark.parametrize('heater,target',[('nozzle',0),('nozzle',250),('bed',0),('bed',90)])
def test_temperature_target_completion_does_not_claim_actual_readiness(heater,target):
    setup,device,commands=fixture();device.temp=[20.];device.bedTemp=20.
    setup.dispatch('temp','printer.set_temperature',{'heater':heater,'target_celsius':target},10.)
    assert commands[0]==f"{'M104' if heater=='nozzle' else 'M140'} S{target}"
    if heater=='nozzle':device.targetTemp[0]=target
    else:device.bedTargetTemp=target
    assert setup.poll('temp') is None
    setup.observe('echo:'+setup.pending['temp']['token'])
    result=setup.poll('temp')
    assert result.status=='completed' and result.detail['actual_celsius']==20
    assert not result.detail['temperature_readiness_verified'] and not result.detail['physical_heating_qualified']


@pytest.mark.parametrize('heater,target',[('nozzle',251),('bed',91),('bed',-1),('chamber',20),
    ('nozzle',True),('bed',1.5)])
def test_invalid_temperature_target_cannot_write(heater,target):
    setup,device,commands=fixture();device.temp=[20.];device.bedTemp=20.
    with pytest.raises(RequestRejected):
        setup.dispatch('temp','printer.set_temperature',{'heater':heater,'target_celsius':target},10.)
    assert not commands


@pytest.mark.parametrize('actual',[-15,float('nan'),float('inf'),None])
def test_unqualified_sensor_blocks_heating_but_allows_zero_target(actual):
    setup,device,commands=fixture();device.temp=[actual];device.bedTemp=20.
    with pytest.raises(RequestRejected,match='sensor_unqualified'):
        setup.validate('printer.set_temperature',{'heater':'nozzle','target_celsius':100})
    setup.validate('printer.set_temperature',{'heater':'nozzle','target_celsius':0})
    assert not commands


def test_wrong_observed_temperature_target_times_out():
    setup,device,commands=fixture();device.temp=[20.];device.bedTemp=20.
    setup.dispatch('temp','printer.set_temperature',{'heater':'nozzle','target_celsius':100},10.)
    setup.observe('echo:'+setup.pending['temp']['token'])
    assert setup.poll('temp') is None
    setup.backend.clock=lambda:10.
    result=setup.poll('temp')
    assert result.status=='outcome_unknown' and result.detail['reason']=='setup_temperature_target_unconfirmed'
