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

## Correlated pause, resume and cancellation checks

An explicit `attach_completion_barrier()` call after capture attachment and before
`start()` enables fixed M400/unique-echo checks through the existing assigned
sender. Pause stays running until its pair and original captured marker qualify;
the external checkpoint provider remains required. The marker is rechecked after
the provider returns. Cancellation uses a separate operation-bound marker plus
its external completion proof. Correlation failure withdraws resume authority and
produces outcome_unknown, with no automatic replay. The capability
`pause_cancel_barrier_enabled` and `resume_completion_barrier_enabled` report
which optional checks are attached. A resume request stays pending while a new
marker and checkpoint are acquired, with no resume effect until they qualify.
The two exact read-only numbered frames may advance transport numbering; all
other checkpoint fields remain strict. Slow callbacks and file changes are
rechecked before dispatch.

The combined native-handler scenario still uses modeled movement and the virtual
printer's M400/M118 handlers. The hardware provider, installed firmware semantics,
physical continuity and the completion marker at file end remain unfinished. See
[completion check details](assigned-completion-barrier.md).

## Unqualified transport recovery

The assigned sender rejects every resend request and communication-timeout
handler invocation. It retires the shared receive/write owner before reporting
an error and closing the logical sender. It does not inherit automatic replay,
timeout probes, heat-wait completion or manufactured acknowledgment credit from
the pinned sender. The diagnostic and prompt lanes share the retirement fence.
Capabilities advertise automatic resend and timeout acknowledgment credit as
unsupported; the separate virtual resend tests retain their original profile.

A frame admitted before retirement can still have an unknown outcome. Retirement
blocks later gated I/O and never claims motion stopped or heaters became cold.
There is no automatic reopen, reset or queue replay. A new startup still needs
explicit prior-state reconciliation and physical qualification. Actual hardware
acknowledgment windows and a recoverable physical resend policy remain unfinished.

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

The optional [source-bound serial recorder](assigned-observations.md) now retains
actual sender line provenance and arrival age. A real provider with independent
physical continuity and qualified controller/screen/configuration facts is unfinished.
Restart/reconnect, actual acknowledgment/resend/queue saturation, shutdown,
physical pause policy, setup/calibration, heated printing and camera association
still need integration/qualification on this path. Existing virtual hot-pause and
restart evidence retains its original scope. Exact development hardware/recovery,
sensor diagnosis and clearance precede commissioning. The protected printer and
existing acquisition/sender services remain excluded.

When source capture is attached, a provenance-validated sensor warning or firmware
fault also retires the shared connection synchronously, before durable capture
completes. The backend then reports an unknown job outcome and invalidates resume;
no provider proof can restore that retired connection. See the source hazard
interlock in [assigned observations](assigned-observations.md).

## Owned sender and prompt-copy lifecycle

Attach `AssignedMachineEventRecorder` before calling `AssignedJobBackend.start()`.
Start verifies active capture and the existing controller lease, then starts the
pinned sender and one passive prompt-copy consumer exactly once. Neither start
nor the consumer discovers or opens a port. The consumer drains observations
without LLM tool calls and does not invoke the external evidence provider. It
uses the same backend guard as foreground operations. Once the sender claims
the reader role, it only drains prompt copies; the sender reads the descriptor.
During startup the existing hub may read before that handoff, serialized through
the same shared gate. No second descriptor or independent receive owner is added.

Capture loss, prompt-channel uncertainty or a consumer callback exception
retires shared dispatch and invalidates resume. Startup interruption is also
sticky: no retry/reopen occurs. Status exposes whether the consumer started,
whether its thread is running, its sample count and its bounded error category.
These are software-lifecycle observations, not physical readiness evidence.

Close signals and joins this consumer before closing the sender, durable capture,
transport and controller lease. The bounded lock-acquisition loop can observe
stop while `OperationService.close()` holds the backend guard. A consumer still
running at the join deadline prevents backend cleanup and retains ownership;
callers can retry close after it exits, never replay I/O. Transport metadata and
read callbacks must still meet their bounded contract. A callback stuck while
holding the backend guard can delay a service close reaching the join; ownership
is retained throughout, and no shutdown deadline or physical stop is promised.

The older direct-sender experiments remain historical. The journaled assigned
job/prompt terminal scenarios now use this lifecycle rather than a test-owned
prompt-draining thread. An idle-report scenario sends more than one copy-buffer's
capacity while making no LLM tool calls; the source report remains observable
without overflow. Native physical transport/provider/commissioning remain open.

Both receive copies may observe a warning first. The consumer preserves an
existing retirement cause, or maps the prompt observer's sensor/firmware cause
to the same source-hazard category used by capture. It cannot replace a specific
sensor warning with a generic consumer failure. Source history retains its own
line provenance independently of which observer reaches the fence first.


A capture attachment now also exposes `printer.machine_state` through an
`AssignedStateReader`. It reads the optional maintained-firmware state report
for a completed inspect operation, without issuing commands. These are source-
correlated controller variables; the external physical job provider and all
physical checkpoint proofs remain required. See [state evidence](machine-state.md).
