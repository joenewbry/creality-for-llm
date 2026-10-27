# Source-backed cold command screening

`ColdJobPolicy`, `cold_job_plan` and `AssignedColdPreflight` implement the bounded
nonprinting preflight component of the unfinished hardware provider. They replace
neither physical qualification nor the existing external evidence callbacks.
The original virtual preflight permits modeled extrusion and is not physical
cold preflight.

The explicit constructor policy supplies XYZ command-space bounds, maximum feed,
path length, nominal motion time, move count and reported temperature bounds.
There are no built-in bounds for the owner's printer. Its canonical hash binds
the exact policy, and nested input/output mutation cannot change it. These bounds
still need hardware/configuration qualification; a policy hash is not that proof.

The cold job is plain ASCII, at most one MiB, with bounded line lengths/counts.
It initializes G21, G90, M82, T0 and zero fan/nozzle/bed targets in the documented
order, then permits only absolute XYZ G1 moves with an explicit bounded feed and
standalone M400. E moves, homing, arcs, mode/offset changes, heater waits, positive
heat/fan targets, calibration/EEPROM writes, raw prompts and arbitrary host/display
messages are refused. All endpoints and the initial commanded position must fit
the convex command box; path/count/nominal-time budgets also apply. Nominal time
omits acceleration and waits and is not an upper bound on elapsed duration.
Command geometry does not verify clearance or physical position.

## Owned source and interface

Attach `backend.attach_cold_preflight(policy)` after the owned machine capture and
before sender start. A completed bound `prompt.inspect` supplies the input for
`job.cold_preflight({job_id, operation_id})`. This read uses the existing
AssignedStateReader and does not send commands or expose policy/path/raw-command
arguments. It rejects waiting/nonrunning firmware, unknown axes, relative positioning or nonmillimeter units,
nonzero offsets or leveling, queued planner blocks, overrides/fan changes and
unqualified reported temperatures. Logical flags remain controller beliefs.

After parsing, the reader rechecks the same original observation and its age.
Successful output includes job/hash, policy hash, capture/session/sequence and
original source time. It explicitly returns physical_readiness_qualified=false,
dispatch_authorized=false and missing external qualifications. The model cannot
turn this preview into hardware authority by changing returned data.

When enabled, stage validation/dispatch and start admission resample the cached
inspect input. An external stage preflight is still mandatory, and source age is
checked again after that callback. Failed checks withdraw the cached preview.
The completed stage receipt retains the screening input. The feature is optional
and the assigned adapter still reports physical_control_qualified=false.

## Scope and work remaining

The inspect input retains the existing one-second source-age bound. Opt in with
`attach_cold_preflight(policy, automatic_inspection=True)`. Stage/start admission
journals a child `prompt.inspect` beneath the running parent. The owned sender
emits that fixed query; competing prompt operations cannot replace it. A completed
correlated child receipt and fresh original state line are required before
screening, external checks and the job effect. The existing controller loop
advances admission without another model call. A slow model can retrieve the
resulting stage receipt later; it cannot refresh the original source clock.
Default explicit-preview mode remains available.

Child intent is committed before its single dispatch. It shares the parent's
deadline and binds job/hash/host/generation/session/capture. Duplicate requests or
lost in-memory workflow state never replay a claimed child. A receipt arriving
before its state line waits within that deadline. Stale state, changed inputs
and failed external proofs prevent the job effect. Source freshness/ownership
are checked after filesystem persistence or witness setup, before selecting or
starting. Interrupted job effects become unknown and are not retried. Restart
recovery leaves unfinished parent/child operations unknown.

Automatic admission invokes trusted external callbacks under the backend guard,
as synchronous admission already does. Those callbacks must be bounded; the
coordinator supplies no timeout that can preempt arbitrary external code. On a
late return it rechecks the original evidence age/deadline and refuses the job
effect. Runtime latency and callback behavior still need qualification.

This coordinates source inspection, not the full physical provider. A later
checkpoint reader still needs an ordered inspection compatible with the strict
completion barrier. Hardware/configuration and physical continuity proofs remain
required, with no physical qualification granted by this optional mode.

Tests use synthetic cold-ready wire values through the real assigned capture,
prompt channel, durable journal and router. An official Model Context Protocol
client exercises discovery, read calls and invalid-argument rejection over memory
streams. Existing native/sender tests remain regression coverage; they do not
qualify a coherent physical cold-job trajectory.

Hardware/firmware/configuration identity, actual stopped motion, sensor health,
motor power/hold, bed clearance and part continuity still require separately
qualified evidence. No physical profile is installed, either printer opened or
motion/heating/extrusion performed by this implementation. Mainboard photos and
Logitech work remain deferred. Printing after nozzle repair remains a separate
qualification plan.
