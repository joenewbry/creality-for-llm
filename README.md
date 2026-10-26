# Streamed virtual printer runtime

H2f connects the preceding host components to OctoPrint's virtual serial sender.
It adds typed JSON tools, managed resume preparation, pause status, virtual
thermal effects and calibration. The test runner denies physical serial opens;
there is no hardware transport factory in this boundary.

Ten end-to-end scenarios exercise cold resume, a native prompt within the sender,
managed hot resume, two pauses in one job, cancel and service shutdown while paused, paused
connection loss, active reset, checksum resend and a thermal-fault report. Each
scenario's existing assertions check receipts and modeled state. Other component
and native-function tests remain included. Scripted temperatures, motor/part
continuity and virtual EEPROM behavior do not qualify real heaters, adhesion,
position retention or hardware settings persistence.

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

The firmware repository must provide the pinned commit/tree; public publication
is pending. Native tests substitute physical dependencies. Runtime scenarios
create local `evidence` reports. Service shutdown does not confirm heater shutdown. The command above makes no paid model calls.

The runner has optional MCP/model-fixture switches used by later boundaries;
the MCP adapter and model client are intentionally absent here. Use ordinary
`--service` scenarios or the JSON `--console` interface only. The dedicated
Model Context Protocol (MCP) client/server and model demonstrations follow in H3.

This software-only boundary is not a release or physical qualification. Remaining
firmware-protection evidence, device access isolation, commissioning, final
publication and real printing qualification remain separate work. Original host
source is AGPL-3.0-or-later; dependencies and firmware retain their own licenses.
