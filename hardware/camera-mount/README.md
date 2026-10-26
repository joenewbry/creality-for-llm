# Logitech C270 overview mount for the S1 Pro

Reviewable prototype, October 26, 2026. Source session title unavailable.
The hardware records identify a C270 on the shared bench; confirmation that it
is the intended camera is pending. No Logitech camera is currently enumerated
on the controller Mac. This design has not been printed or installed.

Use the original camera clip on a printed monitor-like shoe, on a 600 mm M8
threaded mast and an independent bench-edge clamp. Yaw and height are adjusted
with the mast nuts; pitch uses the camera's own hinge. The frame and bed carry
no added camera mass. No housing modification or printer screws are required.

The C270's narrow nominal view makes a close front-corner mount unsuitable for
one view of the entire moving bed and full-height print. The editable viewing
model includes bed motion, not just a stationary 220 mm square. For small parts
up to 100 mm high, begin about 0.89 m from the target. For the entire 270 mm print
height, the modeled overview is about 1.13 m away. These are lens-to-target
distances, not a prescribed cantilever length: place the bench clamp there.

## Files and parts

- `c270-mount-20261026.scad`: editable CAD; select the `part` parameter.
- `clamp.stl`: bench clamp, printed on its side, 78 × 64 × 44 mm envelope.
- `shoe.stl`: original-clip support, 65 × 55 × 26 mm envelope.
- `pad.stl`: pressure pad for the clamping screw, 30 × 30 × 8 mm.
- `clip-fit-4mm.stl`: print this plate first and try the original clip. The source
  also offers 3 mm and 5 mm coupon/plate variants. Do not assume the fit.
- `verify_design.py` / `verification.json`: mesh and conditional viewing checks.

Hardware: one 600 mm M8 threaded rod; four M8 nuts and washers (two for the base,
two for the head); one M6 × 60 screw; one captive M6 nut; thin cork/rubber jaw
pads; a small clip tether/strap. Stock cable length is 1.5 m: measure an external
route with slack and strain relief. An extension may be needed. Keep the cable
outside bed/gantry travel and preserve existing USB hub/service ownership.
The desk jaw opening is 50 mm; the proposed padded working range is 15–45 mm.
Desk fit, clamping strength, vibration and material creep require an actual test.
PETG is a prototype material choice, not a validated structural specification.

## What is verified

All four meshes are connected, have two incident triangles on every edge, positive
volume, and fit the nominal 220 × 220 × 270 mm build envelope in the supplied
orientation. The pinhole model projects all corners of the swept print envelope
and assumed 235 mm physical plate inside a nominal 55° diagonal, 16:9 frame,
with at least 10% of a half-frame as margin. A separate assumed machine keep-out
box has no intersection with the proposed external mounting positions.

These checks do **not** verify physical printer clearance, clip fit, actual
framing/focus, occlusion or strength. `verification.json` keeps those flags false.
No camera has observed a print, and no mesh has been sent to either printer.

## Installation and framing checks still required

1. Confirm the webcam model, clip-plate fit, desk thickness and available space.
2. Measure the actual printer/bed sweep and bed-plane height. Check clamp, mast,
   camera and cable throughout the permitted cold motion envelope after printer
   commissioning. Record the smallest clearance; retain at least the chosen
   20 mm design clearance outside the actual swept machine envelope.
3. Frame corner markers with the bed at its qualified near/far positions and a
   height reference for the intended tallest part. Record images, lens position,
   crop/resolution and the mount revision. Check focus, lighting and vibrations.
4. Verify an unmistakable development-printer landmark before assigning the
   camera. Do not take over an existing camera service or confuse the two printers.
5. Use the overview for gross progress, detachment or spaghetti suspicions.
   Fine first-layer/extrusion defects and occluded regions may require a second
   close-up view. Camera output never proves heater shutdown or motion completion.

## Rebuild

```sh
openscad -o clamp.stl -D 'part="clamp"' c270-mount-20261026.scad
openscad -o shoe.stl -D 'part="shoe"' c270-mount-20261026.scad
openscad -o pad.stl -D 'part="pad"' c270-mount-20261026.scad
openscad -o clip-fit-4mm.stl -D 'part="coupon4"' c270-mount-20261026.scad
python3 verify_design.py
```

Sources: [Logitech setup guide](https://www.logitech.com/assets/46735/4/hd-webcam-c270.pdf)
(fixed focus, original clip, dimensions, cable); [Logitech specifications](https://www.logitech.com/en-us/shop/p/c270-hd-webcam.960-000694)
(nominal diagonal view); [Creality build envelope](https://store.creality.com/ca/blogs/all/ender-series-3d-printers-parameter-comparison).
The mechanical geometry is original AGPL-3.0-or-later source; existing online
S1/C270 models were located for comparison but their geometry was not copied.
