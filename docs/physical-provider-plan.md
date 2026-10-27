# Physical provider implementation and qualification plan

Status: design and dependency record. No physical provider, physical readiness
checker or production launcher is implemented or enabled by this document.
The source-backed cold and post-drain inspections are implemented; supplying
physical facts to their external provider interfaces remains unfinished.

## Inputs still needed from the development printer

- Confirm its exact board/processor target through markings or other reliable,
  unit-specific evidence. The About photo reports 2.0.8.28F4, V1.0.5 and
  CR-FDM-v24S1_301. F401 remains an inference. The published source uses that
  hardware-label string with different processor selections. Open source does
  not identify the installed chip, flash layout or bootloader.
- Resolve the recorded cold nozzle reading of -15 degrees C and obtain current
  cold nozzle/bed readings. The earlier bed reading was 21.7 degrees C, with
  both targets zero. A clog does not establish the sensing fault's cause.
  No repair is recorded or assumed.

These facts gate firmware selection/commissioning and operational tests. Board
inspection does not gate ordinary source, simulation or documentation work.
There is no need to resend the About values. Identify the physical display if
needed to qualify the candidate/display pairing; do not update its firmware
merely because a recovery archive contains display files.

## Engineering qualification after intake

| Boundary | Required evidence and implementation | Current limit |
| --- | --- | --- |
| Firmware and recovery | Match the actual target to pinned candidate, layout/bootloader, settings migration and compatible stock rollback; qualify the paired display behavior | Candidate builds and manufacturer-package provenance exist; installed compatibility and physical restoration are unqualified |
| Pre-open admission | Use OperationService.from_backend_factory with actual assignment, protected-device exclusion, host access policy, qualified driver and current operator-observed readiness | The ordering exists; no physical checker supplies those facts |
| Owned connection | Open only the assigned descriptor, retain the lease/exclusion, capture every actual read/write, establish a new journaled firmware session and verify capabilities/configuration before job actions | Linux software-terminal foundation exists. Production Mac transport rejects before open; the separate operator diagnostic is not qualification of that adapter |
| Machine profile | Bind measured workspace, probe/frame interpretation, firmware/settings hashes, cold sensor checks and a bounded continuously powered motor/position policy | No development-printer profile is qualified or installed |
| Operation evidence | Combine fresh source reports, exact drain/write evidence, job/file/cursor/modes and qualified physical continuity inputs | Controller variables and software fixtures cannot supply physical continuity by themselves |
| Runtime behavior | Bound callbacks or implement a guarded asynchronous provider; measure source ages, prompt responsiveness, deadlines and shutdown behavior on the actual transport | Current external callbacks run under the backend guard and cannot be preempted |

Pre-open evidence must come from trusted operator/qualification sources, without
opening a printer prematurely. After a qualified connection opens, observe and
verify the actual firmware/configuration before enabling job actions. These are
two separate admissions. A stale journal entry cannot substitute for either.
The optional [owned firmware/settings collector](configuration-report.md) now
supplies a reported baseline before the first job. Its digest does not establish
complete runtime configuration, installed binary or physical readiness. V2 controller reports now supply raw live inactivity timers and timeout predicates;
full no-sleep/pause runtime policy and profile coverage remain separate gaps. Optional compound checkpoint
inspection now obtains a new settings report for each pause/resume; matching
reported rows still do not establish complete configuration or physical identity.
The current stock firmware does not supply the new LLM_MACHINE_STATE report;
that source path needs the separately qualified candidate installation.

## Callback implementation mapping

The future physical provider must replace the fixture provider, not wrap its
private virtual counters. The existing AssignedJobBackend constructor requires
preflight, start_blockers, checkpoint and started/resumed/completed/cancelled
callbacks. Optional post-drain mode additionally requires
checkpoint_from_controller(identity, report).

| Interface | Required producer behavior |
| --- | --- |
| Startup readiness_check | Check actual pre-open qualification and any prior-state reconciliation before returning StartupAdmission; an evidence ID alone is insufficient |
| preflight(data, identity) | Validate the bounded payload and its immutable policy/job hash against the qualified machine profile, fresh source and current physical readiness |
| start_blockers(identity) | Recheck live assignment, settings/frame, sensor, position/hold and workspace facts; report every unknown or expired prerequisite |
| checkpoint_from_controller(identity, report) | Interpret native commanded coordinates in the qualified checkpoint frame, bind original source identity/time, and independently qualify queue/position/motor/sensor/part continuity before producing PauseCheckpoint |
| started/resumed | Bind the observed transition and progress to the exact operation, file and source interval; a successful write or sender state alone is insufficient |
| completed/cancelled | Qualify the final file/drain and shutdown state under the operation/lifecycle window, retaining unknown outcomes when proof is missing |

The legacy checkpoint callback must not silently fall back to fixture fields.
If a supported coordinate configuration or physical fact cannot be established,
return a blocker or unavailable evidence. Do not fill missing offsets, positions,
configuration hashes or proof booleans with defaults. Logical homing/enabled flags,
reported temperatures and native commanded coordinates are evidence inputs,
not independent motor, sensor or part measurements. Any inference accepted by a
qualified machine policy must have its assumptions and limits recorded.

Qualification and current observations must bind the actual assignment,
firmware/configuration, job/file, host session, connection generation and relevant
pause interval. Recheck after external work or persistence. New firmware, changed
settings, sensor trouble, lost ownership, unexpected commands, reset/reconnect,
position loss or disturbed parts invalidate the applicable grant. A reference
ID, signed file, owner answer or successful test does not authorize all later
operations by itself. No LLM tool may install a qualification or waive a fault.

## Delivery evidence and remaining demonstration

The earlier post-drain source cohort passes 403 Linux checks and 372 Mac checks,
with 31 Linux-only Mac skips. It uses the actual shared writer/capture/channel/
journal/backend and synthetic source/physical inputs. It does not implement this
physical provider or qualify the connected printer. The earlier installed-package
snapshot is a separate qualification; no new full-suite claim is made here.

After implementing and qualifying the concrete provider/host profile, commission
only the development printer, verify recovery and safe alerts, then demonstrate
a bounded nonprinting job with pause/resume/cancel and uncertainty handling.
Preserve the original printer under every condition. Real heating, extrusion,
long pauses and print quality follow the separate post-repair printing plan.
Logitech work remains deferred and is not a prerequisite for source work.
