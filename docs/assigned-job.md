# Journaled assigned job and prompt adapter

`AssignedJobBackend` wraps the pinned assigned sender in the existing
`OperationService` and `ToolRouter` contract. Stage, start, pause, resume, cancel
and supported prompt operations share one journal, connection and writer. The
virtual backend's device guard is unchanged. There is no default physical
launcher or production hardware evidence provider.

## Staging and transitions

A stage request binds the job ID and original SHA-256. Validation and dispatch
both read bounded source bytes and run the provider's preflight. The adapter
selects a private read-only copy, capped at 16 MiB. Subsequent source edits do not
change the selected job. Changed staged bytes block start/resume, while an owned
cancellation remains available even if that file changed. A foreign sender cursor
cannot be cancelled as this job.

Intent is journaled before dispatch. Duplicate requests return their stored
receipts rather than selecting, starting or resuming again. A fresh request to
repeat a paused checkpoint is refused. Ordinary file-stream progress does not
change the control version and make pause requests perpetually stale; stream
changes while paused do invalidate the expected state and checkpoint.

Start/resume need an observed sender transition and typed external evidence.
Pause requires a complete fresh `PauseCheckpoint`, matching job/host/connection,
file/cursor, line number, reported position and transmitted modes. Its stream epoch
is frozen before the completed receipt. Missing/changed proofs produce unknown
outcome and no resume grant. Resume rechecks the checkpoint, expiry, staged hash
and stream epoch; a saved offset alone never authorizes continuation.

EOF and Operational callbacks alone do not establish job completion or a drained
cancel. Cancellation and final completion require their own external evidence.
Operation deadlines remain current-time limits. Expiry is unknown and never
claims a physical stop. Supported prompt operations may overlap a pending
start/pause/resume, so a firmware wait can be resolved without a second journal or
serial reader. Unknown/fault states do not authorize bind/respond recovery.

## Evidence-provider boundary

The controller supplies trusted, bounded provider methods:

| Method | Required evidence |
| --- | --- |
| `preflight(data, identity)` | Validate the entire allowed job dialect/profile; explicit True or rejection. This is code admission, not a physical readiness certificate. |
| `start_blockers(identity)` | Current independent readiness/clearance/material/configuration requirements; bounded blocker list. |
| `checkpoint(identity)` | Fresh `PauseCheckpoint` with independently acquired configuration, position, motor/part/sensor continuity and queue evidence. |
| `started(identity, operation_id)` | Evidence of the requested start beyond host sender state. |
| `resumed(identity, operation_id)` | Evidence of this resume beyond host sender state. |
| `completed(identity, start_operation_id)` | Exact-job final barrier/completion evidence; a print may last longer than its start-operation deadline. |
| `cancelled(identity, operation_id)` | Owned-job cancellation/queue/target-state evidence, not merely a cancel callback. |

Transition/completion methods return `AssignedJobEvidence`, not a bare boolean.
Its digest binds printer, connection, host session, job and file hash; phase,
operation ID, source, scope and observation time must match. Future, pre-dispatch,
wrong-job, wrong-operation and wrong-scope evidence cannot complete an operation.
These identifiers are references, not certificates. The provider must establish
actual provenance and facts. Cached pause position is a consistency check, not a
fresh independent measurement. The adapter reports
`physical_control_qualified: false` and exposes the provider's evidence scope.
Factories remain responsible for cleaning partial sender/transport construction.

## Qualification and remaining work

Software-terminal scenarios use the actual sender and the same journaled tools.
Two complete all 80 modeled moves once after pause/resume; one also clears a
compiled native firmware wait while pause is pending. A third cancels while
paused, and a fourth refuses an unqualified start before job dispatch. The source
file is changed after staging in these scenarios to exercise the immutable copy.
The test provider reads private virtual-peer state and labels its scope
`software_only_virtual_peer`. Its true motor/part/configuration flags are model
facts, not physical qualification. See `validation/assigned-job-stage123.json`.

A real provider with source-timed serial observations, independent physical
continuity and qualified controller/screen/configuration facts is unfinished.
Restart/reconnect, actual acknowledgment/resend/queue saturation, shutdown,
physical pause policy, setup/calibration, heated printing and camera association
still need integration/qualification on this path. Existing virtual hot-pause and
restart evidence retains its original scope. Exact development hardware/recovery,
sensor diagnosis and clearance precede commissioning. The protected printer and
existing acquisition/sender services remain excluded.
