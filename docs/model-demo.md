# Model-driven native prompt demonstration

On October 25, 2026, `gpt-4.1-mini-2025-04-14` chose and executed tools through
the official MCP client against the running native firmware harness. The model
received a task and interface rules; the runner did not script its mutation
sequence. An independent read-only evaluator checked the final snapshot and
journal rather than trusting the model's final sentence.

The run passed: exactly one response operation completed with
`firmware_user_wait_cleared`, and the final native snapshot had no active prompt
or user wait. Inspect, bind and respond each completed once. Three stale-state
attempts were rejected before dispatch; the model refreshed state and recovered.
This demonstrates useful feedback from deterministic validation, not perfect
first-attempt model behavior.

| Observation | Result |
| --- | --- |
| Model requests | 14 |
| Model-selected tool calls | 13 |
| Completed journal operations | 3 |
| Rejected stale-state operations | 3 |
| Actual completed prompt responses | 1 |
| Estimated standard-rate usage cost | US$0.0144428 |
| Conservative local cost reservation | US$0.1421132 |
| Physical printer access | None |

Raw native-only tool transcript and evaluator output are retained privately in
`evidence/model-prompt-demo-20261025-a.json`. This is one generic prompt, not a
full simulated print lifecycle, adversarial evaluation or physical recovery.
The larger goal's LLM printing/blocker demonstration remains incomplete.

## Runner and limits

`tests/model_prompt_demo.py` requires `OPENAI_API_KEY` in the environment and
must be invoked explicitly. It is never called by pytest. It starts only the
fixed native console, uses local MCP stdio, maps discovered tools to function
calls, and returns model-selected calls through the same router and journal.
The API receives generated fixture data and tool schemas, not credentials,
private source files or physical device logs. Remote response storage is disabled.

The runner pins the model, caps output at 1,024 tokens per request, permits at
most 20 requests, bounds serialized input, uses a US$1 conservative local cost
reservation guard and disables automatic API retries. Reservations are recorded
before invocation so uncertain requests remain accounted for. Pricing estimates
ignore cached-input discounts and are not an invoice. The current runner requests
the default service tier explicitly. Reverify pricing before rerunning or changing
models; this task retains its combined US$10 spending cap.

Install optional dependencies in a separate environment with
`requirements-model-demo.lock`, then run with a new evidence filename:

```sh
uv venv .venv-model --python .venv/bin/python
uv pip sync --python .venv-model/bin/python requirements-model-demo.lock
.venv-model/bin/python tests/model_prompt_demo.py --evidence evidence/new-model-run.json
```

Existing evidence is never intentionally overwritten. The script does not print
credentials or raw API exception bodies. Failure records retain exception type,
request reservations and completed tool results. Offline guard tests cover tool
name collisions, input-size budgeting and ambiguous/nonfinite model arguments.
The combined software/SDK/guard suite passes 279 tests (`evidence/tests-stage17.xml`).

Official sources checked for this integration: [function calling](https://developers.openai.com/api/docs/guides/function-calling),
[the pinned model family](https://developers.openai.com/api/docs/models/gpt-4.1-mini),
and [standard API pricing](https://developers.openai.com/api/docs/pricing). The
verified rates used for this estimate are US$0.40 per million input tokens and
US$1.60 per million output tokens.

## Virtual print-job scenario

The same explicit runner now accepts `--scenario job`, using the separately
pinned `requirements-print-mcp.lock` environment:

```sh
.venv-print-mcp/bin/python tests/model_prompt_demo.py --scenario job \
  --evidence evidence/model-job-NEW-RUN.json
```

It exposes only the software job server, not a configurable device or arbitrary
server. The model chooses stage/start/pause/resume and polling calls. Independent
read-only observations and the journal check the ordered completed operations,
a pause lasting at least half a second with an unchanged mid-job move count,
resumability, and all 80 moves completed once in order. Nine offline tests check
that this evaluator rejects incomplete, moving, non-resumable and physical-access
results. The model's prose cannot establish success.

The model fixture delays each simulated move by 0.25 seconds to accommodate API
latency and uses a five-second position-response timeout. Ordinary integration
fixtures retain their original timing. This is deliberately artificial timing,
not an endurance or physical pause qualification. The job scenario permits at
most 40 model requests and retains the existing US$1 local reservation ceiling
per invocation, counted against the shared task budget. Requests still use the
pinned model, strict tools, no automatic API retries and no stored API response.

The first run (`model-job-demo-20261025-a.json`) stopped before starting: the
model lost track of the running stage receipt and repeatedly got
`operation_in_progress`. That refusal now includes the pending operation IDs
and an instruction to poll them; it does not weaken dispatch serialization.
The second run (`...-b.json`) recovered from that refusal but correctly refused
resume after a deliberately slowed simulator exceeded the original 0.5-second
position timeout. Both failed attempts are retained. Neither accessed hardware.

The third attempt (`...-c.json`) successfully staged, started, paused and resumed
but ended its conversational turn while the job was still running (35/80 moves
at evaluation). The runner now returns an actual read-only observation and asks
the model to continue if it ends a turn while the job remains running/completing.
This does not dispatch any scripted mutation or reset round/cost limits. Early
conversational completion is not accepted as job completion.

### Verified job result

The fourth attempt (`evidence/model-job-demo-20261025-d.json`) passed using
32 model requests. Stage, start, pause and resume completed in order. Independent
observations found move count 22 unchanged over a 1.536-second resumable pause;
the final snapshot reported completed with all 80 moves once in order and zero
physical serial-open attempts. Estimated standard-rate usage: US$0.0527856;
conservative local reservation: US$0.4204136. Across all four job attempts plus
the previous native-prompt demo, estimated usage totals US$0.1245084 and local
reservations total US$1.1301744, within the task's shared US$10 cap. These are
local estimates, not an invoice.

Combined offline/native/MCP validation passes 292 tests
(`evidence/tests-stage19.xml`). This establishes one cold virtual job lifecycle
and a separate native generic-wait demonstration. It does not establish combined
job-plus-prompt control, the full injected blocker matrix, extended hot pauses,
power-loss recovery, or hardware operation. Those remain part of the active goal.

## Combined MCP model scenario (stage 28)

`--scenario combined` selects the unified virtual/native console and its 13 tools.
It uses one journal and virtual serial endpoint. The task adds a generic prompt
at move 10 to the existing stage/start/pause/resume/complete workflow. The model
still chooses every mutation. Independent evaluation requires the complete job
checks, exactly one completed native wait response, an inactive final wait,
observations of the blocked interval, and two confirmed-resumable pause snapshots
with unchanged movement count at least half a second apart.

The combined scenario allows at most 50 requests and retains the US$1 local
reservation ceiling per invocation, charged against the shared task cap. The
console's native wait fixture allows 120 seconds for client latency; ordinary
non-console tests keep a shorter wait bound. Neither timing is a physical pause
qualification. The API protocol remains the existing strict function-call loop;
[official OpenAI tool guidance](https://developers.openai.com/api/docs/guides/tools)
was checked before this extension.

The first combined run (`evidence/model-combined-demo-20261025-a.json`) handled
the native prompt and completed all 80 moves, but was correctly marked incomplete
by the independent evaluator. It inspected Paused before polling the pending
pause operation, when the checkpoint was not finalized and resumable was false.
Only one qualified pause observation occurred before resume. Instructions now
explicitly require polling completion first and then two resumable=true
observations; the evaluator was not relaxed. This run used 40 requests, estimated
US$0.1328768 usage and US$0.832466 conservative reservation.

The second combined run (`evidence/model-combined-demo-20261025-b.json`) passed
with 37 model requests. Nine observations held at move 10 across an 8.452-second
native prompt wait. Three qualified pause observations held at move 48 across
6.950 seconds. The model recovered from stale-state and pending-operation
refusals, cleared exactly one native wait, and completed all 80 moves once in
order. Physical serial-open attempts remained zero. The model cleared the prompt
before its successful pause request; the deterministic MCP test separately
proves prompt recovery while the pause request is pending.

Run B estimated usage was US$0.0896264 with US$0.6269112 reserved. Across all seven
model attempts in this task, estimated usage is US$0.3470116 and conservative
reservations total US$2.5895516, within the shared US$10 task cap. These are local
estimates rather than billing records. Full regression validation passes 388
tests (`evidence/tests-stage28.xml`), including the real MCP client combined
workflow and seven independent combined-evaluator cases.

This qualifies one software-only combined workflow. The prompt marker is
synthetic, the native handler is real compiled code, and movement is OctoPrint's
virtual device. It does not qualify physical thermal behavior, extended pauses,
power loss, cancellation during a native wait, or the remaining blocker matrix.


## Model-driven hot pause and resume

The hot scenario now uses the external hot_console MCP profile. In the recorded
stage75 run, the pinned model chose 31 tool calls across 32 API requests. Four
stale-state operations were rejected; the model refreshed observations and
continued. Exactly four job operations completed: stage, start, pause and resume.
It paused after 38 of 80 model job moves and supplied independent standby
observations spanning 2.024 seconds. The thermal receipts confirm standby at 100°C
and reheat to 200°C with a 60°C bed target; restoration completed and the job
finished with all 80 job moves once in order. Both final heater targets were zero.
The model's summary was not used as proof.

The independent evaluator additionally checks matching thermal/restoration
checkpoint identities, completed effects, retired live authority, no running
monitor, unchanged mid-job standby counts, the restoration-only move, and zero
physical serial attempts. Tests reject partial or contradictory evidence. The
retained run also passed tightened target-shutdown and retirement checks after
completion; no second paid run was needed. Sanitized results are in
[the hot model validation record](../validation/model-hot-stage75.json).

The run used scripted temperatures and synthetic motor/position/adhesion proofs.
For model latency only, this fixture uses 0.25-second moves and a 120-second bounded
pause; ordinary integration timings are unchanged. This is not physical thermal
or endurance qualification. It did not exercise setup/calibration tools or an
injected firmware prompt in the same hot job. Earlier combined runs separately
cover a native generic prompt.

Estimated standard-rate usage was US$0.0885944; the conservative reservation was
US$0.6009. Across eight retained model runs, estimates total US$0.435606 and
reservations US$3.1904516, within the task's combined US$10 cap. These are local
estimates, not billing records. Rates and the pinned snapshot were checked against
[official OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
before this run. The catalog labels this older snapshot deprecated; it remained
available for this recorded request sequence. No model migration is implied.

```sh
.venv-print-mcp/bin/python tests/model_prompt_demo.py --scenario hot --evidence evidence/model-hot-NEW.json
```

This remains an explicit paid command, never invoked by pytest. The existing
US$1 per-invocation reservation, 40-round limit, bounded inputs, no automatic API
retries and disabled remote response storage remain in force. Account all runs
against the shared task budget before invocation.
