# Blocker qualification matrix

Matrix updated through stage 81, October 25, 2026. Passing a host rejection
check does not qualify physical recovery. The protected printer is excluded from
all commissioning. Evidence paths below refer to local, non-public test records.

| Scenario | Trigger and verified behavior | Evidence type | Remaining work |
| --- | --- | --- | --- |
| Generic user wait | Native handler plus virtual stream blocks at move 10; exact prompt response clears wait; qualified pause/resume finishes 80 moves once | Compiled native handlers, virtual sender, actual MCP client and model demonstration | Full controller/screen and development hardware qualification |
| Runout represented as generic continue | Native process retains runout; response receives physical-action rejection; host retains requirement across inspection and refuses another response without writing | Native handler with sensor stub, journaled host adapter; tests-stage33-final.xml | Actual reload evidence and post-intervention recovery; sensor behavior on development printer |
| Firmware-reported homing failure | Inject `Error:Homing failed, printer halted` and halt report while paused; sender disconnects, status retains fault, resume/cancel are refused with unknown device outcome | Synthetic serial reports through real virtual sender; tests-stage35-faults.xml | Real firmware homing-failure detection and timeout logic; normal qualified homing only on hardware |
| Firmware-reported probe failure | Inject probing-failed/halt reports while paused; retain fault, reject resume and avoid claiming confirmed stop | Synthetic serial reports through real virtual sender; tests-stage35-faults.xml | Actual probe-state handler tests; verified normal probing on hardware |
| Firmware-reported heating failure | Inject heating-failed/halt reports while paused; retain fault and reject resume | Synthetic serial reports, no simulated heating or real heater use; tests-stage35-faults.xml | Actual firmware heating-wait timeout/cancel and detection tests; scripted pause reheat is covered below |
| Thermal fault | Virtual reports retain fault and refuse resume; native runaway/error/disable functions detect a sustained drop and clear modeled targets, PWM and pins | Virtual debug injection plus 18 extracted native nozzle/bed cases; tests-stage35-faults.xml and native-thermal-stage78.json | Full manager/sensor/interrupt/kill integration and physical shutdown remain unqualified; never induce physical runaway |
| Short pause and explicit resume | Queue barrier, position/modal checkpoint, unchanged paused movement count, explicit resume, once/in-order completion | Virtual sender and actual model-selected operations | Cold hardware rehearsal after commissioning; real extrusion later |
| Extended pause / clock correction | +61 simulated policy seconds expires checkpoint; clock correction and another pause request cannot renew it; resume refused | Accelerated virtual policy; tests-stage31-renewal.xml | Software standby/reheat now covered below; physical thermal/motor policy and endurance qualification remain |
| Repeated hot software pause | Two standby/reheat/restoration cycles preserve cursor/position and execute every original file move once; old resources retire | Actual sender with extended simulator, scripted temperatures and virtual continuity; tests-stage60.xml | External hot MCP profile now tested; arbitrary job profiles and physical thermal/motor/adhesion qualification remain |
| Temperature loss after restoration | Drop scripted nozzle actual before resume; policy invalidates, sender remains paused, no new job moves | Actual paused hot software job; tests-stage59.xml | Physical observations and fault handling |
| Connection loss/reset during standby | Invalidate policy and close monitor; reject stale/current resume requests; no reheat or file replay; reset handshake is not restored authority | Real virtual transport/debug reset during hot pause; tests-stage62.xml | Physical transport recovery, heater state reconciliation and actual reconnect qualification |
| Missing standby receipt | Drop marker evidence after target writes; timeout marks the effect unknown, refuses resume, and does not resend or reheat | Actual paused hot virtual job; tests-stage62.xml | Physical serial proof and recovery from ambiguous effects |
| Cancel during native wait | Sender cancel attempted; tool reports stop unconfirmed and requires recovery, no prompt dismissal or resume | Native/virtual MCP test, tests-stage29.xml and later regressions | Separate virtual stop now passes stage36 tests; physical stop and reconciliation remain unqualified |
| Lost USB/transport | Virtual disconnect while paused and during active streaming leaves outcome unknown; stale-generation requests and new mutations refused; original start retry sends nothing | Actual virtual disconnect; tests-stage35-faults.xml and tests-stage38.xml | Physical disconnect/reconnect identity, pending-operation interruptions and recovery qualification |
| Controller reset during streaming | Actual virtual reset clears buffer; post-bootstrap start banner changes generation and invalidates checkpoint; outcome unknown, mutations refused and no start replay | Virtual reset through sender receive hook; tests-stage39.xml | Physical reset behavior, controlled reset tool and reconstruction of safe state before any recovery |
| Resend of a rejected current line | Exact requested frame retransmitted; pause/resume then all 80 moves once in order | Real virtual sender with prepared pre-execution error; tests-stage40.xml | Physical error detection and relative-extrusion qualification |
| Resend of an already executed line | Generic debug trigger previously duplicated movement; the virtual acknowledgment guard now refuses before replay and invalidates the connection | Failed stage40 logs preserved; passing stage41 guarded old-line injection | Physical acknowledgment profiles and wider resend windows remain unqualified |
| Host restart, lost acknowledgments | Protocol/journal/prompt-channel coverage plus real child-process SIGKILL at five accept/dispatch/completion boundaries; retry does not replay | Unit/native and process-crash tests with independent file dispatch witness; no physical device | Full combined streamed-job interruption matrix at each transition, host power loss and physical transport recovery |

No test in this matrix establishes real heating, extrusion, print quality or safe
continued printing after a clog. The three new fault-report scenarios all begin
from a verified paused job: they do not establish that a homing/probing/heating
operation was actually running when the fault occurred. Firmware fault detection
and host fault handling are separate requirements.

## Development commissioning prerequisites

The earlier read-only development probe reported nozzle temperature −15°C with
a zero target. Cause remains unresolved. No heating, extrusion, motion, reset or
flash has been performed. Exact board/processor, bootloader/recovery package and
screen version still need matching before a candidate can be installed. Current
bed/movement clearance is not established. The inventory's older left/right
printer labels do not resolve these details. A clarification about development
screen versions and movement clearance is pending; continue software work.

A valid software build and USB topology record do not satisfy those prerequisites.
Never substitute the protected printer or reset the common hub to make progress.


## Native heating-wait qualification

The extracted, unchanged `Temperature::wait_for_hotend` loop now runs with the
pinned configuration's10-second residency,1°C window and3°C hysteresis. The
harness uses the actual emergency-parser header, synthetic temperatures and
clock, and stubbed idle, stepper timeout and screen calls. Seven cases cover
stable heating, legacy M108 interruption, SD-print abort, token-mode rejection
of M108, cooling skipped by S semantics, stalled cooling and target reduction.
It records source/snippet hashes and configuration values in its build evidence.

A true return and a screen-ready update do not prove the target was reached:
cooling can be skipped, a cooling-slope guard can end the loop, and a changed
target can cause early exit. M108/SD abort exit without ready-screen updates but
do not turn targets off. Token-mode M108 rejection is verified: the fixture
terminates that test with a later SD-abort input. This is not evidence of a
supported token-mode host cancellation of an active M109 wait.

The full thermal protection manager, sensor sampling, PWM, interrupt timing and
idle dispatcher are not exercised here. Actual heating-failure detection,
a token-safe heating-wait cancel path remain required; native bed-wait evidence is added below.
Virtual standby/reheat cancellation does not prove those firmware behaviors.


Hot simulator stop now has external MCP and managed-reheat interruption tests.
It uses the sole frame writer outside the ordinary queue, invalidates resume,
and confirms the modeled killed/drained/zero-target state. A lost stop frame
returns outcome_unknown while heater targets remain nonzero. This tests the
explicit extended simulator; stock VirtualPrinter's killed flag alone did not
satisfy shutdown criteria. Active firmware heating-wait stop/queue behavior and
physical stop remain unqualified.


Stage71 extends the same extraction harness to unchanged Temperature::wait_for_bed
with its own configured residency/window/hysteresis values. Sixteen nozzle/bed
cases now include a5°C temperature excursion that restarts the stability timer.
The bed resets UI status on a successful wait; unlike the nozzle path it does
not invoke the RTS ready-page update tested here. Interrupted waits do neither.
Both paths leave targets unchanged after legacy M108 or SD abort, reject legacy
M108 in token mode, and can return true away from target when skipping/stalling
cooling or when the target changes. Evidence: native-heating-stage71 source hashes
and observations. This still excludes full thermal protection, bed sensor/PWM,
real heating, and active M109/M190 integration with the external client.


## Native thermal-runaway detection and shutdown calls

Stage 78 compiles the unchanged `tr_state_machine_t::run`, `_temp_error`, and
`disable_all_heaters` bodies from the pinned firmware, with the actual state
structure from its header. Configuration supplies nozzle period/hysteresis
(60 seconds / 4°C) and bed values (180 seconds / 2°C). Heater-idle handling and
automatic-temperature disable are enabled for this fixture; adaptive fan slowing,
other heater types and the optional bogus-temperature grace period are excluded.

Eighteen cases cover both heaters: one millisecond before and at the fault
deadline, clock wraparound, repeated fault, the hysteresis boundary, recovery
before timeout, target removal/change, first heating and heater-idle timeout.
At a runaway fault the real functions emit the modeled error, request the screen
fault page, clear both modeled targets and PWM amounts, write both modeled pins
low, disable automatic temperature control, refresh the watchdog and invoke the
recording kill stub once. Repeated faults still perform shutdown calls. These
observations are in [the native validation record](../validation/native-thermal-stage78.json),
including source and extracted-function hashes.

Sensor values, clock, target setters, pin writes, serial output, screen calls and
final kill are test doubles. This is not the full temperature manager, actual
sensor conversion, electrical shutdown, screen rendering or physical kill path.
The native functions are unchanged; no protection is disabled in firmware.

The runaway state machine waits for the first target to be reached. Initial
heating failure is handled separately by the `WATCH_HOTENDS` / `WATCH_BED` checks
in `manage_heater`; leaving the machine in `TRFirstHeating` in this harness does
not demonstrate a missing firmware safeguard. That separate watchdog still
needs native qualification. Likewise, supplying target zero or an idle timeout
to this isolated state machine tests its reset behavior, not the setters or
idle logic responsible for switching off heaters in the complete firmware.


## Initial-heating watchdog and deadline correction

Stage 79 adds the actual `HeaterWatch` template, start-watch methods and both
`manage_heater` watch-check branches to the native harness. The nozzle must rise
by 2°C within 40 seconds; the bed uses 2°C within 180 seconds. Those values and
the respective hysteresis settings come from the pinned configuration. Eighteen
cases cover failure to rise, insufficient rise, a later stall after an initial
rise, sufficient rise and restart, near-target disarming, target-off disarming,
repeated faults and two timer-wrap boundaries for each heater.

The exact-zero wrap case failed on firmware `a5c1cab`: `next_ms == 0` means the
watch is disabled, but addition can produce zero at timer wrap. Both heater
fixtures therefore failed to dispatch the expected error. Firmware correction
`126e171` reserves zero for disabled watches and moves a computed zero deadline
to 1, delaying that exceptional deadline by one millisecond. Both regressions
now dispatch the real error/disable functions, clear the modeled heater outputs
and invoke the recording kill stub. Other tested deadline and disarm behavior
is preserved. Failed evidence is retained; the fix is not merely inferred from
source. See [watch validation](../validation/native-watch-stage79.json).

The fix applies cleanly to the pinned original Creality source, but this is not
a current-upstream reproduction or an accepted contribution. It changes the
modified firmware pin used by native tests; older validation records retain their
original source identities. The new F401 candidate builds but has not been flashed.

The full temperature manager, ADC/sensor conversion, interrupts, electrical
outputs, physical kill and end-to-end host reporting remain unqualified. The
fixture invokes unchanged watch-check branches with synthetic time/temperature;
watch restart is explicit in the driver. Target setters and final kill remain
stubs, so this does not prove all production scheduling and recovery behavior.


Upstream review subsequently identified the distinct near-target protection gap
fixed by [Marlin PR 28695](https://github.com/MarlinFirmware/Marlin/pull/28695).
The old Creality source used here lacks that correction. The earlier isolated
watch/runaway cases do not qualify their handoff: add combined near-target
failure/stall tests and backport the upstream correction before physical heating
qualification. The zero-deadline correction alone does not close this gap.


## Near-target handoff backport

Stage 81 backports [Marlin PR 28695](https://github.com/MarlinFirmware/Marlin/pull/28695)
as firmware `98aa032`, preserving its attribution and our separate zero-deadline
fix. The watch now uses the runaway hysteresis and remains armed until that
boundary; its final rise step is capped at the boundary. The runaway state
starts stable monitoring at the same threshold. Configuration comments follow
the upstream change; temperature limits and periods are unchanged.

A combined native fixture calls the actual watch and runaway functions in the
pinned manager's respective nozzle/bed order. It also extracts the actual watch
typedefs, avoiding hard-coded hysteresis wiring. Before the backport, six fault
cases produced no fault and two boundary cases lacked stable protection (eight
failed tests); both ordinary-heating cases passed. Afterward all ten pass:
stalling one degree below the boundary produces heating failure, dropping from
at/above it produces thermal runaway, and normal heating or a boundary hold
remains clear across ten simulated minutes. All modeled heater outputs clear
when a fault occurs. See [combined evidence](../validation/native-handoff-stage81.json).

This resolves the specific local backport prerequisite described above, not
physical-heating qualification. The driver supplies synthetic temperature/time
and explicitly starts the watch. It does not execute the full manager, ADC,
interrupts, real target setters, PID, physical outputs or terminal kill. Chamber
and adaptive-fan behavior are not exercised by the single-nozzle/bed profile.
The updated firmware has not been flashed to either printer.

## Native homing validation and probe-result handling

Stage 101 compiles the unchanged `Endstops::validate_homing_move` and
`Probe::probe_at_point` functions from pinned firmware `98aa032`. Eight cases
exercise trigger present/absent, unreachable coordinates, deploy failure, an
invalid probe measurement, stow failure, normal success and initially triggered
BLTouch reset. The fixture selects RTS screen and BLTouch high-speed branches,
with G29 retry/recover disabled. It supplies trigger bits, probe results,
reachability, deployment, movement, screen writes and a throwing kill stub.
Message macros are fixture labels, not a rendered/translated screen test.

A missing homing trigger sets screen error state 2 and calls terminal kill; a
valid trigger clears the hit. Probe deploy/measurement/stow failures return NaN,
set screen error state 3/page 41, attempt stow and report an error, but this
function does not call kill. Unreachable coordinates return NaN before motion
or the error-report branch. Callers must interpret that result; an invalid
measurement alone does not prove terminal shutdown.

These tests qualify the extracted functions only. G28/G29 caller handling,
endstop interrupt sampling, physical probe timing and movement, terminal-kill
internals and real screen rendering remain unqualified. Existing synthetic
probe-plus-halt tests do not represent every firmware probe-error path.

The added `probe_no_halt_fault` host scenario injects only the pinned English
serial message `Error:Probing Failed`, without a fabricated halt line, into an
already paused virtual job. The actual sender closes the connection under its
configured error policy; typed job tools retain the fault, reject resume/cancel
and report outcome unknown. All six host fault scenarios pass, with zero
physical serial-open attempts. This is host handling of a synthetic report,
not a probe command executed by the virtual printer or a firmware-to-host
integration test.

## Actual virtual-sender process death and restart

Three additional scenarios SIGKILL the actual OctoPrint sender/tool-server process
while streaming, after a completed pause, and after resume dispatch while its
receipt is still running. Each restarts the server with the same SQLite journal
and a fresh virtual connection. Completed receipts remain byte-for-byte equal;
the interrupted resume becomes `outcome_unknown` with `host_restarted` and
`requires_reconciliation`. Repeating old requests dispatches nothing. New
start/resume requests without current staging/checkpoint are rejected, and the
replacement simulator executes zero job moves. Both launchers deny physical
serial opens. All three scenarios pass.

The original simulator dies with its host. This is a process/journal plus actual
sender integration test, not continuity of a surviving printer. It does not
qualify buffered motion, heater disposition, physical reconnect or resuming a
real part after host death. Those remain required for recovery qualification.
