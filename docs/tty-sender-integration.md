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
Both enabled copies must be drained independently of LLM tool-call frequency.
The future sender attachment needs a coordinated prompt-processing loop; this
module creates no background consumer. A receive fault is not evidence that
motion or heaters stopped.

## Evidence

Unit checks cover concurrent consumers, fragmented lines, timeouts, overflow,
arrival age, cached authority loss, and dispatch fencing after receive failure.
A Linux software terminal connects the journaled tools to the actual compiled
pinned firmware handlers. The job consumer reads the final prompt receipt first;
the tool still completes from its own copy, all three protocol frames occur once,
and a subsequent receive closure prevents another writer attempt.

See `validation/tty-receive-hub-stage121.json`. No physical printer was accessed.

## Pinned sender attachment

Sender attachment qualification uses one explicit 115200 endpoint and an isolated
OctoPrint 1.11.8 runtime. The subclass bypasses both plugin serial factories and
the default pySerial opener; automatic discovery and reopening are refused.
The endpoint implements only this pinned sender contract. Logical timeout values
are stored for its communication deadlines, while readline returns complete LF
lines after a bounded transport poll. Baud changes, raw prompt commands through
the job lane and partial frame retries are refused. Sender close retires both
receive copies; the controller retains the descriptor and lease until cleanup.

The sender takes the descriptor-reader role once; prompt processing becomes a
passive copy consumer. Both copies still require coordinated draining. The gate
uses bounded FIFO admission so a polling reader cannot repeatedly overtake a
waiting writer. Metadata authority checks run inside this gate, rather than racing
an independently held descriptor lock.

A software-only integration uses the actual sender over a Linux owned terminal
to the OctoPrint virtual firmware peer. Both runs pause/resume and complete all 80
original modeled moves once. One run also clears a compiled native firmware wait
through journaled prompt tools while sender pause is pending. Native prompt
requests are deduplicated. Physical serial opens and discovery/fallback factories
are denied for the entire experiment. Job methods in these tests are sender calls,
not journaled job tools; no physical job backend is supplied or commissioned.

Real production integration still requires a job backend with immutable staging,
state/sensor/configuration gates, transport observations, qualified checkpoints,
shutdown and restart handling, plus fixed isolated settings/hooks/scripts. The
existing virtual backend deliberately refuses this endpoint. No default launcher
or existing printer service changes. Board/processor/screen/recovery, sensor,
clearance, actual USB driver/device isolation and camera qualification remain open.
