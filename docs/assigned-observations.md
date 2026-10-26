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
