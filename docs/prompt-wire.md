# Experimental prompt wire protocol

Status: experimental receive/dispatch/receipt path integrated in the selected
STM32 F401 development build, with native tests. Unflashed and physically
unqualified. The existing host tools do not yet expose `prompt.respond`.

## Command grammar

Commands are ASCII with exactly one space at the shown separators and a single
LF terminator. Hexadecimal digits are lowercase and fixed width. Line numbers,
checksums, comments, CR, tabs, extra fields and alternate whitespace are rejected.
These commands require a dedicated sender integration; they must not be passed
through an ordinary numbered/checksummed G-code sender unchanged.

| Operation | Command before LF |
| --- | --- |
| Inspect | `M876 V1 I Qrrrrrrrr` |
| Bind session | `M876 V1 Bssssssssssssssssssssssssssssssss Qrrrrrrrr` |
| Respond | `M876 V1 Rssssssssssssssssssssssssssssssss Ppppppppp Scc Qrrrrrrrr` |

`s` is a nonzero 128-bit session. `p` is a nonzero firmware prompt generation.
`c` is the zero-based choice encoded in two hex digits. `r` is a nonzero 32-bit
request correlation number. Parsing establishes syntactic validity only; the
firmware handler separately validates the session, current prompt and action.
The longest line is 65 bytes before LF. Never truncate a command to this limit.

`printer_control.prompt_wire.PromptWireCommand` validates typed fields and
encodes commands. `host_prompt_wire.h` parses without allocation, refuses
partial output updates on invalid input, and contains a fixed-size frame decoder.
Oversized/non-ASCII frames are discarded through the next LF. An incomplete
frame has no result, even if it contains all fields but lacks the terminator.
Each input stream requires its own decoder; reset it on connection replacement.

## Firmware runtime and receipts

`LLM_PROMPT_PROTOCOL` enables the development integration. Build checks require
STM32 hardware UART, host actions/prompts and emergency parsing, with distinct
host and screen ports. The actual STM32 receive callback feeds only the primary
host UART into `host_prompt_inbox.h`, even if the ordinary receive ring is full.
The screen port cannot submit protocol frames. The receive path only collects
bounded bytes; parsing, response execution and output occur in the main idle
loop. A critical section copies the mailbox and overflow latch before handling.
A reentrancy guard prevents nested idle calls from redispatching a response.

The mailbox preserves its first pending frame and reports overflow separately.
Malformed candidates never partly update a command. Ordinary G-code and legacy
emergency commands do not occupy the protocol mailbox. Processing preserves
legacy M112 and M410. M108 cannot release a wait while a protocol frame is
pending/processing or after token mode is bound; legacy M876 is already refused
by the bound-session check.

A receipt has this fixed format, followed by LF:

```text
//llm:v1 Qrrrrrrrr Ccc Dd Tssssssssssssssssssssssssssssssss Ppppppppp Aa Hh Bbb Rrr Ww
```

`Q` is request correlation; `C` is the result code; `D` marks a cached duplicate.
`T` is the bound session; `P` is current prompt generation; `A` is active; `H` is
shown; `B` is button count; `R` is the firmware PromptReason value; `W` is the
firmware user-wait flag. Flags are 0/1;
other fields are fixed-width lowercase hexadecimal. Snapshot fields describe
current prompt state, including on a duplicate receipt, not historical state.

| Code | Meaning |
| --- | --- |
| 00 | Inspect/bind success or response-handler acceptance |
| 01–09 | NoSession, WrongSession, NoPrompt, NotShown, StalePrompt, InvalidChoice, Exhausted, UnsupportedReason, PhysicalActionRequired |
| 80 | Malformed frame; Q is zero |
| 81 | Mailbox overflow/lost protocol traffic; Q is zero |
| 82 | Same request number with different content |
| 83 | Request number older than the last executed mutation |

Mutating request numbers must increase without wrapping. The last identical
mutation can be retried and receives its cached result without repeating the
handler. Inspect does not consume the mutation sequence. An unbound response
cannot poison the sequence before binding. Ordinary Marlin `ok` does not mean
protocol success. Handler acceptance and an inactive prompt do not prove motion
completion or successful physical recovery.

## Remaining integration and qualification

Binding permits one session per firmware boot; another nonce is rejected. This
prevents delayed binds from switching authority back to an old session. It is
an initial limitation, not a complete reconnect solution: the host must refuse
automatic continuation after disconnect and must not reset a printer to recover
access. Fresh-session reconnect negotiation and durable host reconciliation
remain required work. Nonces provide correlation, not authentication.

Strict host receipt decoding and observation are now implemented below. Add
capability discovery, journal/transport/tool integration, prompt event streaming
and explicit physical-resolution evidence.
The build is scoped to the selected STM32 UART configuration, not every Marlin
port. Complete the wait/fault matrix and hardware recovery qualification before
flashing. Measure actual interrupt timing and stop latency separately.

## Evidence

`tests/test_prompt_wire.py` sends Python-encoded bytes through the actual C++
frame decoder/parser and actual `host_token_response` in the native harness.
The stdin dispatcher is test glue, not the firmware runtime. Physical
dependencies are stubs. Cases verify valid binding/response, wrong session,
stale generation, invalid choice, duplicate reply, maximum integer widths,
every truncation of a response, forbidden suffixes, non-ASCII bytes, oversized
frames, recovery at LF and incomplete final frames. No physical USB is opened.

The complete suite passes 199 tests (`evidence/tests-stage8.xml`). No new
hardware-ready firmware or end-to-end USB protocol is claimed by this checkpoint.

The subsequent mailbox checkpoint passes 200 tests (`evidence/tests-stage9.xml`).
Its native test compiles the unchanged inbox header with AddressSanitizer and
UndefinedBehaviorSanitizer. It exercises every frame split, two interleaved
ports, first-frame preservation under overflow, invalid-frame recovery,
reconnection reset and consumption while the next frame is assembling. Calls
are sequential simulations of producer/consumer schedules; this does not prove
actual interrupt exclusion or hardware timing. Mailbox size is compile-time
bounded to at most 144 bytes per port. No printer was accessed.

The integrated runtime checkpoint passes 209 tests (`evidence/tests-stage10.xml`)
and builds for F401 (`evidence/prompt-runtime-f401.log`). Native tests execute
the actual receive/process/receipt functions and extracted UART callback with
physical dependencies stubbed, checking duplicate/conflict/stale requests,
unbound rejection, overflow and port routing with a full normal ring buffer.
Actual emergency-parser tests verify pending/bound M108 gating while M112/M410
remain available. These are not physical USB or interrupt-timing measurements.
Build identity and unflashed artifact: `evidence/prompt-runtime-build.json`.

## Host receipt observation and correlation

`PromptWireReceipt.decode` validates the exact fixed grammar, known result and
reason values, correlation flags and snapshot consistency. It rejects unsupported
versions, malformed widths, normalized whitespace and incomplete records.
`ProtocolObserver` emits a connection-scoped `prompt_receipt` event for valid
records; invalid records are protocol errors. Normal G-code `ok` remains a
separate event. Observation does not enable a capability or make stock prompts
safe for remote response.

`assess_prompt_receipt` compares a decoded receipt with the original command,
connection and deadline. Handler success is only `accepted`, never physical
completion. A different request is unrelated; ordinary correlated firmware
errors are rejected. Session mismatch on acceptance, changed connection, expired
evidence, uncorrelated transport errors and request-history conflicts produce
`outcome_unknown`. In particular, rejecting an old retry does not prove that
its earlier execution failed. The assessment performs no dispatch or replay.

The host checkpoint passes 245 tests (`evidence/tests-stage11.xml`). Tests feed
actual native firmware receipts byte by byte through the host observer and
correlator, including duplicate acceptance and connection reset. Other cases
cover all receipt truncations, malformed fields, inconsistent snapshots,
overflow/conflict uncertainty and deadline boundaries. Firmware is unchanged
from the integrated runtime build above; neither printer was accessed.

The journal-adapter checkpoint adds required `W` wait-state evidence to this
unreleased experimental receipt format. Earlier unflashed artifacts without W
are rejected by the current host decoder. F401 build and artifact identity:
`evidence/prompt-journal-f401.log`, `evidence/prompt-journal-build.json`.
The host adapter qualifies only the firmware user-wait state, not physical
print recovery; see `prompt-backend.md`.

Stage 23 diverts recognized token frames from the primary UART's ordinary RX
ring. They still enter the bounded mailbox and emergency parser, but cannot
later create ordinary queue input and an extra ordinary acknowledgment. Legacy,
numbered and nonmatching frames retain their ordinary path; the screen port is
unchanged. See shared-transport.md for exact boundaries and the remaining host
integration. Actual-header and extracted-UART tests cover byte splits, prefix
mismatches, oversized/malformed candidates, mailbox overflow, screen isolation
and a full ordinary ring. All 324 tests pass; the F401 candidate rebuild succeeds.
Artifact identity is in `evidence/stage23-rx-route-build.json`. It is unflashed.
