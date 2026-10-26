# Creality printer control for LLMs

A development interface for bounded job control, prompt handling, durable receipts
and powered pause/resume. The complete installed-package software suite passed
876 tests with zero skips on Linux/Python 3.11.13. Physical printer commissioning
is incomplete; the firmware candidate is unflashed.

[Architecture and camera path](docs/architecture.md) · [Pause/resume](docs/pause-resume.md)
· [Camera interface design](docs/camera-interface.md) · [Mount prototype](hardware/camera-mount/README.md)
· [Explanation page](https://robot.digitalsurfacelabs.com/pages/creality-for-llm/)

## What is implemented

- Typed jobs, supported prompts, setup operations, event history and durable
  operation receipts with state/generation/deadline checks and duplicate handling.
- Virtual streamed jobs, actual native firmware-handler tests, managed standby,
  reheat/restoration and explicit pause/resume/cancel/stop semantics.
- Linux descriptor ownership/I/O foundation tested with software terminals.
- Read-only Mac USB connection discovery; Mac physical transport remains disabled.

One host codebase contains both OS paths. Firmware builds are selected by the
printer's board/processor and screen, not the host OS. The modified firmware is
in [the separate Creality fork](https://github.com/joenewbry/Ender-3S1-llm).
Both repositories use draft review stacks; these are not commissioned releases.
[Open host PRs](https://github.com/joenewbry/creality-for-llm/pulls) contain the
focused changes, validation and dependencies.

## Reproduce the software checks

Use Python 3.11, Git and Clang. Native firmware tests require the exact pinned
source; the preparation step verifies its commit/tree and refuses an existing
checkout.

```sh
clang++ --version
uv venv --python 3.11 .venv
uv pip sync --python .venv/bin/python requirements-print-mcp.lock
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository https://github.com/joenewbry/Ender-3S1-llm.git
.venv/bin/python -m pytest -q tests tests_mcp tests_model tests_print_mcp
```

The normal suite uses no paid model API. On Mac, Linux terminal tests skip;
Linux/Python 3.11.13/Clang 15.0.7 passed the complete installed-package suite.
See [pinned evidence](validation/linux-package-stage111.json).

## Remaining physical and camera work

Confirm the development board, processor, display, bootloader and recovery route;
resolve the negative nozzle reading and confirm clearance before operational
commissioning. Production USB integration, access isolation and physical pause/
resume remain unqualified. The protected printer stays excluded.

Camera capture, controls and monitoring are planned interfaces. The C270 mount
has editable CAD, manifold mesh and conditional visibility/keep-out checks;
actual clip/desk/printer fit, focus, cable routing and framing remain unverified.
No mesh has been sent to a printer. Real extrusion and print quality require
nozzle repair and supervised qualification.

Original source is AGPL-3.0-or-later, with firmware patches and third-party terms
preserved in [the notices](THIRD_PARTY_NOTICES.md). No upstream acceptance or
physical release is implied by an open draft PR.
