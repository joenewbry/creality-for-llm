# Typed tool interface and virtual console

Status: working software-only prototype. The default transport is newline-delimited
JSON. The native prompt console additionally supports official-SDK Model Context
Protocol (MCP) stdio with `--mcp`; see `mcp.md`. There is no network listener or physical-device
selection option. The default job profile is cold. The explicit hot profile
adds simulated thermal control and managed pause/resume; the combined profile
adds a native generic prompt. See [current profile launch instructions](mcp.md).
Parking, physical control and power-loss recovery remain unqualified.

## Native prompt tools

Run the standalone native prompt console with the main Python environment and a
local C++ compiler:

```sh
.venv/bin/python tests/native_prompt_console.py
```

It builds the shared native firmware harness in a temporary directory and uses
pipes only. The default persistent journal is
`evidence/native-prompt-console.sqlite`; `--journal PATH` selects another one.
Missing parent directories are created. EOF closes the child process and releases
the service lease. The console has no serial-device, arbitrary-binary, raw-command
or network-listener argument. Native physical dependencies remain stubbed.

Start with this line on stdin:

```json
{"id":"describe","tool":"printer.describe","arguments":{}}
```

Use `printer.status` for the request identity/generation/state version, then
submit `prompt.inspect`, poll its receipt with `operation.get`, submit
`prompt.bind`, and finally `prompt.respond` for the observed prompt and choice
zero. Each submitted operation needs a fresh request ID and a future epoch-time
deadline. Poll rather than assuming the immediate `running` receipt is complete.
Discovery labels this fixture `one_generic_user_wait` and provides its compiled
harness hash. It is separate from the OctoPrint job console.

`PromptBackend` can be passed to the same `ToolRouter` and newline-JSON `serve`
transport. Its discovery response exposes eight tools, including observation history:

- `prompt.inspect`: send a versioned snapshot query and return a journal receipt.
- `prompt.bind`: bind the fresh session, with no extra arguments.
- `prompt.respond`: submit the exact prompt generation and choice zero.
- `printer.describe`, `printer.status`, `operation.get`, `printer.events`, `printer.observations`.

All three prompt operations use the standard durable request envelope, including
identity, connection generation, state version and deadline. Inspect sends bytes
and is therefore explicitly submitted as an operation; status only observes
available data. The response schema permits no raw G-code or extra arguments.
Job tools are neither advertised nor accepted by this backend. The OctoPrint
backend now exposes fourteen tools, including bounded home/jog and observation history, and does not advertise prompt
support it cannot execute.

Prompt status exposes snapshot observation time, age and freshness separately
from channel state. Physical recovery remains explicitly unqualified. Discovery
does not certify the injected transport: physical identity, exclusive serial
ownership and action-profile qualification are still required from the enclosing
adapter. No physical launcher or autonomous model integration is provided here.

Validation: 272 tests pass (`evidence/tests-stage14.xml`). JSON envelopes run the
full native-process inspect/bind/respond sequence through the router, service,
journal, channel and actual firmware handler. Tests also verify durable duplicate
receipts, operation-event ordering, cached status without writes, stale snapshot
reporting and rejection of unsupported job/raw/temperature tools. Neither
physical printer was accessed.

The standalone-console checkpoint passes 273 tests (`evidence/tests-stage15.xml`).
A separate CLI process executes the complete JSON lifecycle, rejects a raw
command, closes on EOF, and restarts with a fresh native connection. Historical
completed receipts remain queryable without rebinding or replaying their command.
The shared builder is `tests/native_harness.py`; pytest and the console compile
the same actual firmware source. This does not yet demonstrate autonomous model
selection of tools, MCP, combined job/prompt operation or physical USB behavior.

## Run the console

Install the pinned OctoPrint environment separately, then run:

```sh
.venv-octoprint/bin/python tests/octoprint_virtual_smoke.py --console
```

The default journal is `evidence/virtual-console.sqlite`; `--journal PATH` selects
a separate persistent journal whose parent already exists. One process may own
that journal. At startup, unresolved operations become outcome unknown rather
than being replayed. EOF closes the virtual transport. A saved receipt does not
authorize continuation after process restart.

The console loads one generated cold simulation artifact named `simulation-job`.
It starts idle, without staging or printing it. This is a test console, not a
production daemon or a general file-upload service. Physical PySerial opens are
denied before the sender is constructed, and its sole factory accepts VIRTUAL.

Send one envelope per line; stdout contains one JSON response per envelope.
Diagnostics go to stderr. For example:

```json
{"id":1,"tool":"printer.describe","arguments":{}}
{"id":2,"tool":"job.inspect","arguments":{}}
```

`printer.describe` returns the actual supported tools and strict JSON schemas.
`job.inspect` supplies the current printer identity, connection generation,
state version, job ID, file digest and resumability reasons. Do not reuse a state
version after another operation or a state transition.

For a mutation, use the tool's schema and current observation. The outer `id`
correlates the transport response; the nested `request_id` is the durable
idempotency key. Set a finite future Unix timestamp as `deadline`. Repeating the
identical nested request returns its existing receipt; reusing its request ID
with different data is rejected.

## Available tools

| Tool | Meaning |
| --- | --- |
| `printer.describe` | Implemented capabilities and tool schemas; does not promise planned features. |
| `printer.status` | Current virtual sender and job state, identity, freshness and resumability. |
| `job.inspect` | Same current job observation, including digest and resume blockers. |
| `job.pause_status` | Read-only pause workflow phase, checkpoint deadline, monitor state, latest thermal/restoration receipts and resume gate. |
| `printer.home` | Home XYZ in the cold simulator before its first job; no physical homing claim. |
| `printer.jog` | Bounded XYZ movement after virtual home; see [setup controls](setup.md). |
| `job.stage` | Validate the registered artifact and expected hash, then select a private immutable copy. |
| `job.start` | Start only the explicitly staged job. Completion of this operation means streaming started, not that the job finished. |
| `job.pause` | Request pause; persist the checkpoint with completion evidence. |
| `job.resume` | Validate the saved checkpoint and command witness, then resume the cold in-place virtual job. |
| `job.cancel` | Cancel with virtual queue-drain/zero-target completion criteria. |
| `operation.get` | Reconcile and return a durable receipt for its operation ID. |
| `printer.events` | Paginated operation-receipt events. This is not yet the complete machine-event stream. |

No tool accepts a raw command, USB port or arbitrary filesystem path. The staged
copy isolates an active job from later edits to its registered input. The cold
virtual preflight rejects unsupported commands, heated targets and simulator
mode combinations that do not match the pinned firmware.

## Verification and limits

The integration runner passes all service calls through this JSON transport,
including stage/start, pause/resume or cancellation, and completion observation.
It changes the original file after staging and still verifies every intended
simulated move. It retains tool transcripts alongside serial evidence.

Current coverage also includes malformed/duplicate JSON keys, oversized input,
non-finite numbers, unknown fields/tools, raw-command rejection, event pagination,
duplicate mutations and a separate console-process smoke test. These checks do
not constitute a deployed MCP server, an LLM-model evaluation, a hardware
qualification, or the complete planned interface. Further maintenance tools, full
machine events, real artifact catalog management and physical integration remain
required work. Later MCP/model evidence is recorded separately in `mcp.md`,
`model-demo.md` and `progress.md`.

## Durable observation history

`printer.observations` accepts `{after, limit}` with an independent sequence
cursor and returns `scope: polled_state_observations`, `continuous_capture: false`,
`observations` and `next_after`. Each entry contains a sequence, host recording
time, source tool (`printer.status`, `job.inspect` or `job.pause_status`) and the exact returned
snapshot. Snapshot timestamps and freshness flags remain unchanged; recording
an old snapshot does not refresh its evidence. Reading history does not poll the
backend, append a snapshot or issue commands.

Every successful status/inspection result is committed before returning it to
the caller. Repeated polls are retained, which allows deliberate paused intervals
to be audited. Host recording sequence orders the history even if sensor or host
wall-clock timestamps differ. Records survive reopening the operation journal.
The schema migration adds a separate observations table without modifying old
operation receipts. `printer.events` retains its original receipt-only contract;
its cursor must not be used for observation queries.

Queries accept 1–1000 records and stop earlier at 256 KiB of snapshot JSON. A
snapshot itself is limited to 128 KiB. Continue from `next_after` until the next
page is empty. This is a per-response bound, not a retention policy; disk usage
still grows with polling. Continuous transport/state callback capture, rotation
and retention controls remain work before a production service release.

Current bounded setup operations and their qualification limits are documented in [setup.md](setup.md). The combined virtual backend exposes eighteen tools.

## Pause workflow inspection

Call `job.pause_status` with `{}`. It sends no printer command and does not
advance the thermal policy, refresh a sensor sample or consume resume eligibility.
Its schema-version1 snapshot is retained in the ordinary polled observation
history. Prompt-only backends do not advertise this job-specific read tool.

The `phase` distinguishes `paused`, `holding`, `entering_standby`, `standby`,
`requesting_reheat`, `reheating`, `thermal_ready_restore_required`,
`restoration_required`, `restoration_prepared`, `restoring`, `restored`,
`resuming`, `inactive` and `recovery_required`. The thermal and restoration
objects separate active in-process state from `durable_latest` history. A
completed historical receipt after restart never becomes active authority.

`resume_request_available` means a resume request can enter the appropriate
validation path. `resume_gate: fresh_recheck_required` explicitly requires new
checks at dispatch. The snapshot always has `resume_authorized: false`; reading
it grants nothing. Monitor state, pending operation IDs, blockers, checkpoint
expiry and latest effect outcomes explain why progress is unavailable.
`backend_resume_blockers` preserves the ordinary backend's view; the explicit
restored-resume handoff has its own additional validation path.

Receipt summaries omit raw command sequences and device paths. This tool returns
only the latest durable pause summaries, not an unbounded event history, and
labels its capture as polled. Hot-job mutations are available only in the explicit
`hot_console` simulator profile described in [the MCP guide](mcp.md).
