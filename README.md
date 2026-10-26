# Powered pause and restoration policy

This H2c boundary builds on H2b. It adds command-mode tracking, bounded
thermal-pause policy and immutable in-place restoration plans. These modules
make decisions and emit plans; this boundary does not execute a printer job.

Mode tracking preserves independent coordinate/extrusion modes and relevant
fan, feed and temperature settings. Unsupported or ambiguous commands latch
uncertainty. Cold-job preflight restricts admitted commands and distinguishes
tracked modes from commands the cold test profile permits. Restoration requires a matching job, connection, configuration,
position and continuity checkpoint. It never treats a saved file offset as
permission to resume after reset or position loss.

Thermal pause moves through explicit standby, reheat, ready and consumed states.
Limits, observation freshness, deadlines and continuity evidence gate transitions.
Temperature readiness requires sustained acceptable observations; an acknowledged
command alone is insufficient. Restoration plans preserve order and avoid
coordinate changes. Actual command dispatch, receipt correlation and resumed
stream accounting are later integration work.

## Reproduce

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python pytest==8.3.5
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests
```

The repository argument must supply the exact commit/tree in
`firmware-test-source.json`; the public fork remains unpublished. Preparation
creates an independent firmware checkout, never builds an upload target or
opens a serial port. Tests compare emitted modes and restoration sequences with
extracted functions from the pinned firmware. The native restoration harness
substitutes its parser and physical dependencies; it is not the complete Marlin
parser, scheduler, interrupt system or hardware.

Tests include stale/invalid checkpoints, mode combinations, bounded thermal
transitions and loss of continuity. No test here proves physical heater behavior,
motor hold, part adhesion or resumed extrusion. Virtual sender integration,
setup/maintenance, LLM tools and physical commissioning remain later boundaries.
No PR has been submitted.

Original host source is AGPL-3.0-or-later. Native firmware retains its GPL terms
and is fetched separately; third-party notices are included.
