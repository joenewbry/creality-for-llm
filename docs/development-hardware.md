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
its authenticity or qualify recovery. Separate October 27 [manufacturer-download
research](recovery.md) now establishes package-channel provenance and mirror
payload identity; hardware compatibility and restoration are still unqualified.

The hardware label is not an independent processor identification. The pinned
[official configuration](https://github.com/CrealityOfficial/Ender-3S1/blob/7fffa9270ff8fb5ee208a5b04f832e20bab19c60/Marlin/Configuration.h)
selects F103 or F401 separately while keeping this hardware-label string fixed.
The [About handler](https://github.com/CrealityOfficial/Ender-3S1/blob/7fffa9270ff8fb5ee208a5b04f832e20bab19c60/Marlin/src/lcd/dwin/lcd_rts.cpp)
sends that configured string to the display. This is evidence about the older
published source; the installed 2.0.8.28F4 binary is not proven byte-identical to it.

## Current development-only read-only observations

The owner confirmed the development printer is idle and its movement area clear.
On October 27 UTC, an operator diagnostic revalidated the assigned USB topology
and registry identity, excluded the protected printer, and queried `M115` and
`M105` once each. The installed firmware reports **2.0.8.28F4**, built June 19,
2023. The nozzle reports **−15.00°C**, bed **21.70°C**, and both heater targets
**0°C**. No additional About or Home-screen photo is needed for these facts.

The negative nozzle reading is invalid for this room-temperature commissioning
context. A temperature-sensing fault is suspected; a clogged nozzle alone does
not establish its cause. Keep heating, extrusion and motion disabled while
sensor/wiring diagnosis and readiness qualification remain open. Zero targets
are firmware observations, not independent proof of physical heater shutdown.

The diagnostic used system Python 3.14.6, rather than the qualified installed
Python 3.11 package. Driver exclusivity rejected second opens through both
serial aliases with EBUSY. The first attempt sent no G-code because its owner
parser rejected an FD record; a local-file fixture and four parser tests
validated the correction before a separately recorded second attempt. No
firmware, reset, motion or heater command was sent. Opening/configuring serial
can affect hardware signals, so absence of a reset is not claimed. This is
operator evidence, not qualification of the production physical backend.

[Sanitized observation record](../validation/development-readonly-stage130.json).
Raw identities, serial transcripts and the original photo remain private.

## Hardware inspection and commissioning

The owner prefers to defer mainboard/chip inspection. It is **not a prerequisite
for source work, simulation, documentation or the current read-only diagnostic**.
The F4 version strongly supports the F401 build family, but remains an inference.
Open source describes supported build targets; it does not identify installed
silicon or its bootloader. Before any firmware flash, qualify the specific
candidate, bootloader and compatible recovery procedure. Seek a physical marking
only if remaining uncertainty cannot be resolved sufficiently through other
reliable evidence. Do not open electronics merely to continue host software work.

A historical development-only `M503` settings transcript exists; it is not a
current settings backup or a validated restoration procedure. Manufacturer-channel provenance and all 216 candidate payloads are now checked
separately in [recovery research](recovery.md). Matching archive names and
embedded version strings alone do not authenticate an image or prove restoration.
The screen reports V1.0.5; identify its physical part only if a display update
becomes necessary. No display update is required merely because an archive
contains screen assets. The protected printer remains excluded.

## Logitech work deferred

The owner deferred Logitech work on October 26. Retain the earlier interfaces,
CAD and conditional geometry evidence as deferred work. Camera assignment,
acquisition/control and mount/view qualification do not gate the current printer
work. No camera was opened, installed or qualified by this intake.
