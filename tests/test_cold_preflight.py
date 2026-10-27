import pytest
from printer_control.cold_preflight import ColdJobPolicy,cold_job_plan,INITIALIZATION
from printer_control.journal import RequestRejected

PREFIX=('\n'.join(INITIALIZATION)+'\n').encode()
JOB=PREFIX+b'G1 F600\nG1 X10 Y20 Z5\nG1 X20\nM400\n'


def policy(**changes):
    values=dict(policy_id='software-fixture-only',bounds=dict(x=[0,100],y=[0,100],z=[0,100]),
        max_feedrate_mm_min=600,max_path_mm=1000,max_nominal_seconds=600,max_moves=100,
        temperature_bounds={'T':[5,40],'B':[5,40]})
    values.update(changes);return ColdJobPolicy(**values)


def test_command_geometry_is_bounded_and_not_physical_authority():
    result=cold_job_plan(JOB,policy=policy(),initial_position=dict(x=0,y=0,z=0))
    assert result['moves']==2 and result['final_position']==dict(x=20,y=20,z=5)
    assert result['path_mm']==pytest.approx((10**2+20**2+5**2)**.5+10)
    assert result['nominal_motion_seconds']==pytest.approx(result['path_mm']/10)
    assert not result['heating_or_extrusion'] and not result['physical_readiness_qualified']
    assert not result['clearance_verified'] and not result['elapsed_time_upper_bound']


@pytest.mark.parametrize('command',[b'G1 X10 E0 F600',b'G1 E1 F600',b'M104 S200',b'M109 S0',b'M140 S60',
    b'M190 S0',b'G28',b'G0 X10',b'G91',b'G20',b'G92 E0',b'M84',b'M112',b'M410',b'M876 V1 I Q00000001',
    b'M118 E1 bypass',b'M117 text',b'M500',b'M503',b'M220 S300',b'M221 S100',b'M600',b'M0',b'T1',
    b'G2 X10 I5',b'G4 S100',b'N1 G1 X10*1',b'G1 X10 X20 F600',b'G1 X1 G28',b'G1 X1e2 F600',
    b'G1 Xnan F600',b'G1 X1\tF600',b'G1 X1\x00 F600',b'G1 X1\rF600'])
def test_unsupported_or_effectful_commands_never_form_a_cold_plan(command):
    with pytest.raises(RequestRejected):cold_job_plan(PREFIX+command+b'\n',policy=policy(),initial_position=dict(x=0,y=0,z=0))


@pytest.mark.parametrize('tail',[b'G1 X101 F600',b'G1 Y-1 F600',b'G1 X1 F601',b'G1 X1 F0',b'G1 X1 F-1',b'G1 X1'])
def test_bounds_and_explicit_feed_are_required(tail):
    with pytest.raises(RequestRejected):cold_job_plan(PREFIX+tail+b'\n',policy=policy(),initial_position=dict(x=0,y=0,z=0))


@pytest.mark.parametrize('limits',[dict(max_path_mm=1),dict(max_nominal_seconds=.01),dict(max_moves=1)])
def test_total_plan_limits_are_enforced(limits):
    with pytest.raises(RequestRejected):cold_job_plan(JOB,policy=policy(**limits),initial_position=dict(x=0,y=0,z=0))


def test_initialization_and_initial_commanded_position_are_required():
    with pytest.raises(RequestRejected):cold_job_plan(b'G1 X1 F600\n',policy=policy(),initial_position=dict(x=0,y=0,z=0))
    with pytest.raises(RequestRejected):cold_job_plan(JOB,policy=policy(),initial_position=dict(x=-1,y=0,z=0))
    with pytest.raises(RequestRejected):cold_job_plan(PREFIX,policy=policy(),initial_position=dict(x=0,y=0,z=0))


def test_comments_and_crlf_preserve_original_digest_and_do_not_become_commands():
    data=JOB.replace(b'G1 X20',b'G1 X20 ;M104 S200').replace(b'\n',b'\r\n')
    result=cold_job_plan(data,policy=policy(),initial_position=dict(x=0,y=0,z=0))
    assert result['moves']==2 and result['file_sha256']!=cold_job_plan(JOB,policy=policy(),initial_position=dict(x=0,y=0,z=0))['file_sha256']


@pytest.mark.parametrize('change',[dict(max_feedrate_mm_min=True),dict(max_path_mm=float('inf')),dict(max_nominal_seconds=0),
    dict(max_moves=0),dict(max_moves=10001),dict(bounds={'x':[0,1]}),dict(temperature_bounds={'T':[-20,40],'B':[5,40]})])
def test_policy_requires_finite_explicit_limits(change):
    with pytest.raises(ValueError):policy(**change)


def test_policy_copies_nested_input_and_exposes_only_copies():
    bounds=dict(x=[0,100],y=[0,100],z=[0,100]);p=policy(bounds=bounds);identifier=p.id
    bounds['x'][1]=1000;copy=p.to_dict();copy['bounds']['x'][1]=1000
    assert p.id==identifier and p.to_dict()['bounds']['x'][1]==100
