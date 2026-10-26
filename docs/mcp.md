# Native prompt MCP adapter

The native prompt console can serve Model Context Protocol over stdin/stdout
using the official Python SDK, pinned here to `mcp==2.3.0`. The SDK handles the
protocol session; the adapter routes its tools through the existing deterministic
router, durable service and native firmware harness. No network listener,
physical serial connection or client configuration change is made.

## Current software profiles

For a fresh checkout, first follow [reproducible setup](reproduce.md), including
its pinned firmware preparation. Use the combined locked environment below.
All profiles use stdin/stdout, expose structured tools and refuse physical serial
connections. The later sections retain historical stage-specific evidence and
tool counts; discover the current inventory with `tools/list` or
`printer.describe` rather than relying on those historical counts.

| Profile | Launch arguments after `tests/octoprint_virtual_smoke.py` | Qualified software behavior |
| --- | --- | --- |
| Cold job | `--mcp` | Fixed job lifecycle, bounded home/jog, cold in-place pause/resume |
| Combined job and prompt | `--mcp --scenario unified_prompt` | Cold job with a compiled native generic prompt, correlated response and stop |
| Hot job | `--mcp --scenario hot_console` | Fixed hot job, standby/reheat, managed restoration/resume, cancel/stop, bounded fan/temperature and temporary/saved calibration |

For example:

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py \
  --mcp --scenario hot_console --journal /absolute/path/hot.sqlite
```

Use a distinct journal for each profile. The hot server uses scripted temperatures
and synthetic continuity proofs. It does not include an injected native heating
wait or prompt, and it accepts neither a device path nor an arbitrary print file.
Its ordinary pause budget is 20 seconds; `--model-fixture` uses a 120-second budget
and slower moves for model latency. Neither duration qualifies a physical pause.

Stage and start the fixed job, request `job.pause`, and poll its receipt. Then use
`job.pause_status` to observe standby and `resume_request_available`. A single
`job.resume` request owns reheating, mode restoration, barrier verification and
resumption. Poll that receipt, then keep observing `job.inspect` until the whole
job is completed. A completed resume receipt does not mean the job is finished.
The legacy `job.inspect.resumable` flag stays false for this managed hot profile;
`resume_authorized` also remains false in a read-only snapshot because the
mutation must revalidate current evidence.

`job.cancel` and `printer.stop` have separate receipts and completion proofs.
Process exit alone is not a confirmed heater shutdown. Setup and calibration are
limited to the idle period before the first job; they cannot modify a paused
job's saved state. See [interface details](interface.md) and the
[recorded model-driven hot demonstration](model-demo.md).

## Install and run

Keep the optional SDK dependencies separate from the firmware build environment:

```sh
uv venv .venv-mcp --python .venv/bin/python
uv pip sync --python .venv-mcp/bin/python requirements-mcp.lock
.venv-mcp/bin/python tests/native_prompt_console.py --mcp
```

The base JSON console still runs without these dependencies. To connect a local
MCP client, set its command to the absolute `.venv-mcp/bin/python` path and its
arguments to the absolute `tests/native_prompt_console.py` path followed by
`--mcp`. An optional `--journal /absolute/path/operations.sqlite` selects a
separate persistent journal. This repository does not automatically edit client
settings or start a background server.

The server exposes the seven native prompt tools, with the same request schemas
and durable operation semantics as the JSON console. It returns structured data
and a JSON text representation. Invalid names/schema arguments return MCP tool
errors before router dispatch; schema publication alone is not relied upon for
validation. The adapter deliberately does not request model sampling, expose
arbitrary commands, or advertise job control on the prompt-only backend.

A response receipt means the requested protocol operation finished. A supported
prompt response additionally includes firmware wait-state evidence, explicitly
excluding physical recovery qualification. Follow `operation.get` while an
operation is running and retain request IDs for duplicate-safe retries. Client
cancellation does not mean a previously claimed action was undone; consult the
journal before retrying. Stdio/process shutdown closes the native child and
releases service ownership.

## Verification and limits

The official SDK client initializes a real server subprocess, discovers tools,
executes inspect/bind/respond, verifies duplicate receipts and operation history,
and rejects unknown/raw tools and an invalid response choice. The complete
suite in the isolated SDK environment passes 274 tests:

```sh
.venv-mcp/bin/python -m pytest -q tests tests_mcp
```

Evidence: `evidence/tests-stage16-mcp.xml`. The earlier failed schema-boundary
check is retained in `evidence/tests-stage16-mcp-before-validation.xml`; explicit
JSON Schema validation fixes it. Native compiler/firmware dependencies remain
stubbed. No model inference, physical printer access or complete printing
workflow is claimed. Job/prompt combination, autonomous model demonstration,
physical qualification and release/contribution work remain open.

Implementation references: [official low-level server documentation](https://py.sdk.modelcontextprotocol.io/v2/advanced/low-level-server/)
and [official SDK repository](https://github.com/modelcontextprotocol/python-sdk).
The installed 2.3.0 API was inspected and exercised; dependencies are pinned in
`requirements-mcp.lock` rather than inferred from the repository's moving main.

## Virtual print-job server (stage 18)

The same adapter also serves the ten virtual job tools. Use a separate pinned
OctoPrint/MCP environment:

```sh
uv venv .venv-print-mcp --python .venv/bin/python
uv pip sync --python .venv-print-mcp/bin/python requirements-print-mcp.lock
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --mcp
```

This console generates a fixed cold 80-move software job; it accepts no physical
port or arbitrary job path. Physical serial opens are denied. `--journal` selects
an operation journal. The two backends remain separate: the job server does not
advertise prompt recovery, and the native prompt server does not control jobs.

An official SDK client stages, starts, pauses, waits, resumes and observes the
job. Independent simulator instrumentation verifies all 80 movements completed
once, in order, and none completed during the deliberate pause. The first run
found EOF was incorrectly exposed as completed with only 78 movements finished.
OctoPrint 1.11.8 queues M400 before its done callback; the backend now reports
`completing` until the sender reaches Operational and its virtual movement queue
has no unfinished work. This is specific to the pinned virtual profile, not a
physical completion guarantee. Three focused regressions cover late movement,
disconnect and failure precedence.

The combined suite passes **283 tests** (`evidence/tests-stage18.xml`), including
this subprocess integration. The earlier failed run is preserved as
`evidence/tests-stage18-virtual-mcp-before-completion-fix.xml`. This job test uses
a deterministic client, not model inference. The separately completed native
prompt model demonstration is documented in [model-demo.md](model-demo.md).

## Combined software console (stage 28)

The unified backend can now serve the 13-tool surface through the same SDK:

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py \
  --mcp --scenario unified_prompt --journal /absolute/path/combined.sqlite
```

It uses one virtual transport and journal, with the native generic-wait fixture
at move 10. No physical port parameter exists. `fixture_prompt_waiting` is
explicitly software-fixture evidence, not hardware telemetry. Prompt operations
before that fixture wait are refused before dispatch; a premature probe cannot
poison an otherwise valid software connection. The wait has a 120-second fixture
ceiling in console mode to allow client latency; standalone integration keeps its
shorter limit. Neither duration qualifies a physical pause policy.

The official MCP client discovers all 14 tools, verifies premature-probe refusal,
stages/starts the job, submits a pause during the wait, completes prompt
inspect/bind/respond, polls the original pause, resumes, and verifies all 80 moves
once in order. All seven completed operations appear in one journal. This test
uses deterministic client calls; model evidence is separately reported in
model-demo.md. Connection recovery and broader fault/cancel races remain open.

Stage 34 adds `printer.observations` through the same SDK server: prompt-only
servers now expose eight tools, virtual jobs eleven, and combined servers
fourteen. Existing operation/event tools retain their contracts. The new tool
reads durable polled snapshots; it does not claim continuous machine-event
coverage. Actual SDK-client validation retrieves the observed native wait,
qualified paused state and final completed state from the combined run.
Earlier model demonstrations used the tool inventory recorded in their evidence;
no additional paid model demonstration was run for this read-only tool.

Stage 36 adds `printer.stop` only to the combined virtual profile (15 tools).
Its empty operation arguments do not accept raw G-code. The actual MCP client
now exercises cancel blocked by native wait, separate virtual stop, duplicate
receipt retrieval and refused subsequent resume. This is software-only stop
qualification; see shared-transport.md for the simulator's buffered-motion limit.

The job and combined MCP servers also expose `job.pause_status` with an empty
argument object. The official-client cold pause test checks its checkpoint and
fresh-recheck gate without moving the job or granting resume. Actual hot serial
fixtures exercise the same router through standby, thermal-ready, restored and
retired phases, including recovery-required status after injected faults. The
prompt-only server's eight-tool surface is unchanged.


The hot profile now includes `printer.machine_events({"after": 0, "limit": 100})`.
It returns asynchronous serial observations, capture health and retention bounds.
Use its `next_after` for pagination; handle `history_gap`, `cursor_ahead`, capture
ID changes and health faults explicitly. It is separate from `printer.events`
(operation receipts) and `printer.observations` (polled snapshots). Source events
are observations, never permission to resume or clear a fault. See
[continuous serial observations](interface.md#continuous-serial-observations)
for coverage and shutdown limits. Other profiles do not advertise this tool yet.
