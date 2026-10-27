# Goal completion audit

October 27, 2026. **The original goal is not complete.** Source publication,
software demonstrations and review PRs do not establish a commissioned printer.
This audit retains the original development-printer scope and excludes the working
printer even after its job finishes. The rows describe evidence scope, not a new
full-suite run.

## Original implementation steps

| Requirement | Evidence and current state | Unfinished work |
| --- | --- | --- |
| 1. Preserve source and match hardware/recovery | Pinned controller/screen sources and attributed candidate builds exist; [source relationships](contributions.md), [build records](firmware-stack-builds.md), [recovery research](recovery.md) | The owner About photo now reports firmware 2.0.8.28F4, screen V1.0.5 and hardware label CR-FDM-v24S1_301; see [hardware intake](development-hardware.md). Physical processor/board markings are deferred; the [manufacturer stock package](recovery.md) and mirror payload identity are now verified, while bootloader compatibility and physical rollback remain unqualified. Historical settings exist but restoration is untested. The [parity audit](firmware-parity.md) confirms stock input shaping is missing from the .24 candidate; it is a protocol experiment, not a stock-equivalent print upgrade. The [maintained v040 candidate](maintained-firmware.md) retains shaping and now includes the complete watch/runtime handoff correction, with 32 extracted fault-path cases; full manager and physical qualification remain open. |
| 2. Fork, branch, focused stack and ecosystem contribution | Public host and Creality firmware repositories, dependent draft PRs and [standalone Marlin PR 28753](https://github.com/MarlinFirmware/Marlin/pull/28753) exist | The requested publication/submission artifacts exist. Review, merge and maintainer acceptance are external follow-up, not claims made by this delivery. Physical release qualification is tracked separately below. |
| 3. Specify the whole LLM interface and blockers | [Interface](interface.md), [blocker matrix](blocker-matrix.md), typed tool schemas, exact prompt identities and explicit uncertainty rules exist | Actual display/controller pairing and the complete physical alert inventory remain unqualified. Unknown states must remain blocked. |
| 4. Build firmware and implement host control | Candidate builds, native firmware functions, virtual sender, journal/tool service, Linux assigned prompt connection and Mac metadata discovery are tested | The assigned job/prompt adapter is software-tested with a software-only evidence provider; source-bound serial reports retain actual sender line identity/age and synchronously retire dispatch on sensor/firmware hazards, while the physical evidence provider, remaining policy/recovery integration and Mac serial-driver isolation are unfinished. Pre-open startup admission and post-commit revalidation exist, but no real hardware readiness checker is supplied. Sender error closure uses a short latch and emits no automatic commands on an uncertain connection. |
| 5. Simulated printer and blocker matrix | Real virtual sender, native handler/protection fixtures, MCP/model demonstrations and [streamed process-death cases](../validation/streamed-host-restart-stage109.json) provide substantial software evidence | A simulator restarted with its host does not model a surviving physical printer. Full controller/screen/ADC/interrupt integration and real timing remain open. |
| 6. Commission only the clogged printer | No firmware was flashed; the earlier development-only query reported a negative nozzle temperature with zero target | Owner confirmed idle/clear; current read-only queries again report nozzle −15°C with zero targets. Diagnose sensing and qualify the candidate/recovery route before installation or actuation. No protected-printer substitution. |
| 7. Exercise sequences and simulated printing | Constrained virtual jobs finish original moves once, including supported waits and powered pause/resume; [model hot-pause evidence](../validation/model-hot-stage75.json) | No motion-only hardware rehearsal or real-print continuation is qualified. A clogged nozzle does not make an ordinary print file safe to execute. |
| 8. Deliver report and separate printing plan | Reproduction instructions, public source/PR links, owned explanation page, qualification records and [printing plan](printing-qualification-plan.md) are available | Hardware-backed development release, checked rollback and physical demonstration remain open. Printing qualification follows repair and commissioning. |

## Required behavior and blocker coverage

| Area | What the evidence supports | What it does not support |
| --- | --- | --- |
| Identity and isolation | Assignment/generation rejection, journal lease, device-open guards, Linux kernel-terminal exclusion and read-only Mac discovery | Exact board/MCU/screen identity; OS policy against all pre-existing/privileged handles; physical driver/reset behavior |
| Generic wait and unknown alerts | Native token/session handlers plus virtual/MCP/model response to a supported exact wait; stale/unsupported responses refused | Universal dismissal of Creality screen popups or real screen rendering |
| Runout/reload | Native sensor substitutes and host rejection retain physical-action requirements | Actual reload evidence or extrusion through the clogged nozzle |
| Heating waits, timeout and thermal faults | Extracted hotend/bed waits, watch/runaway/handoff branches, virtual fault reports, source-bound shared-dispatch inhibition and modeled shutdown outputs | Complete manager/ADC/interrupt/kill behavior, token-safe cancellation of an actual active heating wait, physical heater shutdown |
| Homing/probing | Extracted validation/probe-result functions and synthetic error reports, including probe failure without an invented halt | Full G28/G29 caller/interrupt integration or a physically qualified probe/motion envelope |
| Queue pressure and stop | Bounded prompt/mailbox/frame paths, serialized writes and virtual stop/cancel evidence | Measured MCU response bounds or a physical emergency-stop guarantee |
| Pause/resume | Verified virtual cursor/mode/thermal restoration and original moves once; repeated cycles, invalidation and interruption tests | Endurance/adhesion/motor holding or actual extrusion continuation. Power loss/reset/reconnect resume remains unqualified. |
| Resend/ack uncertainty | Exact virtual resend cases, rejection of unsafe replay, durable unknown outcomes; the assigned sender explicitly retires on unqualified resend/timeout instead of inheriting replay or fabricated credit | Arbitrary physical acknowledgment windows or blind replay after reconnect |
| Host death | Journal/crash boundaries and actual virtual-sender SIGKILL in running/paused/resume-dispatched states; old requests do not replay | Buffered motion or heaters on a surviving printer, safe physical reconnect or recovered print continuity |
| Setup/calibration | Bounded software fixtures distinguish temporary changes from persistence | Physical homing, jogging, heating or calibration on this unit |
| Camera and mount — deferred by owner | Earlier broker/client/owner API tests and CAD/view models retained | Owner deferred Logitech work on October 26. Native capture/control and physical qualification remain unqualified and do not gate current printer work. |
| Publication | Owned explanation page, architecture, platform/repository distinctions, PR dependencies and screenshot review | Firmware commissioning or an accepted upstream release |

The [current installed Linux qualification](../validation/linux-package-stage137.json)
passes 1175 tests with zero skips at public source `ad89416`, including the camera,
startup, shared serial ownership, journaled assigned job/prompt tools, source-bound serial history and explicit transport-uncertainty retirement and synchronous source-hazard dispatch inhibition and an owned sender/prompt-copy lifecycle. The earlier 986-case qualification
remains historical. Subsequent delivery changes are documentation/qualification
metadata only. Neither test counts nor a successful source export prove physical
readiness.

## Startup gap found during this audit

The original service constructor receives an already-created backend. A future
physical backend could therefore open a serial port before journal ownership is
checked. `OperationService.from_backend_factory` provides the required order:
canonical journal lease, local recovery and historical-state summary, explicit
trusted readiness check, then backend construction with the same lease.

History includes prior backend construction intents, completed operations and observations: a completed `job.start`
receipt can coexist with a still-running print. History requires an explicit
prior-state reconciliation admission; it is not inferred safe from terminal
receipt status. An evidence ID is only a reference. The readiness checker must
independently establish actual assignment, board/recovery and current-machine
facts; no such physical checker is activated here. Old unknown receipts and
thermal/restoration gates remain intact. This is not a resume or reset tool.

## Next work, with dependencies

1. Continue the production transport/sender integration using the lease-first
   factory and qualified pre/post-open assignment checks. Keep it unavailable
   to physical tools until device policy, driver behavior and readiness are proven.
2. The About versions are recorded in [the owner intake](development-hardware.md). Defer physical processor/board inspection for host/read-only work; qualify firmware target and recovery before flashing;
   inventory notes, CH340 metadata and the firmware version string do not prove them.
3. Resolve the negative sensor report (owner already confirmed idle/clear) before
   firmware commissioning or operational tests. Never use the working printer.
4. Logitech work is deferred by the owner. Preserve the existing software/CAD
   and camera owner; camera assignment or mount qualification does not gate
   current printer progress.
5. Execute non-printing commissioning, then the separate printing plan after repair.

Physical dependencies do not excuse unfinished independent software work, and
software progress does not waive those dependencies. No success criterion has
been reduced to the work already completed.

Backend construction intent is committed before invoking the factory, even if no
ordinary operation receipt exists. A failed or interrupted open therefore leaves
history for the next readiness check. The immutable intent is not proof that a
connection succeeded or shutdown completed. Printer startup journals and camera
bindings are rejected in the opposite service role before receipt recovery.
