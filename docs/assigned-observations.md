# Source bound assigned serial observations

`AssignedMachineEventRecorder` attaches to one owned `AssignedSerialEndpoint`
before its sender reader starts. It copies the pinned sender's received-message
hook into the controller journal without changing the reply. It introduces no
serial opener, second descriptor reader or physical launcher.

Each complete line carries a monotonic source sequence, printer identity,
connection generation, byte count, SHA-256 and the original ASCII line. Arrival
means host read return, before subsequent metadata checks. A fragmented line
retains its oldest constituent byte's arrival time; later queued lines retain
their own segment timestamps. Delivery and durable persistence cannot refresh a
stale report. Firmware emission time and device latency remain unknown.

The hook checks the actual sender endpoint, owner process, exact reader Thread
object, next source sequence, identity and original bytes. Modified, replayed,
skipped or foreign-source lines retire capture. The bounded worker records raw
lines and parsed events with the same provenance. Byte/item overflow, durable
write failure and source loss are explicit. Retention exposes cursor gaps.

`printer.machine_events` reads this history after attachment to
`AssignedJobBackend`. `printer.status` and `job.inspect` include `serial_reports`:
temperature, reported position, firmware identity, faults and sensor suspicions.
`arrival_fresh` describes the age of a received report. Units, homing state,
planner completion, sensor validity and independently verified physical position
are not inferred from `M105` or `M114`. Controller reset clears previous reports
and changes the protocol observer generation; provenance keeps the original
connection generation so the two identities are distinguishable.

Capture failure or early capture closure invalidates attached job authority.
An assigned resend request or communication timeout also retires the shared
connection; the original received resend line remains in this history.
Shutdown stops the sender, closes and drains capture, then closes the transport
before releasing the controller lease. Capture timeout retains that lease.
Physical printer shutdown is a separate operation; recorder closure does not
claim stopped motion or cold heaters.

Software-terminal scenarios exercise these hooks through OctoPrint 1.11.8 and
journaled job/prompt tools. Their transition/checkpoint provider still reads
virtual-peer fields and explicitly labels its evidence software only. This
recorder supplies source-bound controller reports; it does not supply the missing
physical evidence provider, configuration/continuity qualification, thermal
policy, restart recovery, actual USB driver isolation or camera association.
Neither printer nor camera is opened by these tests.

## Source hazard dispatch interlock

A second bounded instance of the same protocol parser classifies each original,
provenance-validated line synchronously before the sender receive hook returns.
A `sensor_suspect`, firmware `fault`, or nonfinite-temperature parser error
retires the shared connection immediately. Durable recording remains asynchronous;
a stalled journal worker cannot keep dispatch authority alive after classification.
The raw line and parsed event are still queued for the durable history. The
interlock records the source sequence and original arrival time in
`serial_reports.dispatch_inhibit`.

This is a one-way withdrawal of authority. Normal temperatures do not certify
sensor health, clear the inhibition or permit reconnect/resume. Both job and
prompt writes share the retirement fence. A frame already admitted before the
fence may have an unknown outcome, and buffered motion/heating may continue on
the printer. No stop/reset/emergency command, automatic retry or physical
shutdown claim is made. The existing negative-temperature classifier is specific
to this room-temperature development profile; different environments require a
qualified sensing policy. Checksum diagnostics remain transport observations;
the assigned sender's separate resend/timeout fence still applies.

This closes the gap between source hazard observation and later host dispatch.
It does not provide the missing independent physical evidence provider or qualify
the production hardware adapter. An initial firmware `start` remains a reset
observation rather than a thermal hazard; this interlock does not change startup
or reset qualification.

The [qualification record](../validation/source-hazard-stage131.json) separates
the initial native-pause fixture failure, its correction and the installed
Linux software result. It makes no physical commissioning claim.


## Correlated controller variables

The maintained candidate now has an optional session/request-bound state report.
[Controller-state evidence](machine-state.md) describes its parser, source
interlock and typed `printer.machine_state` read. A completed prompt inspect
receipt alone is insufficient: current source/capture, matching receipt, request
window and original arrival age are also required. The report provides actual
firmware variables, not the missing physical job-evidence provider or checkpoint
proofs. No device was accessed for this software delivery.
