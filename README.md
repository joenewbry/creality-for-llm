# LLM printer control

Development implementation of a typed LLM control interface for the Creality S1 Pro.
The protected production printer and its host software are outside this repository's device scope.

This development prototype includes typed tools, durable operations, virtual jobs,
native firmware prompt handling and bounded simulator setup controls.
It does not yet implement a qualified hardware controller. Tests use virtual devices, software terminals and hardware substitutes; no physical printer or camera capture is controlled.

Current installed-package qualification: **1136 tests passed, zero skips on Linux/Python 3.11.13** at the [public source snapshot](https://github.com/joenewbry/creality-for-llm/tree/f19377abbf0762969077a2ef12d5f5edd1ca2a4e). Installed runtime bytes and all 99 locked dependencies were verified. See [the qualification record](validation/linux-package-stage125.json), [goal audit](docs/goal-audit.md), [contribution stack](docs/contributions.md) and [post-repair printing plan](docs/printing-qualification-plan.md). Firmware commissioning and physical printer/camera operation remain unqualified.

The [owner About intake](docs/development-hardware.md) records firmware 2.0.8.28F4, screen V1.0.5 and reported hardware CR-FDM-v24S1_301. Physical processor/recovery/sensor checks remain open. Logitech work is deferred at the owner’s request.

## Source layout

- `printer_control`: host tools, durable service, protocol, ownership and camera interfaces.
- `tests`, `tests_mcp`, `tests_model`, `tests_print_mcp`: behavioral and integration checks.
- `firmware-test-source.json` and `scripts/prepare_firmware.py`: fetch the separately published, pinned firmware source into an ignored local checkout.
- `hardware/camera-mount`: editable CAD and conditional prototype geometry checks.
- `docs` and `validation`: design, qualification scopes and sanitized evidence.
- `evidence`: local/private raw artifacts, excluded from source publication.

## Run software tests

For a clean checkout, follow [reproducible software setup](docs/reproduce.md).
It pins the modified firmware needed by native tests as well as host dependencies.


Use Python 3.11 and `pytest==8.3.5` in an isolated virtual environment.
Run `python -m pytest -q tests tests_mcp tests_model tests_print_mcp` for the complete suite. Software-terminal opens and read-only OS metadata checks are included; physical serial opens are denied by the virtual runners.
Firmware builds use `platformio==6.1.18` and an isolated `PLATFORMIO_CORE_DIR`.

## OctoPrint integration experiment

Install `requirements-octoprint.lock` in a separate Python 3.11 environment.
Run `python tests/octoprint_virtual_smoke.py --scenario resume`, then repeat with
`missing_position` and `disconnect`. Each command runs in its own process with
temporary settings. No web server starts. Physical PySerial opens are denied;
the only connection factory accepts `VIRTUAL`. The sender and bundled virtual
firmware are OctoPrint 1.11.8; application callbacks and plugin discovery are
replaced. A recording subclass observes actual simulated move completion.

This tests an integration direction, not the complete LLM controller or S1
firmware. Physical parking, thermal-policy qualification and a production adapter remain unfinished. A missing position report still lets OctoPrint enter
Paused; missing position therefore remains an uncertain result and blocks resume.

Add `--service` to route pause/resume/cancel through `OperationService` and the
virtual-only backend. The service resume scenario now captures a durable
checkpoint and executes all 80 moves once through the service, without a direct
resume bypass. Also run `--service --scenario interference` and `--service
--scenario expired`. The latter advances a test clock by 61 seconds; it is not
a real-time endurance test. This initial profile is cold and in-place, with a
60-second simulated policy limit and explicit virtual-model restrictions.

An opt-in `--service --scenario hot_resume` experiment now streams a fixed hot
simulation through standby, reheat, native-compared modal restoration and the
durable one-use resume handoff. It checks every file move executes once in order
and final heater targets are zero. `--scenario hot_resume_temp_loss` verifies
that losing nozzle temperature after restoration keeps the job paused.
`--scenario hot_resume_twice` verifies two complete cycles in the same job,
with distinct checkpoints and retired prior-pause resources. These
use an extended virtual model, scripted temperatures, synthetic motor/part
continuity and accelerated time limits. They do not qualify either physical
printer, arbitrary hot jobs or long-duration adhesion. The explicit
`hot_console` profile exposes these simulated operations through typed MCP tools;
see [current profile launch instructions](docs/mcp.md).

Design: [prior art](docs/prior-art.md), [pause/resume](docs/pause-resume.md),
[interface](docs/interface.md), [typed tools and console](docs/tools.md), [publication preparation](docs/publication.md).
The [explanation page](https://robot.digitalsurfacelabs.com/pages/creality-for-llm/)
is published behind the existing site access gate.

Do not run upload targets. Firmware files built here are not qualified for either physical printer.
The second printer's board, bootloader, screen version, physical identity and rollback image must be recorded before flashing.

## License and attribution

Original host code, tests and documentation are offered under the GNU Affero
General Public License, version 3 or (at your option) any later version
(`AGPL-3.0-or-later`), without warranty. See [LICENSE](LICENSE).
Upstream code and assets retain their own notices and terms; see
[third-party notices](THIRD_PARTY_NOTICES.md). The host license does not grant
rights to stock firmware binaries or screen assets. A commissioned physical development release remains pending.
