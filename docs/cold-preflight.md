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

The inspect input has the existing one-second source-age bound. This component
does not issue or renew inspections, and a model round trip cannot extend that
bound. The full controller-managed provider must acquire fresh inspections within
its journaled stage/start workflow; that coordinator is still unfinished. A later
checkpoint reader also needs an ordered inspection compatible with the completion
barrier, rather than injecting arbitrary reads into its strict write sequence.

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
