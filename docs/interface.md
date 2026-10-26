# Printer control interface version 1

Status: implementation contract. Capabilities become available only after corresponding validation; this document does not imply they are all implemented.

## Layers and authority

The LLM calls typed tools. A deterministic host service validates the request and holds the exclusive USB connection. Firmware enforces machine limits and exposes state and prompts. The screen remains an observer and manual control surface, including stop access.

The physical development profile forbids heating and extrusion. A simulator profile supports virtual heating/extrusion without any serial device access. Never infer physical readiness from a simulator result. Live tests require a separate identity-bound assignment; there is no auto-select-first-device behavior.

## Tool surface

Read tools: `printer.describe`, `printer.status`, `printer.events`, `printer.machine_events`, `job.inspect`, `operation.get`, `prompt.inspect`, `calibration.inspect`.

Mutation tools: `job.stage`, `job.start`, `job.pause`, `job.resume`, `job.cancel`, `prompt.respond`, `printer.home`, `printer.jog`, `printer.set_temperature`, `printer.set_fan`, `printer.stop`, `printer.reset`, `calibration.set_temporary`, `calibration.persist`.

Maintenance and heating tools remain unavailable on the clogged-printer profile until their applicable prerequisites are established. Persistent calibration is a distinct operation and never a side effect of jogging. No unrestricted G-code tool is exposed.

Each mutation requires `request_id`, `printer_id`, `connection_generation`, `expected_state_version`, `deadline`, `operation` and a strictly validated `arguments` object. Unknown fields, non-finite numbers and wrong types are rejected. Device identity is not interchangeable with a USB port string. The same request ID with different content is an error.

Each result includes an operation ID and one of `accepted`, `running`, `completed`, `rejected`, `failed`, `outcome_unknown`. Duplicate identical requests return the durable receipt. Journal acceptance before emitting bytes; after a crash reconcile unresolved operations and never replay ambiguous motion. `ok` means command acceptance/processing at the protocol level, not necessarily physical completion. Completion criteria are operation-specific.

Read responses include `observed_at`, `state_version`, `connection_generation`, source and confidence. Position is commanded/estimated unless independent measurement exists. Expired telemetry becomes stale; a disconnect makes physical state unknown. Heater targets, actual temperature, command queue status and job status are independent fields.

## State and transition rules

Connection: `unassigned -> disconnected -> identifying -> connected`. A mismatch transitions to `identity_rejected`. Any connection loss invalidates prompts, readiness and physical completion claims and advances the connection generation on reconnection.

Job: `none -> staged -> starting -> running -> pausing -> paused -> resuming -> running -> completing -> completed`. Cancellation passes through `canceling` until shutdown/motion disposition is verified. A fault becomes `faulted`; uncertain execution becomes `outcome_unknown`. New jobs require current bed clearance, material/nozzle profile and known machine state. Reconnect never implies automatic resume.

Prompts: `assembling -> open -> response_requested -> resolved`, or `expired`/`superseded`/`outcome_unknown`. A prompt is not actionable until the firmware finishes announcing its buttons. Give every prompt a new ID scoped to connection generation. Never reuse a response for a later prompt with identical text.

Only proven response types are exposed. Unknown prompts remain visible with no automated responses. Runout, refill and purge require physical evidence and cannot bypass the sensor; thermal and homing failures remain faults. Requesting an acknowledgment is not proof the wait ended.

Stop requests must not wait behind a blocking G-code request. Even an emergency parser cannot guarantee mechanical stop latency: measure firmware and physical response separately. A disconnected stop is `outcome_unknown`, never `completed`.

## Firmware protocol gaps requiring implementation

Stock host prompts have no wire-level prompt token. A host-generated ID alone cannot prevent a race where a manual screen action changes the firmware prompt before an old response arrives. Add a firmware prompt-generation token checked when a response executes, with explicit accepted/rejected and resolved events. Preserve token integrity across resets through a session handshake; host reconnection invalidates prior tokens.

Expose a versioned structured status/event protocol with machine-readable fault reason, current blocking state and allowed actions. Do not parse localized screen strings as authority. Correlate Creality-specific touchscreen paths with these events. Keep ISR-level parsing bounded; perform recovery work in the normal firmware loop. Hardware safety never depends on the LLM or host being responsive.

## Required test evidence

See the goal's blocker matrix. Cover stale prompt races, duplicate requests, expired deadlines, host crash between journal/write/ack, port reordering, unknown prompts, queue saturation and reset during movement. Separate parser tests, real firmware logic harnesses, serial emulator tests and physical qualification.

## Implemented service foundation

`OperationService` acquires a process lease next to the canonical journal path
before recovering unfinished operations. A second owner cannot recover or
dispatch against the first owner's journal. The persistent lock file must never
be deleted; replacement is detected. Forked children cannot use the inherited
lease or unlock the parent's ownership. This advisory lock is separate from
the required exclusive USB-device ownership.

The backend's state lock encloses observation, request validation, journal claim
and dispatch. Dispatch must enqueue and return without waiting for physical
completion. The service returns a running receipt until a fresh, same-identity
backend observation supplies an explicit terminal outcome. A changed connection
invalidates pending outcomes; stale observations remain pending. Dispatch errors
are uncertain and never automatically replayed. Backend shutdown must succeed
before the service releases ownership to a successor.

`OctoPrintVirtualBackend` connects this foundation only to the bundled software
printer. It supports pause/resume/cancel for a cold, in-place profile, refuses
any physical transport, and captures a complete checkpoint for that limited
virtual profile in the pause receipt. Missing position data returns outcome
unknown. Cancellation completion requires
the virtual sender to be operational, simulated moves drained and heater targets
zero. These completion rules do not qualify a physical printer.

`StreamWitness` hooks record queued and transmitted commands. Checkpoint capture
freezes out-of-band mutations at both queue and send boundaries. An attempted
mutation invalidates eligibility; emergency stop commands remain available.
Resume arming checks the witness epoch atomically and admits only tagged job/file
commands while retaining the external-command gate. All service writers are
serialized; the hosting integration must exclude independent raw writers that
could bypass hooks or spoof sender tags. No remote raw-command tool exists.


## Virtual job faults (stage 21)

The virtual adapter retains OctoPrint's first error callback as `blocking_fault`
with bounded message/reason, a host observation timestamp and source. It clears
the pause checkpoint and inhibits stage/start/pause/resume even if later sender
callbacks suggest running or completed. `automatic_fault_recovery_supported` is
false. The adapter does not infer fault clearance from a later operational state.
An unresolved ordinary operation becomes outcome_unknown when a sender fault
arrives. Cancellation remains eligible for normal validation, but an offline
sender cannot produce a successful cancellation receipt.

On connection loss, the job's current outcome is unknown even when the fault
report is known. The status preserves both facts: `job_state: outcome_unknown`
and the diagnostic. A prior completed pause receipt remains historical; it does
not authorize resume on the lost connection. This profile still has no physical
transport or recovery/reset operation.

The executable `--service --scenario thermal_fault` fixture stages, starts and
pauses a real OctoPrint software job, injects the virtual printer's Marlin fatal
error, and observes the sender close its connection. Typed status exposes the
fault, resume/cancel attempts are rejected, and independent movement records
show no continuation. The same assertions now cover the existing disconnect
fixture. Full suite: 323 passed (`evidence/tests-stage21.xml`). Fault text is
simulated; actual firmware thermal shutdown, physical stopping distance and
heaters are not exercised. A combined generic-prompt recovery during a running
job remains unimplemented.


An opt-in managed resume adapter can bind a single `job.resume` operation to
thermal preparation, a fresh in-place restoration, and the existing one-use
resume handoff. The operation remains running throughout preparation; replaying
its request ID returns the same receipt. `job.pause_status` reports its phase
and operation ID without advancing it. Deadline or preparation failure closes
the resume gate and records an uncertain outcome; it does not claim heater
shutdown. Cancellation interrupts the worker before dispatching cancellation.
The worker shuts down before the controller releases ownership.

This adapter is currently implemented only by the fixed hot virtual test
profile. Its temperatures and continuity proofs are simulated. It is not an
available physical-printer feature or a power-loss recovery mechanism. Normal
MCP adapters still require their existing qualified profile and do not silently
acquire this capability.


Interruption coverage now includes cancellation and service shutdown during
managed reheating. Both stop and join the preparation worker and monitor;
raising the simulated nozzle to its saved target afterward does not restore or
resume the job. Shutdown releases the controller lease only after worker and
backend closure. Its outstanding operation remains in the durable journal for
restart reconciliation, rather than being falsely marked complete.

The fixed hot virtual adapter now adds a bounded heater-off sequence to
`job.cancel`: nozzle target zero, bed target zero, a queue barrier, and a unique
completion marker. This internal one-use grant remains usable when stale pause
workers revoke their own authority; it permits no heating or motion and cannot
arm resume. Dispatch still follows the service's durable operation claim.
Completion requires the marker, drained model moves, an idle sender, and zero
nozzle/bed/chamber targets. Missing confirmation at the request deadline returns
`outcome_unknown`, requiring recovery. A command acknowledgment alone is not
completion. The cold adapter behavior is unchanged.

This is virtual-only cancellation qualification. Controller shutdown still
means worker/transport closure, not verified heater shutdown. Physical stopping
distance, real sensor readings, emergency handling and loss of connection need
their own qualification before exposing a hardware hot profile.


The explicit `hot_console` simulator exposes the same typed tools through
Model Context Protocol (MCP), using the official client/server SDK and real
OctoPrint virtual sender. Launch it with:

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --mcp --scenario hot_console --journal /tmp/hot-demo.sqlite
```

Use a new journal for an independent demonstration; an existing interrupted
journal preserves recovery requirements. The fixture automatically installs its
thermal monitor after an observed completed pause. It uses accelerated synthetic
temperatures and continuity proofs, advertised by job.inspect. The client only
calls stage/start/pause, pause_status, resume or cancel, and operation.get; no
client command sets simulated temperatures or bypasses the resume gates.
The client regression verifies duplicate requests, standby, complete ordered
job continuation and verified cancellation targets in separate runs. This is an
external software demonstration profile, not a general hardware runtime.


The hot simulator also exposes `printer.stop` with empty arguments. It revokes
pause/resume authority and sends M112 through the sole complete-frame writer,
bypassing the ordinary command queue. Completion requires the extended virtual
device's killed flag, drained model moves and zero heater targets. Lost stop
frames return outcome_unknown at the request deadline. Stop requires recovery;
it is distinct from cancellation and never offers automatic resume.

The bundled OctoPrint virtual device's M112 implementation only sets a killed
flag. Our explicit Marlin simulator extension additionally sets heater targets
to zero, modeling the pinned Marlin kill()->disable_all_heaters() call. This
model is not evidence of real shutdown timing, stopping distance or sensor state.
Stopping while paused/reheating is tested, including through an external MCP
client. Buffered active-motion and actual firmware M109 stop integration remain
separate qualification work.

Do not implement active heating-wait cancellation by clearing wait_for_heatup
alone: returning to the scheduler can release queued work and leaves targets
unchanged. A future graceful firmware cancellation must close admission, discard
queued job work, resolve planner disposition, disable heaters, exit the wait and
report independently observed state. The emergency-stop path provides a separate
terminal action; it does not turn a stopped print into a resumable one. No new
firmware heating-cancel command or physical adapter is enabled by this demo.


The extended hot simulator adds `printer.set_fan` with strict arguments
`{"fan":0,"pwm":128}`. The pwm integer ranges from0(off) to255(full requested
output); booleans, fractional values, other fan indices and extra fields are
rejected. It is available only while idle before the first job, with heater
targets zero and no buffered moves. Job/pause fan changes remain disallowed.

The command is journaled and idempotent. Completion requires the unique barrier
marker and matching simulator-internal fan state, not an acknowledgment alone.
Missing state times out to outcome_unknown and latches recovery. Fan adjustment
preserves the existing position proof. Inspection labels the value as simulated
and reports physical_fan_qualified false; no tachometer or actual airflow is
claimed. The ordinary cold virtual profile does not advertise this tool because
its bundled device does not model fan state. No physical profile is enabled.


The extended hot simulator exposes `printer.set_temperature` with arguments such
as `{"heater":"nozzle","target_celsius":180}` or
`{"heater":"bed","target_celsius":50}`. Targets are integers: nozzle0–250°C,
bed0–90°C. Other heaters, negative/fractional values, booleans and extra fields
are rejected. The operation is idle-before-first-job only, with drained moves,
one modeled nozzle and no chamber target. Positive targets require finite,
profile-bounded actual-temperature observations; zero targets remain available
with an invalid sensor reading unless another recovery gate already blocks setup.

Completion means the unique barrier was observed and the simulated target
matches the request. It does not wait for temperature convergence or authorize
printing. Receipts report actual temperature separately and always set
`temperature_readiness_verified:false` and `physical_heating_qualified:false`.
Status exposes actual/target pairs, replacing nonfinite/unknown readings with
null. Missing target confirmation expires uncertain. Motion and fan setup still
require zero heater targets; paused/active job temperature edits are rejected.
The ordinary cold profile and physical development profile do not enable this
tool. The separate stop operation remains available for terminal shutdown.


Calibration is available only in the extended software simulator. The read-only
`calibration.inspect` returns current/saved steps-per-millimeter, whole-settings
fingerprints, dirty-state and recovery flags, plus default values and explicit
adjustment bounds. `calibration.set_temporary` takes axis x/y/z/e, steps_per_mm
and expected_settings_sha256. Values must be finite, at most six decimal places,
and within90–110% of the simulator's fixed defaults. The operation changes RAM
through M92, invalidates position proof, and verifies the disk settings stayed
unchanged. It does not home, move, extrude or save.

`calibration.persist` takes only expected_settings_sha256. It explicitly invokes
M500, then requires the unique completion marker plus matching entire RAM and
saved-file contents. A stale fingerprint or settings changed outside the
controller is rejected. The full settings snapshot is checked because M500 saves
more than the selected steps value. Timeout or partial dispatch becomes uncertain
and blocks later setup/jobs. A prior unknown calibration receipt also blocks the
profile after controller restart; there is no automatic recovery or replay.
Both mutations require idle-before-first-job, heaters off and drained moves.

The virtual EEPROM is a file in the simulator's temporary directory. Verified
saving means persistence in that file, not a physical EEPROM write, power-failure
durability or mechanically correct calibration. The tests reload its values and
keep bounds anchored to factory defaults, avoiding cumulative bound expansion.
Physical calibration, offsets/leveling and recovery commissioning remain separate.


## Continuous serial observations

The hot software console exposes `printer.machine_events` with `after` and
`limit`, independently of operation receipts and polled snapshots. A bounded
receive-hook queue copies the sender's original input; a worker parses and writes
ordered records into the same controller-owned journal. Capture starts after
service ownership and bootstrap, rather than implying coverage from power-on.
Replies and acknowledgment accounting are unchanged, and the hook performs no
database writes or backend callbacks. Read-only access never dispatches commands.

Each record carries a global journal cursor, capture ID, source generation,
source timestamp, kind and parsed data. `generation_scope: protocol_observer`
means that these generation IDs are diagnostics; they must not be substituted
for the backend identity used in mutation requests. Resets invalidate the
observer's prompt/capability state. No record supplies recovery authority.

Default retention is 10,000 events, pending receive capacity is 256 KiB, and
pages are capped at 1,000 records and approximately 256 KiB of encoded payload.
`history_gap` identifies a cursor older than retained history; `cursor_ahead`
identifies a cursor beyond this journal's latest record. Neither field claims
uninterrupted physical coverage. New captures have new IDs and explicit start
records. Normal shutdown drains pending data, invalidates locally observed
prompts with reason `capture_ended`, and records closure. This does not assert
that firmware ended the prompt or disconnected. An unterminated final line
(including an overlong line still being discarded) produces `truncated_line`
and latches `incomplete_serial_line`; no newline, acknowledgment or temperature
is synthesized. Final observation errors, prompt invalidation and capture loss
are committed together when the database remains writable. A capture
failure stops capture and exposes health failure, with a loss record when the
database remains writable. A disk failure may prevent that loss record itself.

Queue overflow, invalid observations and persistence failure are latched rather
than silently clearing the queue and claiming success. This is an observational
facility: it cannot resume, dismiss a fault, or establish that a machine stopped.
The caller must stop/detach capture and join its worker before releasing the
controller lease; a close timeout reports that ownership is still needed.
An unclean process death can lose queued observations, and the next capture must
be treated as a discontinuity. This is not a lossless hardware audit guarantee.

The integration is currently limited to `hot_console`; cold/native-prompt tools
retain their existing scope. Physical USB ownership, OS isolation, commissioning,
controlled reset/reconnection and continuous physical telemetry still need work.
