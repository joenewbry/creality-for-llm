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

## Sender integration

`AssignedJobBackend.attach_completion_barrier()` opts in after source capture is
attached and before the sender starts. No new model tool or raw command argument
is added. When the sender reaches Paused, the backend queues the fixed pair
through that same sender and keeps the pause receipt running until its original
source marker and complete-frame writes qualify. Only then does the external
provider attempt the checkpoint. The marker is checked again after that callback,
which may have blocked; stale or intervening evidence cannot become a checkpoint.
The cancellation path similarly waits for its own operation-bound pair and
retains the separate external cancellation proof requirement. A refused enqueue
or failed correlation produces outcome_unknown and withdraws resume authority;
repeating the request cannot submit the pair again.

Resume now stays in admission with the sender paused until a new marker for the
journaled job.resume request qualifies. It compares a new checkpoint with the saved one, rechecks the
staged file after the provider returns, checks the source marker again, arms the
stream guard and checks source authority/arrival/deadline once more before the
single resume effect. A delayed, missing or unqualified reply cannot release the
file stream. The normal resumed-transition proof is still required afterward.
Its final receipt retains the historical admission marker.

Read-only completion queries can consume numbered transport frames. The writer
records their original line numbers; admission permits exactly those two known
frames to advance the saved transport counter. It does not adjust file cursor,
position, modes, configuration, offsets, leveling or physical proofs. Unexplained
line-number changes refuse resume. The saved checkpoint is not rewritten.

The new owned-terminal scenarios exercise powered pause/resume of all 80 original
modeled moves once, a separate cancellation marker, an interleaved command, and
maintained native prompt/state handlers combined with the barrier path. Native
sampling uses hardware stubs, and movement plus M400/M118 are provided by the
pinned virtual printer. This is not a run of Marlin's physical motion planner.
The ordinary checkpoint provider still reads private virtual-peer fields and
identifies its scope as software-only. The marker does not replace motor, sensor,
configuration or part-continuity qualification.

Sender scheduling or background traffic may make the deliberately strict pair
fail qualification. This is unknown evidence, not permission to retry or bypass
it. Construction remains opt-in while firmware/profile qualification and a
physical provider are unfinished. EOF completion still uses the existing external
provider; integrating a final marker needs an explicit lifecycle evidence window,
separate from the earlier job.start dispatch deadline. Fresh source-correlated
resume admission is implemented; physical continuity remains separate from both the new marker and a historical pause checkpoint.

Pause supports continuously powered, in-place continuation only after all
checkpoint proofs qualify. Power-loss recovery and opening the protected printer
are not enabled by this work. Logitech work remains deferred.
