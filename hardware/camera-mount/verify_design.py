"""Verify prototype meshes and a conditional pinhole-view/keep-out model.

This is not an actual-printer fit, optical calibration or structural test.
"""
from pathlib import Path
from collections import Counter, defaultdict
import itertools, json, math, re, hashlib
ROOT=Path(__file__).resolve().parent

def dot(a,b): return sum(x*y for x,y in zip(a,b))
def cross(a,b): return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def unit(v):
    n=math.sqrt(dot(v,v));return tuple(x/n for x in v)
def mesh(path):
    raw=path.read_text()
    vertices=[tuple(round(float(x),6) for x in value.split()) for value in re.findall(r'vertex\s+([^\n]+)',raw)]
    assert len(vertices)%3==0 and vertices
    edges=Counter();neighbors=defaultdict(set);volume=0
    for i in range(0,len(vertices),3):
        a,b,c=vertices[i:i+3];assert len({a,b,c})==3
        volume+=dot(a,cross(b,c))/6
        for p,q in [(a,b),(b,c),(c,a)]:
            edges[tuple(sorted((p,q)))]+=1;neighbors[p].add(q);neighbors[q].add(p)
    assert set(edges.values())=={2}, 'nonmanifold edge'
    pending=[vertices[0]];seen=set()
    while pending:
        v=pending.pop()
        if v not in seen:seen.add(v);pending.extend(neighbors[v]-seen)
    assert len(seen)==len(neighbors), 'disconnected solids'
    bounds=[[min(p[i] for p in vertices),max(p[i] for p in vertices)] for i in range(3)]
    assert all(a>=-1e-6 for a,b in bounds), "part extends below build coordinates"
    sizes=[b-a for a,b in bounds]
    assert all(x<=limit for x,limit in zip(sizes,[220,220,270]))
    assert volume>0
    return dict(triangles=len(vertices)//3,watertight_edges=True,connected=True,positive_volume_mm3=volume,size_mm=sizes,sha256=hashlib.sha256(path.read_bytes()).hexdigest())

diagonal=math.radians(55);aspect=(16,9)
hfov=2*math.atan(math.tan(diagonal/2)*16/math.hypot(*aspect))
vfov=2*math.atan(math.tan(diagonal/2)*9/math.hypot(*aspect))
def view(camera,target,height):
    forward=unit(tuple(t-c for t,c in zip(target,camera)));right=unit(cross(forward,(0,0,1)));up=cross(right,forward)
    points=list(itertools.product([-110,110],[-220,220],[0,height]))
    points+=list(itertools.product([-117.5,117.5],[-227.5,227.5],[0]))
    projected=[]
    for p in points:
        v=tuple(a-b for a,b in zip(p,camera));depth=dot(v,forward);assert depth>0
        projected.append([dot(v,right)/(depth*math.tan(hfov/2)),dot(v,up)/(depth*math.tan(vfov/2))])
    maximum=max(abs(c) for p in projected for c in p)
    assert maximum<=.9, 'less than 10 percent half-frame margin'
    # Camera/shoe conservative horizontal box: +/-45 x +/-50 mm.
    # Reference machine keep-out +/-280 x +/-350 mm, not an actual scan.
    clearance_x=-280-(camera[0]+45);clearance_y=-350-(camera[1]+50)
    assert clearance_x>20 and clearance_y>20
    return dict(camera_mm=camera,look_at_mm=target,print_height_mm=height,lens_to_target_mm=math.dist(camera,target),maximum_normalized_frame_coordinate=maximum,half_frame_margin_fraction=1-maximum,all_12_corners_inside_nominal_frame=True,reference_keepout_gap_x_mm=clearance_x,reference_keepout_gap_y_mm=clearance_y)

result=dict(schema_version=1,model='55 degree diagonal pinhole at 16:9; uncalibrated nominal optics',horizontal_fov_deg=math.degrees(hfov),vertical_fov_deg=math.degrees(vfov),bed_motion_mm=[-110,110],build_volume_mm=[220,220,270],physical_plate_assumption_mm=[235,235],machine_keepout_assumption_mm=[[-280,280],[-350,350]],presets=dict(full_height=view((-650,-850,500),(0,0,135),270),low_parts=view((-480,-680,380),(0,0,60),100)),meshes={n:mesh(ROOT/n) for n in ['clamp.stl','shoe.stl','pad.stl','clip-fit-4mm.stl']},physical_clearance_verified=False,physical_visibility_verified=False,structural_or_print_fit_verified=False,limitations=['nominal optics; actual focus/FOV/crop must be checked','gantry, extruder, cabling and printed object can occlude the view','desk thickness, camera clip and rod/cable route not measured','reference keep-out box is a conservative design assumption, not a scanned printer'])
(ROOT/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'meshes':len(result['meshes']),'conditional_view_presets':len(result['presets']),'physical_fit_verified':False}))
