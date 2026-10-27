# Stock .28 and the .24 protocol candidate

October 27, 2026. Offline comparison only; neither printer was accessed.

**The current unflashed candidate is not a stock .28-equivalent upgrade.** It
adds the tested prompt protocol to an older .24 source tree, but omits an
existing motion feature. Keep it labeled as a protocol-development candidate.

## Versions and repository relationships

| Artifact | Pinned identity | What it establishes |
| --- | --- | --- |
| Installed development firmware | M115 reports 2.0.8.28F4, June 19, 2023 18:06:53 | Reported version; not a complete installed-image readback |
| Manufacturer stock delivery | .28F4/F401 package, mainboard SHA-256 `de6290525b7cd183f32dbae3b0f4e752374196eb90bf864beae637f2d045dcc2` | [Manufacturer provenance and payload comparison](recovery.md); physical restoration untested |
| Current candidate | [98aa032](https://github.com/joenewbry/Ender-3S1-llm/tree/98aa03248337b8039a09bbc1a29f33432aed08ca), based on official `s1_pro_plus` 7fffa927 | .24F4 controller source, with our bounded prompt and protection changes |
| Official HighSpeed branch | [1a502c7](https://github.com/CrealityOfficial/Ender-3S1/tree/1a502c76b09c3e192f15857838f3017a9422e8d6) | Complete tree has 211 entries/203 files: screen payloads, notes and a prebuilt .27F4 image, **not controller source** |

The HighSpeed branch name does not identify a newer source baseline to build.
The checked `main` and laser branches report V3.0.3/V3.0.3_Z and contain no M593 or
input-shaping source; the checked language branch has no Marlin Configuration.h.
Matching .28 controller source was not located in these checked branches. This
is not a claim that it cannot exist in another channel. The screen branches are
separate resources, and none of these are Mac/Linux editions of printer firmware.

## Known feature differences

The historical development-only M503 capture from October 25 reports:

```text
M593 X F50.00 D0.15
M593 Y F35.00 D0.15
```

It labels these as input shaping. These are historical reported settings, not
fresh calibration measurements or commands to replay. The manufacturer .28 notes
also advertise vibration optimization. Searching all tracked Marlin files at
candidate 98aa032 finds no M593 method/dispatch or INPUT_SHAPING source. **The
candidate does not retain this shaping capability.** Its S-curve acceleration
and jerk settings do not establish the same feature.

| Feature | Stock evidence | Candidate and qualification |
| --- | --- | --- |
| Input shaping | Historical M503 reports X 50 Hz, Y 35 Hz, damping 0.15 | Missing from the .24 candidate; do not claim stock motion-feature parity |
| Typed remote waits | Actual stock M115 reports host actions, prompts and emergency parser disabled | Candidate enables those paths plus session/prompt tokens; native/software evidence exists, physical pairing remains open |
| Model preview, screen PID and leveling-data workflow | Manufacturer .28 release notes advertise these features | PID command M303 and leveling code exist in the older source, but matching screen behavior is unqualified; no inference of UI parity |
| 160 mm/s printing | Manufacturer release claim | No measured speed, quality, vibration or physical-print equivalence on this unit |
| Settings restoration | Historical M503 transcript exists | Still untested; source V83 and a V83 literal in the stock image do not prove EEPROM layout compatibility |

## Engineering decision and next step

Retain the current candidate, six firmware draft PRs and pinned native fixtures
as development evidence. Do not relabel .24 as .28 or present a compiler pass as
a compatible print upgrade. The proposed printing baseline should preserve the
existing input-shaping capability and other required stock behavior. A maintained
S1 Pro Marlin baseline with shaping is the next source evaluation; port the
bounded prompt changes and qualify the result before changing test-source pins.
No switch to Klipper or replacement source has been selected by this audit.

The current candidate can remain a non-printing protocol experiment. Any future
installation still needs target/bootloader/recovery and sensor qualification;
printing additionally needs a deliberate feature/configuration comparison and
the [separate printing plan](printing-qualification-plan.md). The current screen
should be preserved unless a different paired screen update is justified and
qualified. The manufacturer recovery archive is not a settings backup.

[Structured findings and source references](../validation/firmware-parity-stage134.json)
separate reported stock facts, complete-tree inspection and unresolved physical
qualification. This audit changes no runtime, firmware binary or test pin. Camera
work stays deferred; the protected printer remains excluded.

## Follow-on maintained-source port

A separate [v040-based candidate](maintained-firmware.md) now builds and has
focused native/host prompt checks. It retains input-shaping source while
porting the guarded protocol. The earlier candidate and its test-source pin
remain unchanged. Neither source has physical printing qualification, and the
new baseline still needs its own screen, settings, thermal-manager and hardware
qualification before commissioning.
