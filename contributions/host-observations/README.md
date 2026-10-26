# Printer observations and prompt protocol

This H2a review boundary builds on H1's typed operation core. It adds bounded
Marlin serial parsing, prompt-wire encoding/decoding and asynchronous journaled
machine events. Observations do not grant permission to resume or dismiss a
fault. Acknowledgments alone do not prove physical completion.

Capture has a bounded queue and retention window. Overflow, truncated final
lines and persistence failures are explicit. Closing capture invalidates local
prompt observations without claiming the firmware closed its prompt. The caller
must retain controller ownership until the capture worker has stopped.

## Reproduce

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python pytest==8.3.5
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests
```

The firmware repository must contain the exact commit and tree in
`firmware-test-source.json`. The public fork is not yet published. Preparation
creates an independent checkout and refuses an existing destination; no upload
or printer connection occurs. The native harness compiles copied production
prompt handlers with substituted hardware dependencies. It checks token/session
identity, malformed frames, stale responses, supported choices and selected
screen/wait handlers. This is not a full firmware simulation or touchscreen test.

The software capture tests require no physical device. No backend connects these
modules to a physical printer in this boundary. Virtual job execution, powered
pause/resume integration, setup/maintenance, the LLM tool server and physical
commissioning remain later review work. No PR has been submitted.

Original host source is AGPL-3.0-or-later. Native firmware source retains its own
GPL terms and is fetched separately; third-party notices remain included.
