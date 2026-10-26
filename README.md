# Durable pause effects and restoration handoff

H2d builds on H2c's powered-pause policy. It adds stream-state evidence,
one-use command grants, durable thermal effects, background pause monitoring
and restoration dispatch/receipt handling. A tested handoff rechecks evidence
before allowing a stub backend to continue the same job.

Effect intent is recorded before dispatch. Storage or partial-write failures
cannot silently authorize follow-up commands. A restart seals uncertain prior
work instead of replaying it. Restoration needs its own completion receipt and
fresh continuity evidence before the service dispatches resume. Cancel and stop
remain distinct from confirmed physical shutdown.

These modules accept explicitly supplied callbacks; this boundary provides no
physical sender or full virtual job runner. The tests use deterministic backend
and stream-hook substitutes. They qualify state transitions, ordering, grants
and durable outcomes, not actual queued motion or heater behavior. The later
combined-virtual cancellation tests remain in the complete project's integration
suite; they are not included or claimed as passed by this boundary alone.

## Reproduce

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python pytest==8.3.5
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests
```

The firmware repository must contain the exact commit/tree pinned in
`firmware-test-source.json`. Public firmware publication is pending. Inherited
native tests use extracted firmware functions and physical stubs. The installed
package has no OctoPrint or model-client dependency at this boundary.

The full managed resume workflow, virtual job sender, setup/maintenance, LLM
interface and physical commissioning remain later review units. No test here
opens a printer or establishes resumed extrusion quality. No PR is submitted.
Original host source is AGPL-3.0-or-later; separately fetched firmware retains
its own GPL terms and attribution.
