# Virtual job and setup adapter components

H2e builds on H2d with OctoPrint virtual job/combined-prompt adapters, bounded
setup operations and resend tracking. The adapter requires the explicit VIRTUAL
port and OctoPrint's virtual-printer type. It provides no physical port factory.

Completion needs the expected state transition and drained modeled moves, not
just an end-of-file callback. Home/jog operations are bounded to the cold test
profile and require correlated completion evidence. Combined cancellation
revokes queued pause grants before dispatch. Resend uncertainty cannot silently
renew an invalidated job. Tests also kill a separate host process at durable
operation boundaries and verify that startup does not replay its effects.

This boundary qualifies adapter components using controlled sender/device
substitutes. It does not run the full serial sender or qualify actual motion,
heaters, homing or USB ownership. Later boundaries add the complete virtual job
runner and managed pause/resume workflow. The original full-project integration
tests remain separate from these component results.

## Reproduce

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip sync --python .venv/bin/python requirements-octoprint.lock
uv pip install --python .venv/bin/python pytest==8.3.5
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests
```

The reviewed firmware must contain the pinned commit/tree in
`firmware-test-source.json`. The public firmware fork is not yet published.
Inherited native tests use actual functions with physical stubs. Adapter
admission tests use inert objects and reject nonvirtual assignments before job
access; no physical serial port is opened. Model credentials are unnecessary.

Original host source is AGPL-3.0-or-later; separately fetched firmware and
installed dependencies retain their licenses. This is our local adapter package,
not an OctoPrint upstream contribution. No public PR has been submitted.
