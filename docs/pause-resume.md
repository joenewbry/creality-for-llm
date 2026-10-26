# Pause and resume contract

Status: specification; physical pause/resume is not qualified. The initial mode requires the same powered controller, exclusive sender and uninterrupted connection.

## Commands and evidence

`job.pause` and `job.resume` are separate idempotent operations. Never expose a toggle to an LLM. Cancellation is terminal for this interface; restarting a file from its beginning is not resuming.

On pause, stop dispatching model commands first. Account for commands already transmitted and drain the planner through a supported barrier. Save the stream cursor at the next unexecuted command, the file hash, job ID, firmware/configuration identity, connection generation and checkpoint version. A serial `ok` alone is insufficient evidence that buffered movement finished. Record the checkpoint transactionally before reporting `paused`.

The checkpoint also records coordinates and their confidence, units, absolute/relative axis and extrusion modes, extrusion coordinate, feedrate, active tool, fan, temperature targets, coordinate offsets and leveling state. Parking is a separate bounded sequence with its own evidence. No parked coordinate may overwrite the original resume coordinate. Unknown modes mean no automatic resume.

On resume, validate the checkpoint and current state, apply the qualified thermal/motor policy, restore modes and required targets, return along a qualified path, then resume at exactly the saved cursor. No homing into an existing part, replayed extrusion, or silent acceptance of a replaced file. Host restart during any sequence produces uncertainty until reconciled, not an automatic retry.

## Short and extended pauses

Short pauses may retain qualified motor and thermal targets for a bounded interval. Extended pauses need explicit nozzle standby, bed adhesion and motor-hold policies, including idle-timeout behavior. We have not established safe temperatures or duration limits for this printer/material. Until measured, an extended pause cannot promise successful continuation. A timer expiry must produce a visible state/reason, not silently disable motors while continuing to claim resumability.

Turning the machine off is a separate recovery feature. A saved file offset does not establish physical position or part adhesion after power loss. USB loss, firmware reset, lost motor holding, a moved part, changed configuration, thermal faults and uncertain execution invalidate automatic resume. `job.inspect` must return a machine-readable reason and needed intervention.

## Tests required before claiming support

- Pause at a known cursor with commands buffered; ensure all preceding commands execute once and no later model command executes until resume.
- Repeat pause and resume requests; return the same receipt without extra movement.
- Exercise relative/absolute coordinates and extrusion, changed feedrate, offsets, fans and temperature targets.
- Cancel while paused; reject later resume without starting another job.
- Inject disconnect, firmware reset, host crash, lost position, stale telemetry and file replacement at each transition.
- Exercise extended-pause timeout, sensor faults and failed reheat in software; never generate these faults on the physical printer.
- Run the same host adapter through a serial emulator, then qualified cold motion. Successful simulated extrusion does not qualify a real print.

The clogged development printer has a negative nozzle-temperature observation; heating and extrusion remain disabled. The other printer remains excluded from all development access.

## Checkpoint implementation

`PauseCheckpoint` validates a versioned, immutable record with file/configuration
hashes, printer and host session identity, cursor/line number, coordinates,
offsets, leveling, axis/extrusion modes, feedrate and overrides, tool, fan and
temperature targets. It also requires explicit pause evidence. A false proof
can be stored for diagnosis but blocks eligibility. The checkpoint hash detects
accidental payload changes; it is not an authentication signature.

The whole checkpoint envelope can be committed atomically as the completed
pause operation's journal result. Reopening the database preserves the record;
a new host session still blocks automatic resume. No separate checkpoint file
write can race the completed receipt. A current observation must be obtained
again from the backend, not copied from saved evidence.

The implemented comparison is for an unchanged, in-place pause. It rejects
changes to modes/targets/position instead of assuming a restoration sequence
has already been qualified. Parking, reheat and extended-pause policies remain
required work; this conservative initial predicate does not replace them.

`ModalTracker` observes normalized commands in transmitted order. Its output is
commanded state, not completed execution. Unknown commands latch uncertainty;
defaults are not inferred. Position/offset/leveling and physical continuity
still require independent backend evidence. The tracker is now connected to
sender hooks through `StreamWitness`; the virtual backend captures and compares
checkpoints before in-place resume. Unsupported modes or absent evidence still
reject resume.

The pinned S1 firmware clears the extrusion override on G90/G91. Consequently
mode restoration must issue G90/G91 before M82/M83. A native harness compiles
the unchanged relevant methods extracted from `gcode.h` and checks 256 mode
sequences against the tracker. This is narrower than compiling the whole
G-code dispatcher; that distinction is retained in the test.

OctoPrint 1.11.8's virtual printer is useful for transport and buffering but is
not a complete Marlin execution model: its source has a single `_relative`
flag used for both axes and extrusion and lacks M82/M83 handlers. Its ordinary
acknowledgment path can therefore accept such commands without modeling their
effect. Do not use the earlier 80-move stream test as evidence of independent
extrusion-mode restoration. Source examined locally in the pinned environment;
no physical behavior is inferred from this simulator limitation.

## Virtual in-place integration

The current backend verifies the software printer type before accepting it.
The tested profile requires millimeters, matching XYZ/E relative modes, fan
off and zero heater targets; queue drainage and position are checked against
the software printer. Zero offsets, no leveling, held position and an undisturbed
part are properties of this constrained simulation profile, not measurements
from hardware. The configuration hash identifies that profile. These assumptions
must be replaced with qualified observations/policies for physical operation.

The completed pause receipt stores the checkpoint atomically. New ordinary
operations cannot dispatch until the prior operation has a durable terminal
receipt. A failed receipt write therefore cannot authorize resume from an
uncommitted checkpoint. Cancel/stop retain their separate interruption path.

The checkpoint expires after 60 simulated policy seconds. Tests advance the
policy clock to verify expiry without claiming a long-duration physical trial.
Valid in-place resume emits no restoration moves because the captured state
must still match. A queued/sent mutation or attempted blocked mutation invalidates
the saved witness epoch. Parking, changed modes/targets, extended thermal policies,
host restart and power-loss recovery remain separate unfinished work.

## Pause policy visibility and irreversible expiry

`job.inspect.pause_policy` now reports the cold profile's 60-second short-pause
limit, expiry time, remaining seconds, and whether the interval was invalidated.
It explicitly marks extended pauses unsupported and motor-hold evidence as
virtual-model-only. No nozzle standby/reheat or physical motor policy is inferred.

Observing expiry or a clock value earlier than checkpoint creation permanently
invalidates that checkpoint's interval. Later clock correction cannot revive it;
status revision changes on invalidation. A new pause request while already paused
is rejected, preventing checkpoint renewal through another request ID. Retrying
the original request still returns its existing durable receipt. Cancellation
remains available under its existing evidence requirements.

The virtual integration test shifts policy time from zero to +61 seconds and
back to zero, checks status and resume rejection, attempts checkpoint renewal,
and cancels without additional model movement. This is accelerated policy testing,
not a 61-second endurance test. Extended thermal/motor behavior remains unfinished.

## Abrupt host-process death

`tests/test_crash_recovery.py` kills a real child process at five operation
boundaries: accepted, claimed before dispatch, dispatched, completion transaction
before commit, and committed completion. A separate fsynced append-only file
witnesses the simulated dispatch independently of the journal. The successor
acquires the released owner lock and retries the original request twice without
emitting another dispatch. A second restart preserves the same receipt/events.

Incomplete operations return `outcome_unknown` and require reconciliation. A
completion interrupted inside the database transaction rolls back both its row
update and event; it cannot emerge as a completed pause. A committed completion
survives. These tests intentionally do not use graceful service shutdown.

This qualifies process-crash receipt handling, not USB survival, controller
position retention, host power-loss durability, or continuation after a host
restart. The fixture's pause evidence is software-only and explicitly not
resumable. Physical and end-to-end sender recovery remain unqualified.

## Extended thermal policy component

`ThermalPausePolicy` now implements a separate decision engine for a hot pause.
It is not connected to `job.pause` or any physical/simulator heater transport;
the existing job tools retain their cold, short-pause restrictions. It must not
be described as end-to-end extended-pause support yet.

The component starts at the pause checkpoint's capture instant and uses the
same host's monotonic clock. Limits are mandatory inputs: short/total intervals,
standby temperature, maximum temperatures, effect/reheat deadlines, stabilization
time, tolerance and maximum observation gap. There are no supplied hardware or
material defaults. The total interval cannot exceed the checkpoint's recorded
lifetime. A cold checkpoint cannot be reinterpreted as a hot-pause authorization.
The sample temperatures/durations in tests are synthetic fixtures, not operating
recommendations for a Creality printer or material.

After the short interval, the engine emits one identified standby-target action
while retaining the original bed target. It waits for an independently correlated
effect receipt; a target value alone is insufficient. A resume request emits a
separate action restoring the original nozzle target. It then requires fresh
in-range temperature samples over the stabilization interval. A temperature dip
restarts that interval. An unheated bed does not need to reach zero degrees;
when a bed target is active, losing its held temperature invalidates continuation.

Every observation must establish the same printer, connection, host session,
job, file, configuration, firmware and checkpoint, along with valid position,
undisturbed part, healthy sensors, no fault and a still-valid motor-hold lease.
An observation gap/replay, reversed clock, target mismatch, timeout or lost
continuity permanently invalidates the instance. Restoring good values cannot
revive it. A successful thermal check produces one-use eligibility; mode and
position restoration are still explicitly required before any stream resume.

Integration must durably record each returned action before dispatch, preserve
its ID across receipt reconciliation, and avoid replay after uncertain writes.
The component neither owns a transport nor installs a background timer; those
remain required integration work. A qualified adapter must supply evidence
rather than copy saved proofs, and must not reconstruct a fresh policy to renew
an expired interval. Power loss, host restart and reconnection remain excluded.

Tests cover the complete synthetic standby/reheat/stabilize/consume sequence,
uncorrelated target changes, stale receipts, sensor/thermal failures, motor-hold
loss, disturbed parts, identity changes, observation gaps, irreversible expiry,
failed reheat, bed-off behavior and bounded configuration. They do not test real
thermal dynamics, bed adhesion, motor endurance, parking or extrusion restart.

## Durable thermal-effect coordination

`DurablePauseEffects` now runs policy decisions under an existing
`OperationService` owner lease and backend guard. It creates thermal session and
effect records in the same SQLite journal. Session admission and table creation
are transactional. A target effect's `dispatching` record commits before the
adapter callback can run; matching observation evidence commits before the
coordinator exposes the resulting policy transition. Callback failure records
`outcome_unknown` and invalidates the policy. Receipt-storage failure also
invalidates it, even when the target or resume eligibility may have taken effect.

The service blocks ordinary operations while a thermal session is active or
requires recovery. Validated cancel/stop operations invalidate the thermal
session before their backend dispatch. Failure to store that invalidation does
not suppress cancel/stop. An unfinished session survives restart as a recovery
requirement, and its uncertain effects are never replayed. The same checkpoint
cannot create another thermal session to renew its interval. There is no
implemented recovery/clear operation for an invalid session yet.

Thermal eligibility consumption must also commit before returning permission.
It remains separate from restoring modes/position and actually resuming the
stream. Tests exercise real SQLite persistence and the operation service, using
an in-memory target-effect callback. Thermal records are available through the
coordinator's `effects()` method; they are not yet part of the public tool event
stream. No heater transport, continuous timer or new hot-pause tool is installed.
End-to-end serial simulation and physical qualification remain required.

## Virtual serial thermal effects

`VirtualThermalTransport` now connects the durable coordinator to the pinned
OctoPrint sender's ordinary queue, using M104/M140 targets followed by a unique
M118 echo marker. It verifies the same virtual device instance on every action,
limits targets to the supplied software profile, rejects duplicate effect IDs
and partial queueing, and permits at most two effects per adapter instance.
The original shared frame writer and resend guard remain in the path.

The serial regression currently uses an idle software fixture. The adapter also
accepts a paused virtual sender only with a frozen command witness, using the
exact-sequence grant described below. End-to-end streamed hot-pause integration
and mode/position restoration remain required. It is not offered through a
physical launcher or the public job tools.

The serial regression seeds an idle virtual model with hot initial targets and
explicitly scripted actual temperatures. Standby and reheat commands execute
through the real sender/virtual parser. A matching serial marker plus matching
internal target values establish target completion, separately from the policy's
temperature stabilization check. Dropping that marker causes timeout and an
unknown durable effect even when targets changed. No retry or movement occurs.

Run both software-only cases with the combined environment:

```sh
python tests/octoprint_virtual_smoke.py --service --scenario thermal_serial
python tests/octoprint_virtual_smoke.py --service --scenario thermal_serial_missing_receipt
```

The fixtures test integer targets because the pinned virtual target parser does
not model fractional targets faithfully; fractional requests are rejected rather
than silently rounded. The script records every target send and verifies zero
physical serial opens and zero completed moves. Temperature response, motor
holding and part continuity are explicit fixture assumptions. This is not a
hot streamed-print pause/resume, thermal-endurance or physical safety test.


## Exact thermal sequence through the frozen command witness

`StreamWitness.authorize_thermal` grants a nonce-tagged, exact M104/M140/marker
sequence at a specified mutation epoch. Every queue/send phase must arrive in
order; a wrong command, wrong phase, duplicate or stale tag is rejected. The
grant cannot authorize movement, arbitrary G-code or a second sequence. An
unrelated blocked mutation or emergency stop revokes the outstanding grant,
including commands already queued but not yet sent.

Authorized heater writes still update tracked modal targets and mutation epochs.
They do not preserve or renew the original resume checkpoint. The virtual adapter
checks the expected queued/sent epoch changes and refuses further thermal work
if unrelated mutations occurred. A new grant cannot simply adopt an unexpected
current epoch. The ordinary resume path remains blocked until a separately
qualified restoration/checkpoint process is implemented.

The actual serial thermal regression now freezes this witness before standby
and reheat, passes both exact sequences through all sender hooks, and proves
that the original checkpoint epoch cannot authorize resume. It still uses an
idle hot-seeded model with scripted temperatures, not a streamed hot job. Unit
checks additionally cover reordered commands, modified temperatures, movement
attempts, replayed phases, unrelated mutation and emergency stop revocation.

Combined-backend interruption must revoke the job sender's same witness. The
combined wrapper now forwards `job.witness` to the service-level coordinator;
a separate witness would leave the queued commands authorized. Four regression
cases queue a thermal sequence, then assert revocation inside cancel/stop dispatch,
with and without thermal-receipt storage failure. These exercise the actual
service and combined backend with stubbed device callbacks, not physical shutdown.

## Continuous software pause monitor

`ThermalPauseMonitor` samples the observation provider and advances the durable
policy on a worker thread, independently of LLM status calls. Its interval must
be at most half the policy's maximum observation gap. Resume requests are retained
as an event; target receipt correlation and temperature stabilization still run
on fresh observations. Consuming eligibility obtains another observation under
the monitor's cycle lock and stops the worker after durable consumption.

The service registers one monitor and closes it before closing the backend or
releasing its owner lease. Shutdown sets a stop event and joins outside the
backend lock. If the worker does not exit within the bounded join, shutdown
raises and service ownership remains held. A late sample returning after close
cannot dispatch another effect. Sampling failure invalidates the policy; a
slow sample is checked at its actual completion time rather than relabeled fresh.

The `thermal_serial_monitor` regression uses the real serial sender with scripted
thermal samples and the frozen command witness. Without calling `driver.advance`
or printer status, it waits for automatic standby, requests reheat, verifies
stabilization and consumes eligibility. It verifies four target-command sends,
completed durable effects, zero movements/physical opens and a stopped worker.
Additional tests cover provider failure, delayed observations, stalled shutdown
and competing monitor installation.

This Python worker is not a real-time heater watchdog and cannot guarantee timing
under process suspension or a blocked provider. Invalidating a policy is not
proof that heaters are off. Firmware protections and explicit stop evidence remain
required. This monitor is not yet connected to the public job tools or a fully
qualified streamed hot-print pause/resume lifecycle.

## In-place restoration planner

`InPlaceRestorePlan` binds the original and fresh pre-restoration checkpoint
hashes to an immutable command sequence. It requires unchanged position,
offsets, leveling, cursor, job/file/controller identity, millimeter units, T0,
and original thermal targets, with every continuity proof still true. Reheating
belongs to the thermal coordinator; this planner never emits heating or XYZ/E
movement. It rejects stale observations and cannot extend the saved deadline.

The sequence sets axis mode before extrusion mode (the pinned firmware clears
the E override on G90/G91), restores fan and feed/flow percentages, then issues
a feedrate-only G1. Integer fan values 0–255 and integer overrides 1–300 are
accepted; feedrate is limited to 60000 mm/min and a 32-character decimal literal.
These are software eligibility bounds, not machine/material qualifications.
Unsupported values are rejected without rounding.

Post-restoration comparison requires independently acquired evidence after a
completed execution barrier and exact original state. An acknowledgement is
insufficient. The adapter remains responsible for evidence provenance, exclusive
ownership, frozen stream, durable dispatch receipts and correlation of the
barrier with this plan. The planner itself sends nothing and grants no resume
authority. Its hash is an evidence identifier, not authentication.

Tests cover mode combinations, changed identities/position/targets, lost proofs,
stale evidence, expired pauses, unsupported numeric values and immutable plans.
The existing native mode harness checks pinned firmware mode semantics. Fan,
override and feedrate restoration have not yet been qualified through a full
hot streamed pause/resume. Parking and physical restoration remain unfinished.

`StreamWitness.authorize_restore` admits that exact plan plus an M400 barrier
and plan-specific echo marker. The same exclusive grant slot serves thermal
and restoration commands, preventing interleaving. Every command must traverse
queuing, queued, sending and sent in order with its private grant tag. Changed,
replayed or out-of-order commands revoke the grant; unexpected mutations and
stop commands revoke it too. Existing thermal-coordinator interruption also
revokes restoration permission. Normal job moves remain frozen throughout.

All six restoration mutations advance the stream epoch at queue and send time,
so the old checkpoint cannot authorize resume. Finishing a grant does not arm
resume. The adapter still must establish the execution barrier and fresh exact
state before accepting a new epoch; this host hook has no physical observation
capability. ModalTracker now records explicit feed/flow percentages for the
bounded restoration dialect. The original cold-job preflight continues to
reject override commands, preserving that profile's existing qualification.

## Native restoration handler evidence

`tests/native_restore_harness.py` compiles unchanged extracted functions from
our pinned firmware: mode bit operations, M106, M220, M221, G0/G1 destination
handling, fan assignment and planner flow-factor refresh. Twelve cases combine
all four axis/extrusion mode pairs with low, middle and upper accepted
fan/override/feedrate values. The feedrate-only G1 reaches the movement-preparation
stub with unchanged XYZE coordinates. A positive-control G1 with X changes that
coordinate, confirming that the stub's movement observation is exercised.
A stopped-controller control leaves feedrate unchanged because G0/G1 does not
execute. An acknowledgement or transmitted-mode record must therefore never
substitute for execution evidence.

This harness uses a deliberately small numeric parser and stubbed movement
preparation, with optional feature branches disabled and one non-volumetric
extruder. It does not test the complete Marlin parser, planner, interrupt layer,
physical output or all configured feature branches. The ordinary full firmware
build remains a separate check. Runtime-generated source manifests record the
file and extracted-snippet hashes; no firmware code is silently rewritten.

Marlin stores feedrate in float32 millimeters per second, so a requested
1200.25 mm/min is observed as approximately 20.00416756 mm/s. The native test
compares that representation with a float-appropriate tolerance. A future
physical checkpoint adapter must capture the firmware's canonical state; this
finding does not relax the existing exact checkpoint comparisons globally.

Pinned OctoPrint virtual firmware has feed/flow handlers but no separate M82/M83
or M106 handler, and uses one relative-mode flag for XYZ and E. Acknowledging
these missing commands cannot qualify restoration. Full hot-resume integration
must explicitly supply and validate those simulation behaviors or use a more
complete firmware simulator; current native tests do not fill that runtime gap.

## Opt-in simulator restoration extension

The `restore_serial` test scenario selects `MarlinModalMixin`; ordinary cold
scenarios retain upstream virtual behavior. This extension supplies separate
M82/M83 extrusion modes, clears that override on G90/G91, and models M106/M107
fan state. Linear moves snapshot mode, feedrate and overrides at queue time.
Later mode changes cannot reinterpret a previously queued move. Four coordinate
and extrusion combinations are compared with the extracted native handlers,
including float32 feedrate/coordinate arithmetic. Arcs, other tools and inch
moves are rejected by the extension's supported path. Motion delay is a test
fixture setting, not a physical timing or collision model.

The real sender serial test initializes the extension, freezes the witness,
executes the exact restoration grant, observes its marker and compares actual
simulator mode/fan/override/position state. Coordinates stay unchanged and the
old epoch still cannot resume the job. Identity and physical-continuity proofs
in this fixture are synthetic; no hot job is streamed or resumed by this test.
The extension is test-only and does not change public cold-job capabilities.

This integration exposed a marker length failure: the full plan hash exceeded
the pinned simulator's 64-byte receive buffer and produced a partial write.
The marker now uses `LLM_RS_` plus a 128-bit hash prefix, while the plan retains
its full hash. Its complete frame fits 64 bytes including a uint32 line number,
checksum and newline. Correlation is not authentication; partial-write failures
still latch uncertainty without automatic retries.

## Durable restoration receipts

`DurableRestore` records the plan, both checkpoint envelopes, exact commands,
marker and expected stream epoch before dispatch. It commits `dispatching`
before invoking the adapter. Dispatch happens at most once for that instance;
a partial write, failed admission, storage error or interruption invalidates the
attempt. Cancel/stop revoke the shared grant before their own dispatch, including
when storing the invalidation fails.

Completion requires the same live service owner/session, a frozen witness at
exactly the expected post-command epoch, the matching marker and fresh
post-barrier checkpoint evidence. It commits the observed checkpoint and barrier
before exposing `completed`. The serial restoration scenario now uses this
coordinator and verifies that dispatch intent is already durable at the write
callback. Its synthetic identity/continuity evidence remains explicitly scoped.

Startup invalidates every outstanding restoration's authority and converts a
`dispatching` receipt to `outcome_unknown`, without replay. An already completed
receipt retains its observation for diagnosis, but cannot authorize resume in a
new process. Tests exercise prepared/dispatched/completed database restart
states, partial dispatch, storage failures and interruption. These restoration
restart tests release the old owner and reopen the database; they are not new
process-kill or power-loss tests.

Restoration completion alone does not arm resume. The service now supports an
explicit backend handoff described below; the public cold backend has no hot
handoff implementation yet. There is no public restoration command or physical
transport. A completed restoration receipt is evidence, not a resume grant.

## One-use restored-resume handoff

For a completed restoration, OperationService can route `job.resume` to a backend
that explicitly implements `restored_resume_evidence` and
`dispatch_restored_resume`. Unsupported backends reject the request. The adapter
must independently establish the currently paused job, file/cursor, coordinates
and modal state, and return a fresh checkpoint and thermal observation. The
coordinator compares the checkpoint to the original pause, checks the exact
post-restoration stream epoch, and rechecks thermal continuity. It does this
both before claiming the request and again immediately before dispatch.

A consumed thermal policy remains subject to strictly increasing observations,
maximum evidence gaps, original pause duration, motor-hold lease, sensor proofs,
unchanged targets and actual nozzle/bed temperatures. Consuming eligibility does
not freeze those facts. A hot checkpoint cannot use the handoff without its
matching consumed thermal policy. Rechecking produces no new heater command or
second eligibility grant.

The resume operation ID and final checkpoint are persisted as
`resume_dispatching` before the backend callback. A failed or uncertain callback
is never retried. Its host command gate is closed, although already transmitted
moves may continue. Only a backend-observed completed resume transition can
retire the restoration as `resumed` and release the ordinary-operation gate.
Duplicate request IDs return the existing operation receipt. Restart turns an
in-flight resume into unknown, while retaining evidence; retired rows do not
block a later checkpoint, and old checkpoint IDs remain unique.

Current tests exercise this service/coordinator handoff with stubbed job
execution, including temperature loss between validation and dispatch, partial
resume dispatch, storage failure, repeated requests and interruption. Those unit tests do
not establish a streamed hot-print resume. The opt-in runtime experiment below
adds actual virtual paused-job/cursor evidence and a confirmed sender printing
transition; its physical limitations remain explicit.

## Streamed hot-job simulation

`hot_resume` selects an explicit test-only HotVirtualBackend and the opt-in
modal simulator. Staging accepts one fixed fixture file. That file sets a 200°C
nozzle target, 60°C bed target and fan 128, then streams 80 relative-extrusion
moves through the actual OctoPrint sender. Physical serial opens are denied.
The test pauses after observed progress, captures the actual file cursor and
reported/virtual coordinates, and freezes further job commands.

A continuous policy monitor enters standby, observes correlated target receipts,
requests reheat and requires stable scripted temperature observations. After
consuming thermal eligibility, the test performs durable serial restoration and
checks unchanged cursor/position. The service then reacquires actual virtual
state and thermal evidence at validation and dispatch, resumes the same sender
job and observes its printing transition. It verifies the original file's 81
G1 commands (one initial feed-only command plus 80 moves) execute exactly once
in order, apart from one separately accounted restoration feed-only command.
The job finishes at X80 / E80 and sends final zero heater targets.

The companion `hot_resume_temp_loss` injects a nozzle temperature of 180°C after
restoration. The resume request fails, the policy and restoration invalidate,
the sender remains paused and no additional job move executes. This is not a
heater-shutdown test.

Thermal actuals are scripted, standby starts after 0.2 seconds and stability is
checked over 0.1 seconds. Motor holding, undisturbed-part and sensor proofs are
virtual-fixture assumptions. No real nozzle heating, extrusion, adhesion,
long-duration pause, parking or physical recovery is qualified. The production
cold backend remains unchanged; hot hooks currently live only in the test
fixture. Further fault/reconnect variants and public tool integration
remain required before calling the complete hot workflow supported.

## Repeated pause lifecycle

`hot_resume_twice` runs two complete standby/reheat/restore/resume cycles inside
one fixed streamed job. It requires distinct checkpoint IDs and increasing file
cursors, and accounts for exactly two restoration feed-only commands outside the
original file sequence. Both cycles finish with closed monitors and retired
thermal coordinators/transports. Final job coordinates and zero heater targets
are checked just as in the single-pause experiment.

A consumed thermal policy cannot be replaced before its resume is confirmed.
Resume admission requires the old monitor to be closed and its worker stopped.
After a backend-observed resume, the backend retires its receipted thermal
transport; durable restoration completion then permits the service to clear its
monitor/coordinator references. Retirement failure invalidates authority and
keeps the gate closed. No monitor is joined while holding the backend guard.

Retired transports reject later writes and ignore late marker observations.
Retired coordinators reject advancement and cannot revoke another pause through
a late interruption callback. During the second actual serial pause, the test
tries the old transport and monitor and verifies their rejection does not change
the new stream epoch. Unit tests also cover premature replacement, an unclosed
monitor and failed retirement. This proves the bounded two-cycle software case;
it does not qualify hours-long physical pauses or motor/adhesion endurance.

## Faults during a hot software pause

The actual streamed hot fixture now also tests disconnect and controller reset
after a confirmed standby target effect. The continuous monitor invalidates the
policy, stale/current resume requests are rejected, and no further job movement
or reheat command follows. Reset may cause the sender's own handshake; that is
not treated as restored job authority. Connection generation changes and job
outcome remains unknown. Neither scenario claims heater shutdown or attempts
automatic reconnect.

A separate missing-receipt variant drops only the standby marker observation.
Both target writes still reach the virtual device exactly once. Timeout records
the effect as unknown, closes the monitor and rejects resume without replaying
the targets. Its post-invalidation write check begins at policy invalidation;
the complete pause-write trace independently checks exactly one M104/M140 pair.
No scenario induces a physical fault or qualifies physical recovery.
