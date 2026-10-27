# Development printer identification

Owner-supplied About-screen evidence, October 26, 2026. The original photo stays
private; its SHA-256 and transcription are in
[the intake record](../validation/development-about-owner-20261026.json).

| Field visible in the photo | Reported value |
| --- | --- |
| Model | Ender-3 S1 Pro |
| Mainboard firmware | 2.0.8.28F4 |
| Screen firmware | V1.0.5 |
| Hardware label | CR-FDM-v24S1_301 |
| Print size | 220 × 220 × 270 mm |

This resolves the requested About values. The `F4` suffix is consistent with the
F401 build family in the older vendor source, and the hardware/firmware strings
match the previously downloaded recovery candidate's name. It does not establish
its authenticity or qualify recovery.

The hardware label is not an independent processor identification. The pinned
[official configuration](https://github.com/CrealityOfficial/Ender-3S1/blob/7fffa9270ff8fb5ee208a5b04f832e20bab19c60/Marlin/Configuration.h)
selects F103 or F401 separately while keeping this hardware-label string fixed.
The [About handler](https://github.com/CrealityOfficial/Ender-3S1/blob/7fffa9270ff8fb5ee208a5b04f832e20bab19c60/Marlin/src/lcd/dwin/lcd_rts.cpp)
sends that configured string to the display. This is evidence about the older
published source; the installed 2.0.8.28F4 binary is not proven byte-identical to it.

## Evidence still needed before commissioning

1. A readable mainboard label and processor marking from the development unit.
   Disconnect that unit from mains and USB before inspecting its electronics.
   An exact chip marking resolves the remaining processor choice; another About
   photo is unnecessary. Leave the protected printer untouched.
2. Current nozzle and bed readings on Home, with heater targets off, plus
   confirmation that the development unit is idle and its bed/movement area is
   clear. The earlier −15°C nozzle report needs diagnosis; this About photo
   contains no temperature reading or mechanical-clearance evidence.

After those facts, qualify the actual driver/assignment, preserve readable
settings and confirm the recovery procedure before any firmware installation.
The current screen version is recorded; a physical display part identification
is still needed if a display update becomes necessary. No display update is
required merely because a recovery archive contains screen assets.

## Logitech work deferred

The owner deferred Logitech work on October 26. Retain the earlier interfaces,
CAD and conditional geometry evidence as deferred work. Camera assignment,
acquisition/control and mount/view qualification do not gate the current printer
work. No camera was opened, installed or qualified by this intake.
