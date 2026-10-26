# Sharing one job and prompt connection

Status: integration work in progress. The existing native prompt and OctoPrint
virtual job demonstrations use separate backends. Neither is a qualified physical
shared transport. Do not attach a second writer to a printer's serial port.

## Findings from pinned OctoPrint 1.11.8 and our firmware

The ordinary sender waits on clear-to-send acknowledgments and normally wraps
job commands with line numbers and checksums. Our bounded prompt grammar accepts
only exact unnumbered frames. Sending a token command through the ordinary queue
can both change its grammar and strand it behind the wait it must resolve.

OctoPrint's emergency-force helper is also unsuitable unchanged: it accounts for
an ordinary `ok` and calls its normal formatting path. `_do_send_without_checksum`
retries writes and treats some missing byte counts as success. The prompt channel
requires an exact write count and treats uncertain writes as unknown without
blind retry. A raw serial write beside that loop is not a qualified alternative:
partial writes could interleave with an ordinary job line.

On the firmware side, the primary UART previously delivered token frames to both
the prompt mailbox and the ordinary receive ring. Even though the emergency
parser recognized them, later normal queue processing could create an extra
ordinary acknowledgment. The new `HostPromptRxRouter` diverts exact `M876 V1`
candidates from the ordinary ring. It buffers at most seven prefix characters;
a mismatch releases them unchanged, and normal input then streams until LF.
Recognized malformed/oversized candidates remain isolated and get the mailbox's
protocol-error receipt. Screen traffic is not diverted. The mailbox and emergency
parser still see every original primary-UART byte, even when the normal ring is
full. No recovery work occurs in receive interrupt context.

This router recognizes the same boundary as the mailbox: exact `M876 V1` followed
by a space or LF. Numbered frames, leading whitespace, other versions and legacy
M876 remain on the ordinary path. Host writers must use the strict encoder. Ring
overflow policy is unchanged; the routing prefix introduces up to seven bytes
of ordinary-input buffering, not a guarantee against ordinary buffer overflow.

## Integration still required

1. One serial owner and one byte-level write arbiter must serialize complete
   ordinary lines and prompt frames, including partial writes. An uncertain write
   invalidates the connection rather than allowing a new frame to complete it.
2. A priority prompt lane must bypass a pending ordinary acknowledgment without
   changing ordinary line numbering, checksum history or acknowledgment credit.
   It accepts only strict token frames; it is not an arbitrary G-code escape hatch.
3. One receive path fans out correlated token receipts and observable faults to
   the prompt channel while preserving OctoPrint's ordinary protocol stream.
   Shutdown/reset/fault evidence must invalidate both job and prompt eligibility.
4. A combined backend must coordinate one journal/ownership boundary, expose
   machine events, and allow supported prompt recovery while a job operation is
   pending. Prompt resolution does not itself prove job resume or movement.
5. Test an injected generic wait during an actual streamed virtual job, bounded
   queue saturation, partial writes, resend, manual prompt replacement, concurrent
   cancel, disconnect and restart. Verify all moves once, ordinary acknowledgment
   accounting, stale-response rejection and no access to physical serial devices.

The firmware routing change is a prerequisite, not completion of these steps.
Physical port assignment, exclusive serial ownership and recovery qualification
remain separate gates before any hardware use.

## Complete-frame writer implemented (stage 24)

`SharedFrameWriter` now provides a common mutex around each complete byte-frame
write. Its prompt entry accepts only canonical `PromptWireCommand` frames;
the ordinary entry accepts one bounded ASCII line and excludes M876 commands.
The prompt decoder re-encodes the parsed command, retaining all existing field
range constraints. This is an internal transport boundary, not a replacement
for operation/profile validation or an arbitrary-command LLM tool.

The underlying writer is called once. Short counts, absent/non-integer counts,
exceptions and interruption latch transport uncertainty. Both lanes then refuse
new writes; neither retries the uncertain suffix nor lets another frame complete
it. The lock has a finite acquisition timeout. A caller timing out before it owns
the lock writes nothing, but does not cancel or infer the outcome of the current
owner. The underlying writer must itself have a bounded I/O timeout; Python's
lock cannot interrupt a driver that blocks forever.

The virtual-printer factory now routes all actual OctoPrint sender writes through
this boundary. Its MCP pause/resume and fault/disconnect scenarios pass with the
wrapper installed. A separate native dispatcher test uses its strict prompt
entry. Concurrent-writer tests use a deliberately paused partial underlying
write and verify no interleaved bytes. Additional tests reject malformed frames,
ambiguous counts, reentrant writers and dispatch after uncertainty.

This establishes the byte-write boundary in software. The priority prompt lane
is callable directly, but has not yet been combined with the sender's receipt
reader or native token-capable virtual firmware. Serial ownership, coordinated
close/disconnect, acknowledgment accounting, one combined backend and a prompt
in the middle of a streamed job still require integration. Do not infer physical
shared-connection readiness from these separate tests.

## Bounded receive copy implemented (stage 25)

`SharedReceiveBuffer.received_hook` returns the exact input object to OctoPrint
while queuing an ordered ASCII byte copy for one prompt consumer. It neither
injects acknowledgments nor consumes the sender's replies. The hook uses only
its own short-held lock, never a backend/channel lock or callback into service
code. Capacity is bounded; overflow, non-ASCII/invalid input or close permanently
invalidates the copy and discards queued authority. It cannot silently reset to
a new connection. Empty timeout reads consume no queue entries.

`PromptBackend` now converts read-path OSError into `receive_stream_unavailable`
uncertainty, clearing its prompt snapshot and preserving readable operation
receipts/status. This does not claim that the USB connection physically closed:
the loss may be in the host copy. A pending mutation becomes outcome_unknown,
and duplicate requests retain the durable outcome without another write.

The software sender fixture installs the copy after its initial connection
handshake, before any prompt authority exists. The bundled virtual printer emits
a deliberate non-ASCII reset-banner byte; a failed early-attachment test is
retained in `tests-stage25-receive-before-handshake-boundary.xml`. Unexpected
non-ASCII input after attachment remains invalidating. The recording fixture also
adds LF to virtual logical lines that lack it, modeling the actual firmware's
serial framing; this transformation is fixture-only, not in the receive hook.

Native prompt receipts now pass through the copy in a subprocess test. Real
OctoPrint job/fault integration checks preserve the sender's behavior and verify
copied progress/fault text. Concurrent producer/drain, byte-fragment ordering,
overflow, malformed input and close tests cover the bounded component. A unified
reader/writer lifecycle and token-capable virtual firmware feeding both backends
are still required before claiming shared-connection job recovery.

## Mid-stream native prompt experiment (stage 26)

Run the standalone integration fixture with:

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario native_prompt
```

It streams the same 80-move job through actual OctoPrint. At the MODEL_010 marker,
the fixture drains virtual moves and withholds that ordinary command's completion
until the compiled native prompt handler clears its generic wait. The host uses
the same SharedFrameWriter for ordinary and token frames; native receipts return
through the virtual serial reader, OctoPrint receive hook and SharedReceiveBuffer.
A small fixture bridge forwards token bytes to the native subprocess and forwards
its real receipts back. It never generates an ordinary ok for a token frame.

The test verifies ten completed moves remain unchanged for at least 0.25 seconds.
Typed inspect/bind calls finish while the normal command is awaiting its reply,
without advancing the sender's ordinary line number. A stale response is rejected
without a write or wait release. The valid response clears the native wait;
duplicates return the existing receipts, with only three token frames written.
The resumed stream then also passes the existing explicit job pause/resume checks
and completes all 80 movements and markers exactly once in order.

Evidence: `evidence/octoprint-virtual-service-native_prompt.json` and its serial
log; full suite `evidence/tests-stage26.xml` reports 363 passed. Physical serial
opens remain denied. This is a hybrid fixture: actual native prompt handlers and
actual OctoPrint virtual movement, joined at a synthetic marker. It does not run
the full Marlin planner or a real M0 on hardware. Native stdout scheduling uses
the existing test-only idle marker, never a physical wire command.

The test currently invokes job and prompt routers with separate test journals,
borrowing one virtual transport under the fixture's lifetime. It is deterministic,
not a new LLM demonstration. A single combined backend/journal, unified lifecycle,
cancellation/fault races and LLM tool surface remain required. The CLI rejects
using this scenario as a combined console until that integration exists.

## One-journal combined backend (stage 27)

`CombinedVirtualBackend` now composes the virtual job and prompt adapters under
one guard, context version, transport lifetime and OperationService journal.
Thirteen typed tools expose both operation families plus shared reads. Prompt
operations may overlap a pending job.start/job.pause/job.resume, but not another
prompt, stage or cancel. Ordinary job mutations retain serialization. Cancel
invalidates prompt authority; it still requires the job backend's own completion
evidence and does not promise to resolve every blocked-wait cancellation case.

The `--service --scenario unified_prompt` experiment deliberately requests pause
while the native wait blocks the stream. Inspect/bind/respond finish through the
same journal while pause is pending; then the pause records a verified checkpoint
and explicit resume completes all 80 movements once in order. Seven completed
operations are present in one event history. A synthetic sender-error callback
at the end also revokes prompt authority and prevents another job start. Other
existing fault tests retain actual virtual serial error/disconnect injection.

This exposed a pinned OctoPrint 1.11.8 scheduling race: M400 can be acknowledged
before its on_sent callback adds M114 to the job queue. The reader has already
found no work, and M114 remains queued until the position timer expires. Raising
the test timeout from 0.5 to five seconds did not fix it. Failed evidence remains
in `tests-stage27-before-sender-wakeup.xml` and `stage27-before-sender-wakeup.log`.

The local virtual-only `VirtualSender` subclass now calls the existing queue
transfer method after queuing a tagged pause/cancel position query. It neither
fabricates acknowledgment credit nor changes normal write formatting. The
integration fixture deliberately waits for real M400 acknowledgment before its
on_sent callback twice, covering pause and final completion. The original
0.5-second position timeout is restored; position evidence remains mandatory.
This is a local pinned-version integration adjustment, not an upstream fix claim.
Check maintained upstream source and contribution guidance before submitting it.

All 380 tests pass (`evidence/tests-stage27.xml`). The combined fixture remains
software-only and deterministic, with no combined MCP console or model run yet.
Cancellation during native wait, reconnect and full blocker coverage remain
unqualified. Neither physical printer was accessed or flashed.

## Stage 28 follow-up

The combined MCP console and actual model demonstration are now implemented and
verified; see model-demo.md for retained failed/successful evidence and exact
limits. The full suite passes 388 tests. The model completed native prompt
recovery and a qualified pause/resume in one job. The deterministic MCP test
additionally resolves the native prompt while job.pause is pending. Cancellation
during native wait, reconnect and full blocker coverage remain unqualified.

## Cancellation while the fixture wait is active

The combined backend attempts sender cancellation and then returns
`outcome_unknown` with `cancel_blocked_by_native_wait`, `stop_confirmed=false`,
and `requires_recovery=true` when the fixture wait is still active. This avoids
an indefinitely running receipt and explicitly disallows automatic resume. It
does not clear the prompt or prove a stopped device. The MCP regression checks
the blocked move count, duplicate receipt and refused resume. A real out-of-band
stop path remains unqualified and is not advertised as supported.

## Separate virtual stop path (stage 36)

The combined virtual profile adds `printer.stop` with empty arguments and the
usual identity/version/deadline/request envelope. It bypasses the ordinary job
queue with one complete `M112` frame through the same writer. There is no second
serial owner or raw-code tool. The adapter first invalidates prompt and resume
authority; further mutations require recovery. An identical request returns the
existing receipt. A different stop request cannot silently repeat the command.

Completion is restricted to `software_virtual_device_only`: the virtual killed
flag must be set, buffered movement must be drained, and all virtual heater
targets must be zero. Missing evidence at the deadline produces outcome_unknown.
The receipt always says physical_stop_qualified=false. The pinned virtual
printer's buffered-move worker does not itself stop on the killed flag, so this
is not an instantaneous emergency-stop model. It can finish already buffered
moves before the stop receipt completes. The blocked native-wait test begins
with ten completed moves and an empty movement queue and confirms no later moves.

A native prompt may remain logically waiting after the virtual stream is stopped;
this operation does not pretend to acknowledge or recover that prompt. The
combined status reports stopped only for verified virtual quiescence and rejects
resume. Actual controller shutdown, heater de-energization, motor state, restart
and physical recovery remain unqualified. Standalone job/prompt profiles do not
advertise this new operation.

## External controller reset (stage 39)

The virtual job adapter receives post-bootstrap serial lines through
`observe_transport_line`. An exact `start` banner enqueues a reset event without
taking the backend lock from the reader thread. Processing it changes the
connection generation, invalidates the checkpoint and latches a controller-reset
fault. A previously started job reports outcome_unknown even if the sender's
own reset handling subsequently reports ordinary cancellation. Old request IDs
still return historical receipts, but do not dispatch again; new commands require
recovery. The combined adapter also invalidates prompt authority when this job
connection generation changes.

This receive-hook wiring is part of the virtual launcher, not a qualified
physical serial implementation. Initial bootstrap banners are not treated as
unexpected resets. The actual virtual reset clears buffered commands, so executed
moves can contain gaps. The experiment records their indices and prohibits using
the old stream cursor for continuation. It does not implement reconnect, reset
recovery, or safe homing into an existing print.

## Resend scope (stage 40)

Two virtual integration cases now reject the next unexecuted numbered command
and request that same line again, reporting either checksum or line-number error.
They use the bundled virtual device's prepared-error callback before execution
and its real resend emitter. The real sender retransmits the exact requested
frame, then the job pauses, captures a valid checkpoint and resumes to all 80
moves/markers exactly once in order. This tests normal current-line transport
recovery, not corruption detection in physical firmware.

The first attempt used the bundled generic `trigger_resend_checksum` and
`trigger_resend_lineno` debug commands. Those request `lastN` instead of the next
unexecuted line. The transcript showed a previously executed G1 retransmitted;
the executed-movement comparison failed. Failed logs and test results remain
preserved. This is a distinct unsafe ambiguity, not a passing recovery scenario.
The host does not yet enforce a verified last-acknowledged-line boundary against
arbitrary old-line resend requests. Do not advertise arbitrary resend recovery
or use these software results to qualify physical relative movement/extrusion.

## Resend acknowledgment boundary (stage 41)

The virtual launcher now places VirtualResendGuard below the shared frame writer.
It records outgoing ordinary command slots before their bytes can be acknowledged,
tracks the last acknowledged numbered command, and excludes token-frame receipts
from ordinary acknowledgment accounting. Resend is accepted only for the single
outstanding, unacknowledged numbered command. Previously acknowledged lines,
unsent lines, missing baselines and multiple outstanding slots fail closed.
The profile expects one ordered acknowledgment per ordinary command and one
additional ok following a resend request; that additional ok cannot acknowledge
the retransmitted movement.

On violation, the guard latches a fault, prevents further ordinary writes and
reports an explicit host transport error to the sender before it can retransmit.
The receive copy retains the original device line. The sender closes and the
backend reports an unknown device outcome, preserving the diagnostic. A reset
cannot erase the guard fault. An exact M112 frame can pass this guard, but the
outer writer may still refuse it if transport write integrity has been lost;
no stop completion is inferred from the guard.

The previously failing old-line debug trigger now passes a rejection test: no
movement is duplicated, subsequent mutations are refused and original start
receipts are not replayed. Both legitimate current-line resend cases still
complete all 80 moves once through pause/resume. This supersedes the stage40
unresolved gap only for the pinned virtual profile. Physical firmware variants,
other acknowledgment conventions, lost acknowledgments and multi-command retry
windows remain unqualified; this is not general transport recovery support.
