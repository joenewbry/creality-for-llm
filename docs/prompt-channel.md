# Prompt transport channel

Status: experimental component, tested against the running native firmware
harness. It does not discover/open devices, acquire the serial owner lease,
journal operations, or expose an LLM tool. Those integration requirements remain.

`PromptChannel` accepts an already-owned writer and a connection generation.
Only one request may be outstanding. It assigns increasing request numbers,
marks the transaction pending before writing, and requires the writer to report
the exact number of bytes accepted. A short/unconfirmed write or exception is
uncertain, including an exception after a reentrant receipt callback. No command
is automatically retried.

The initial sequence is inspect, then bind against a fresh unbound snapshot.
A preexisting binding cannot authorize a reconnect, even if it matches the
requested nonce. Respond requires a bound session, a recent snapshot, the exact
current prompt, a shown button list, a valid choice and a supported reason.
These are transport checks, not permission to heat, purge, move or override a
physical requirement. The eventual backend must enforce the device profile and
operation policy before dispatch, and the service must journal mutations first.

Received bytes flow through `ProtocolObserver` and strict receipt correlation.
Normal `ok` and unrelated receipt numbers do not finish a transaction. The
matching receipt can establish protocol acceptance or rejection, never physical
completion. Rejection invalidates the cached snapshot and requires inspection
before another mutation. Timeouts, disconnects, controller reset, malformed
receipts, overflow and request-history uncertainty invalidate the session and
stop further dispatch on that channel. Late receipts cannot revive it.

Callers must continue polling `tick` while waiting so deadlines can expire even
without incoming bytes. They must call `disconnect` on loss of transport. The
writer must enqueue/write bounded bytes and return; physical completion must
not block this state lock. This is not a reconnect implementation or a durable
replacement for `OperationService` and its journal.

## Evidence

The complete suite passes 260 tests (`evidence/tests-stage12.xml`). The channel
test starts the actual compiled firmware runtime harness as a child process,
writes inspect/bind/respond bytes through pipes, and reads real firmware
receipts back into the channel. The `~` byte is a native-harness-only idle
scheduling hook; it is never part of the wire protocol. Physical dependencies
remain stubbed. The harness reports acceptance and an inactive prompt after the
response; it does not prove a physical wait or print recovered.

Additional tests cover simultaneous submitters, write failure/partial results,
reentrant callbacks, stale snapshots, wrong prompts/choices, rejection followed
by required inspection, existing bindings, deadlines, disconnects, malformed
records, overflow, reset and backward clocks. Neither physical printer was
accessed; firmware source is unchanged from the preceding integrated build.

## Observed fault inhibition (stage 20)

Faults and sensor-suspect events recognized by `ProtocolObserver` now invalidate
the session and cached prompt, latch the originating event, and set an explicit
invalidation reason. No later `ok`, valid prompt receipt or ordinary temperature
report clears this inhibition. The channel has no automatic fault-recovery or
reconnect operation. Its enclosing adapter must establish recovery separately.

This closes a gap where parsed faults were ignored by the prompt channel. Faults
before or after a response receipt in the same pump result prevent an unresolved
operation from completing successfully. A receipt already durably completed
before a later fault remains historical; current status still becomes uncertain
and further mutations are refused. This does not prove when a physical fault
began or retroactively undo a response that reached firmware.

Tests cover thermal runaway, heating failure, halted-controller reports, homing
failure, unknown Error lines and negative temperatures while idle, before a
receipt, after a receipt and split into individual bytes. Transport checksum,
resend and busy messages do not latch a machine fault. Native subprocess tests
inject a fault around a real firmware response and verify outcome_unknown,
visible diagnostics, durable duplicate results and refusal of another response
without another write. These are host reaction tests with injected serial fault
text; they do not exercise real heaters, sensors or the firmware thermal loop.
