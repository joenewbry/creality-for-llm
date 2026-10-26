# LLM-facing printer tools and evaluation

H3 builds on the streamed virtual runtime in H2f. It adds a Model Context
Protocol (MCP) adapter, actual client/server integration tests, a bounded model
demo runner and independent outcome evaluators. It does not add physical USB
access or grant an LLM authority to override deterministic controls.

Tools publish structured schemas and dispatch through the same identity,
state-version, deadline, journal and backend checks as the JSON service. The
model can choose supported operations; it cannot issue unrestricted G-code.
Pause, resume, cancel and stop are distinct operations with separate completion
evidence. Unknown outcomes and physical-intervention requirements remain visible.

## Reproduce without model API calls

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip sync --python .venv/bin/python requirements-print-mcp.lock
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests tests_mcp tests_model tests_print_mcp
```

The firmware repository must supply the exact commit/tree in
`firmware-test-source.json`. Public firmware publication remains pending.
The ordinary test command opens local virtual/native subprocesses, uses actual
MCP clients and checks model-output evaluators against fixtures. It requires no
model credential and makes no paid API call. Physical serial opens are denied
by the virtual runner; native physical dependencies remain substituted.

`tests/model_prompt_demo.py` is a separate, opt-in paid model demonstration.
Its execution requires credentials, a model still available to the account,
a task-wide spend allowance, and retained success/failure evidence. The tests
above do not invoke it as a model run. Earlier paid-run evidence is separate
from this boundary's qualification; no new live-model outcome is claimed here.

This remains a development interface. Physical identity, production transport,
commissioning, final release/rollback evidence and public PR/page delivery are
unfinished. Scripted virtual temperatures and continuity do not qualify real
printing, physical pauses or resumed extrusion. No PR has been submitted.
Original source is AGPL-3.0-or-later; installed dependencies and separately
fetched firmware retain their own licenses and attribution.
