# Printing qualification after nozzle repair

Status: proposed sequence; **not executed**. This plan is separate from software
qualification and the clogged-printer non-printing tests. It never authorizes the
protected printer, a shared-hub reset or automatic recovery of an existing part.

## Entry conditions

Use only the independently assigned development printer after exact board,
processor, screen, installed candidate and recovery procedure are verified.
Resolve the negative sensor reading, repair the nozzle, verify sensors and normal
homing/probing, and establish actual movement/bed clearance. Retain readable
settings, firmware/file hashes, device generation and recovery artifacts. The
mainboard/screen source-version mismatch must be understood before installation.

The physical USB backend, exclusion policy, manual stop access and command receipts
must pass non-printing commissioning first. Camera images need verified assignment,
framing, capture timing and a maintained view calibration. An unavailable camera
must not be described as continuous inspection or a confirmed stopped printer.

## Test sequence

| Stage | Procedure to qualify | Evidence required to advance |
| --- | --- | --- |
| 1. Repaired-machine baseline | Select and review a small, bounded job/profile for the actual repaired machine. Confirm the bed/material/nozzle setup and run it under supervision without interruption. | Exact file/profile hashes; actual identity/settings/sensors; ordinary stage/start/completion and shutdown evidence; physical inspection of adhesion and part quality. Protocol completion alone is insufficient. |
| 2. Brief powered pause | Pause while streaming, drain/account for buffered motion, verify checkpoint and remain paused for a deliberate recorded interval within the qualified policy. Resume explicitly. | Same job/hash/generation, cursor and mode evidence; actual pause/position/targets/fan observations; no unearned resume authority; physical continuation inspection. |
| 3. Repeated pause/resume | Perform a second independently requested cycle and repeat identical request IDs while polling receipts. | One effect per request, no replay of prior restoration resources, correct cursor and material continuity; actual print inspection. |
| 4. Bounded extended pause | Only after the thermal/motor/adhesion policy is physically qualified, choose an interval inside its bounds. Apply standby targets, observe motor/position/part retention, reheat and restore before resuming. | Recorded duration, temperatures/targets, motor/position confidence and adhesion; policy expiry behavior; actual resumed part quality. Do not infer endurance from accelerated clocks or seconds-long software fixtures. |
| 5. Cancellation while paused | Cancel a separately staged test during pause and verify the required shutdown/cleanup. | Explicit stop/cancel outcome and fresh relevant device evidence. A successful write or closed connection is not proof of stopped motion or cold surfaces. |
| 6. Fault/recovery boundaries | First replay software cases. Any controlled physical disconnect/reset experiment needs its own qualified procedure and a fresh disposable test part. | Unknown state retained when evidence is lost; no blind file replay, homing into an existing part or automatic power-loss resume. Physical continuity remains disabled until separately established. |

Choose numerical movement, temperature and duration limits only from the qualified
hardware/profile and its validated policy. This document supplies no executable
G-code and does not bypass the current no-heating/no-extrusion development profile.
Never deliberately induce runaway, drive into the bed or force a filament jam.

## Acceptance record

For each stage retain starting state, trigger, exact command/receipt sequence,
expected and actual events, firmware/host/configuration identities, timing, source
frame IDs, final targets/state and physical inspection. Distinguish measured
position from commanded position and observations from visual inferences. Record
any detached/moved part, uncertainty or policy violation as a failed continuation
qualification even if the sender reports completed.

Only a passing, reviewed hardware-backed record can qualify that tested profile
and pause interval. It does not qualify other jobs, hours-long pauses, power-loss
recovery, a different board/screen/nozzle, or the protected printer.
