# Assigned completion barrier

`AssignedCompletionBarrier` is an internal adapter primitive for an already owned
assigned connection. It persists an operation/job-bound unique marker before
queueing `M400` followed by `M118 E1 LLM_BARRIER_<token>` through the existing
sender. It does not open a device, expose arbitrary G-code to the model, retry an
uncertain command or authorize a physical pause/resume.

Attach it to the source recorder before the first shared-writer frame. One trace
observes both prompt and ordinary frames at the actual write boundary. It accepts
only the exact barrier/marker pair (including valid numbered frame checksums),
records write-start time and full-write confirmation, and rejects intervening or
subsequent frames. A receive can precede a write's return: its original source
arrival is retained, but no result is available until both writes are confirmed.

The marker must come from the durable assigned capture under the same lease,
printer, connection generation and capture ID, after the marker write started and
before the operation deadline. An `ok`, sender EOF callback, arbitrary caller
report or old marker supplies no completion evidence. Capture gaps, resets,
source/operation authority loss, stale arrival, repeated marker and interrupted
queue/write leave reconciliation required. The deadline and arrival age are
checked again after committing evidence. A new instance refuses a journal with
prior barrier intents; it never reconstructs or replays them.

The result is `source_correlated_firmware_barrier_marker`, explicitly with
`physical_readiness_qualified: false` and `resume_authorized: false`. Its useful
meaning depends on qualifying M400/M118 semantics on the installed firmware.
The maintained source's M400 calls Planner::synchronize, which waits on the
planner's full busy predicate rather than merely its block count. Firmware
identity, physical sensor/motor/part continuity and a checkpoint provider remain
separate requirements.

## Integration remaining

The primitive is implemented and tested against the real shared writer, owned
journal and source recorder using software endpoints. It is not yet connected to
the assigned job evidence provider or exercised as a new scenario through the
pinned sender. The existing job provider continues to identify itself as a
software fixture. Callers must hold their job control guard, supply the exact job
identity, and invoke dispatch with an internal enqueue adapter for that same
sender; no application or model input may supply that callable. Sender scheduling
and background traffic may make the deliberately strict pair fail qualification.
This is conservative unknown evidence, not permission to retry or bypass it.

Pause supports continuously powered, in-place continuation only after all
checkpoint proofs qualify. Power-loss recovery and opening the protected printer
are not enabled by this work. Logitech work remains deferred.
