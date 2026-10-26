# Assigned terminal sender integration

The assigned Linux connection now has one receive owner (`TTYReceiveHub`). Both
job-response lines and prompt observations originate from that same descriptor.
`TTYPromptBackend.open(..., copy_jobs=True)` enables the job copy at construction;
its normal default remains prompt-only and does not accumulate unused job data.
This is transport composition, not a complete job sender or commissioned printer.

## Shared connection contract

- All writes use `backend.frame_writer`: complete job frames and typed prompt
  frames share its existing frame validation and serialization.
- The writer delegates through the receive owner's gate. A receive failure and
  a queued write cannot race past each other. Partial/unconfirmed writes invalidate
  both receive lanes and are never retried.
- A sender calls `backend.receive_hub.job_readline()`. It returns a complete LF
  line or empty after one bounded poll; it never creates an acknowledgment,
  truncates a line, or adds a delimiter. Lines preserve their original bytes.
- The prompt adapter pumps timestamped copies through the same owner. A job
  consumer reading a receipt first does not consume the prompt copy or renew its
  age. The receive clock is sampled immediately after read, before later metadata
  checks. Both consumer paths revalidate ownership before releasing cached bytes.
- Queue overflow, malformed/non-ASCII input, EOF, descriptor failures and assignment
  loss invalidate the connection. No flush, reset, modem-line toggle, new port,
  background thread, reconnect or replay is introduced.

Read callbacks and identity checks must be bounded. The descriptor poll is at
most 0.1 seconds; the shared gate acquisition has a 0.2-second limit. These are
software bounds, not measured physical USB response guarantees. Queue capacity
is bounded, default 64 KiB per copy, and job line content is at most 2048 bytes.
An enabled job copy needs an active consumer; unread overflow is an explicit fault.
A receive fault is not evidence that motion or heaters stopped.

## Evidence

Unit checks cover concurrent consumers, fragmented lines, timeouts, overflow,
arrival age, cached authority loss, and dispatch fencing after receive failure.
A Linux software terminal connects the journaled tools to the actual compiled
pinned firmware handlers. The job consumer reads the final prompt receipt first;
the tool still completes from its own copy, all three protocol frames occur once,
and a subsequent receive closure prevents another writer attempt.

See `validation/tty-receive-hub-stage121.json`. No physical printer was accessed.

## Remaining production work

Attach the real sender's serial interface to these endpoints without adding
another reader or reopening the descriptor. Qualify its startup/handshake,
acknowledgment credit, resend policy, barriers, job staging/checkpoints, pause and
restoration, shutdown and process-death behavior through the same connection.
The existing virtual sender remains the qualified job path; no physical job tools
or default physical launcher are enabled here. Exact board/processor/screen and
recovery, sensor diagnosis, driver/device isolation and physical commissioning
remain prerequisites for actual printer access. Camera ownership is separate.
