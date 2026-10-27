# Source-bound controller state

The optional maintained-source firmware report supplies actual controller
variables for the future physical job-evidence provider. It does not replace
that provider or qualify physical readiness. The candidate is unflashed.

The new firmware feature `LLM_MACHINE_STATE` is advertised by M115. After a
successful session bind, the existing journaled `prompt.inspect` command produces
a separate `//llmstate:v2` line carrying its request and bound session. The line
contains firmware uptime and sequence, homed/trusted and logical motor-enable
flags, commanded native position, workspace/home offsets, relative modes,
leveling, unit factor, feedrate, feed/flow overrides, fan and temperature variables.
`firmware_running` means Marlin's `isRunning()`, not evidence of a running job.

The feature is off in ordinary firmware profiles and on in the maintained LLM
candidate. The getter only copies scalar values under a short critical section;
serialization happens afterward. It does not synchronize the planner, issue a
command, change settings, release a wait, move an axis or heat a component.
The fixed lowercase-hex grammar uses binary32 float bit patterns. Native tests
compile the actual getter and wire sources against hardware stubs. F401 build
and native-source evidence are separate from physical qualification.

## LLM tool sequence

1. Bind the supported prompt session through the existing journaled tools.
2. Submit `prompt.inspect` using the usual request identity/state/deadline fields.
3. After that receipt completes, call `printer.machine_state` with its
   `operation_id`. This read emits no serial commands. A report still in transit
   or awaiting durable capture returns `correlated_state_report_pending`; no report
   by the inspect deadline returns `state_report_unavailable_for_inspect`.

The read is exposed only after assigned source capture is attached. It checks
the durable completed inspect request and receipt, current prompt snapshot,
printer/connection/session/request identities, original host-read arrival time,
source capture, journal lease and deadline. State older than one second, an old
prompt transaction, capture loss, wrong identity, replay, invalid values or reset
cannot supply a usable observation. Metadata checks cannot refresh its age;
clock and age are checked again after the final source check. Reading never
returns resume or physical readiness authority.

`AssignedMachineEventRecorder` preserves the original line and provenance in its
history. Malformed, replayed, changed-session or nonfinite state reports retire
shared dispatch synchronously; negative room-temperature sensor reports retain
the existing sensor interlock. No automatic stop, reset, replay or shutdown claim
is added. A sender action already admitted may still have an unknown outcome.

## Meaning and remaining work

Homed/trusted/enable flags describe firmware beliefs. They do not independently
verify mechanical position or driver power. Commanded position may be ahead of
queued movement. Zero planner blocks is not an operation-correlated barrier or
proof of stepper inactivity. Uptime wraps and is not a host timestamp. Host-read
arrival time does not establish ADC sample age or sensor health.

This report is not a full configuration/image fingerprint: mesh, probe geometry,
volumetric/retraction and every optional feature are not captured. Part/material
continuity still needs its own evidence. The actual job provider must combine
qualified configuration, barriers, safe preflight and physical qualification;
it cannot copy these report values into true checkpoint proofs. Existing
`AssignedJobBackend` still reports physical control unqualified. Stock firmware
without this feature cannot supply this observation. The protected printer stays
excluded, Logitech work stays deferred, and no device is opened by these checks.

See [scoped validation](../validation/machine-state-stage139.json). The maintained
firmware candidate and the host's older firmware-test pin remain distinct.


## Live inactivity counters

State schema V2 adds the activity-reset origin, M84 stepper interval and M85
maximum inactivity interval, the compiled XYZE idle-axis mask, and the actual
production timeout predicates sampled at the report uptime. The host checks
unsigned 32-bit elapsed arithmetic against those predicates; malformed or
contradictory reports synchronously invalidate the owned source. The existing
request/session/sequence/arrival checks apply unchanged. V1 remains readable
with inactivity coverage explicitly null. V2 adds no commands or timer resets.

The elapsed value is modulo 2^32, not a wall-time interval or wrap count. A zero
stepper interval can have a true raw predicate while MarlinCore separately
requires nonzero interval, no queued blocks, no reset inhibition and eligible
SD state before shutdown. A zero M85 interval disables its predicate. Idle-axis
mask zero means no supported idle-axis timer; it is not a guarantee of motor
power. Full no-sleep/pause policy, actual motor/position/part continuity, complete
configuration and installed binary identity remain unqualified. No countdown
or readiness grant is inferred from these values.

The pinned native fixture now includes the production G-code timeout methods
and uint32 elapsed helper, alongside the real getter and serializer. Eight
getter configurations and boundary/wrap/zero cases run with hardware stubs;
the integrated software-terminal scenario reads the V2 timers through the
actual assigned sender/recorder/tool path. See runtime-timers-stage151 validation.
