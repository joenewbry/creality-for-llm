"""Bounded nonprinting command-space plans. No physical readiness authority.

This complements a separately qualified hardware/configuration/continuity
provider. It does not establish physical position, clearance or actual duration.
"""
from dataclasses import dataclass
import hashlib
import json
import math
import re
from .journal import RequestRejected

AXES=('x','y','z')
INITIALIZATION=('G21','G90','M82','T0','M106 S0','M104 S0','M140 S0')
NUMBER=re.compile(r'([XYZF])([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))')


def finite(value):return type(value) in (int,float) and math.isfinite(value)


@dataclass(frozen=True,init=False)
class ColdJobPolicy:
    _encoded: str
    id: str

    def __init__(self,*,policy_id,bounds,max_feedrate_mm_min,max_path_mm,max_nominal_seconds,max_moves,temperature_bounds):
        if type(policy_id) is not str or not policy_id.strip() or not 1<=len(policy_id)<=128:raise ValueError('bounded cold policy identity required')
        if type(bounds) is not dict or bounds.keys()!=set(AXES):raise ValueError('explicit XYZ command bounds required')
        if type(temperature_bounds) is not dict or temperature_bounds.keys()!={'T','B'}:raise ValueError('explicit reported temperature bounds required')
        for limits in [*bounds.values(),*temperature_bounds.values()]:
            if type(limits) not in (tuple,list) or len(limits)!=2 or not all(finite(v) for v in limits) or not limits[0]<limits[1]:
                raise ValueError('finite ordered limits required')
        if any(not 0<=limits[0]<limits[1]<=60 for limits in temperature_bounds.values()):
            raise ValueError('cold reported temperature limits required')
        if any(not finite(v) or v<=0 for v in (max_feedrate_mm_min,max_path_mm,max_nominal_seconds)):
            raise ValueError('positive finite command limits required')
        if type(max_moves) is not int or not 1<=max_moves<=10000:raise ValueError('bounded move count required')
        value=dict(schema_version=1,policy_id=policy_id,bounds=bounds,max_feedrate_mm_min=max_feedrate_mm_min,
            max_path_mm=max_path_mm,max_nominal_seconds=max_nominal_seconds,max_moves=max_moves,temperature_bounds=temperature_bounds)
        encoded=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
        object.__setattr__(self,'_encoded',encoded);object.__setattr__(self,'id',hashlib.sha256(encoded.encode()).hexdigest())

    def to_dict(self):return json.loads(self._encoded)


def cold_job_plan(data,*,policy,initial_position):
    """Strict absolute XYZ G1 profile; no E axis, heating, homing or raw waits.

    Straight-line geometry and nominal feed timing are commanded estimates.
    They are not physical clearance checks or upper bounds on elapsed time.
    """
    if type(policy) is not ColdJobPolicy:raise TypeError('explicit cold command policy required')
    if type(data) is not bytes or not 0<len(data)<=1024*1024:raise RequestRejected('cold_job_size_out_of_range')
    normalized=data.replace(b'\r\n',b'\n')
    if any(b!=10 and not 32<=b<=126 for b in normalized):raise RequestRejected('cold_job_requires_plain_ascii_lines')
    if type(initial_position) is not dict or initial_position.keys()!=set(AXES) or not all(finite(v) for v in initial_position.values()):
        raise RequestRejected('cold_initial_commanded_position_required')
    limits=policy.to_dict();position=dict(initial_position)
    def inside(point):return all(limits['bounds'][a][0]<=point[a]<=limits['bounds'][a][1] for a in AXES)
    if not inside(position):raise RequestRejected('cold_initial_position_outside_command_bounds')
    lines=normalized.decode('ascii').splitlines()
    if len(lines)>50000 or any(len(line)>256 for line in lines):raise RequestRejected('cold_job_line_limits_exceeded')
    initialization=0;feed=None;path=seconds=0.;moves=0
    for number,raw in enumerate(lines,1):
        line=' '.join(raw.split(';',1)[0].split())
        if not line:continue
        if initialization<len(INITIALIZATION):
            if line!=INITIALIZATION[initialization]:raise RequestRejected(f'cold_initialization_required_at_line_{number}')
            initialization+=1;continue
        if line=='M400':continue
        if not line.startswith('G1 '):raise RequestRejected(f'cold_command_unsupported_at_line_{number}')
        tail=line[3:];parameters={}
        while tail:
            match=NUMBER.match(tail)
            if match is None or match[1] in parameters:raise RequestRejected(f'cold_parameters_unsupported_at_line_{number}')
            value=float(match[2])
            if not finite(value):raise RequestRejected(f'cold_nonfinite_parameter_at_line_{number}')
            parameters[match[1]]=value;tail=tail[match.end():].lstrip()
        if not parameters:raise RequestRejected(f'cold_empty_move_at_line_{number}')
        if 'F' in parameters:
            feed=parameters['F']
            if not 0<feed<=limits['max_feedrate_mm_min']:raise RequestRejected(f'cold_feedrate_out_of_range_at_line_{number}')
        coordinates={a:parameters[a.upper()] for a in AXES if a.upper() in parameters}
        if not coordinates:continue
        if feed is None:raise RequestRejected(f'cold_explicit_feedrate_required_at_line_{number}')
        target={**position,**coordinates}
        if not inside(target):raise RequestRejected(f'cold_target_outside_command_bounds_at_line_{number}')
        distance=math.dist(tuple(position[a] for a in AXES),tuple(target[a] for a in AXES))
        path+=distance;seconds+=distance*60/feed;moves+=1
        if (not finite(path) or path>limits['max_path_mm'] or not finite(seconds)
                or seconds>limits['max_nominal_seconds'] or moves>limits['max_moves']):
            raise RequestRejected(f'cold_plan_limits_exceeded_at_line_{number}')
        position=target
    if initialization!=len(INITIALIZATION) or not moves:raise RequestRejected('cold_complete_initialization_and_motion_required')
    return dict(scope='cold_command_space_plan',file_sha256=hashlib.sha256(data).hexdigest(),policy_sha256=policy.id,
        moves=moves,path_mm=path,nominal_motion_seconds=seconds,initial_position=dict(initial_position),final_position=position,
        heating_or_extrusion=False,physical_readiness_qualified=False,clearance_verified=False,elapsed_time_upper_bound=False)
