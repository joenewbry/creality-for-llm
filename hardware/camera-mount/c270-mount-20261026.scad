// Creality S1 monitoring mount prototype, 2026-10-26.
// Original source: AGPL-3.0-or-later. Session title unavailable.
// Retains the original webcam clip; no camera housing or printer modifications.
// All dimensions mm. Physical fit, strength and clearance remain unqualified.
part = "assembly"; // clamp, shoe, pad, coupon3, coupon4, coupon5, assembly
$fn = 64;
rod_d = 8.6;
rod_length = 600;
clip_plate_thickness = 4;
clamp_width = 44;
clamp_depth = 64;
clamp_height = 78;
jaw = 14;
spine = 22;

module hex_bore(af,h) { cylinder(d=af/cos(30),h=h,$fn=6); }
module clamp() {
    difference() {
        cube([clamp_width,clamp_depth,clamp_height]);
        translate([-1,-1,jaw]) cube([clamp_width+2,clamp_depth-spine+1,clamp_height-2*jaw]);
        translate([22,19,-1]) cylinder(d=6.6,h=jaw+2);
        translate([22,19,-.1]) hex_bore(10.4,6.2);
        translate([22,53,-1]) cylinder(d=rod_d,h=clamp_height+2);
        translate([22,53,-.1]) hex_bore(13.4,7.2);
    }
}
module shoe() {
    difference() {
        union() {
            translate([-32.5,0,0]) cube([65,clip_plate_thickness,55]);
            translate([0,15,5]) cylinder(d=22,h=14);
            translate([-11,clip_plate_thickness-1,5]) cube([22,16-clip_plate_thickness,14]);
        }
        translate([0,15,4]) cylinder(d=rod_d,h=16);
        for(x=[-24,24]) translate([x-2,-1,23]) cube([4,clip_plate_thickness+2,15]);
    }
}
module pad() {
    difference() {
        cylinder(d=30,h=8);
        translate([0,0,-1]) cylinder(d=6.6,h=6);
    }
}
module coupon(t) {
    // Clip-fit plate only. Print one before the full mount.
    translate([-27.5,-17.5,0]) cube([55,35,t]);
}
module assembly() {
    color([.08,.37,.40]) clamp();
    color([.65,.69,.72]) translate([22,53,-6]) cylinder(d=8,h=rod_length+6);
    color([.08,.37,.40]) translate([22,38,rod_length-20]) shoe();
    color([.65,.69,.72]) translate([22,19,-14]) cylinder(d=6,h=52);
    color([.25,.27,.28]) translate([22,19,34]) pad();
    // Camera/clip are schematic envelopes, not measured replacement geometry.
    color([.12,.13,.14]) translate([22-36.45,26,rod_length+40]) cube([72.91,25,31.91]);
    color([.08,.08,.09]) translate([22-18,32,rod_length+12]) cube([36,24,24]);
}
if(part=="clamp") translate([0,0,clamp_width]) rotate([0,90,0]) clamp();
else if(part=="shoe") translate([32.5,55,0]) rotate([90,0,0]) shoe();
else if(part=="pad") translate([15,15,0]) pad();
else if(part=="coupon3") translate([27.5,17.5,0]) coupon(3);
else if(part=="coupon4") translate([27.5,17.5,0]) coupon(4);
else if(part=="coupon5") translate([27.5,17.5,0]) coupon(5);
else assembly();
