# Printer-control qualification and release preparation

H4a collects the software qualification, source/build references, prior-art
research, contribution preparation and recovery candidate documentation for the
preceding host stack. It includes native firmware protection regressions and a
Linux-only descriptor transport with assignment and controller-lease checks. This is not a commissioned development
release, and no physical printer migration is authorized by these artifacts.

## Reproduce software evidence

Use Python 3.11, Git and `clang++` on macOS or Linux:

```sh
uv venv --python 3.11 .venv
uv pip sync --python .venv/bin/python requirements-print-mcp.lock
uv pip install --python .venv/bin/python --no-deps .
.venv/bin/python scripts/prepare_firmware.py --repository /path/to/reviewed/firmware-repository
.venv/bin/python -m pytest -q tests tests_mcp tests_model tests_print_mcp
```

The firmware repository must contain the exact commit/tree in
`firmware-test-source.json`; the public firmware fork remains unpublished.
Native tests compile actual or extracted source functions with substituted
physical dependencies. They are not complete firmware simulation or hardware
qualification. The normal suite calls no paid model API.

Linux terminal-exclusivity, descriptor I/O and assigned-factory tests are skipped
on macOS. They require a Linux run to qualify those layers; the adapter rejects
macOS before opening a device.
The Linux tests use software terminals, not printers. Exclusion does not evict
existing descriptors or replace device permissions and identity checks.

## What remains before a development release

- Establish the development printer's board, MCU, screen, bootloader and recovery
  route. The currently printing printer remains protected and excluded.
- Resolve the observed sensor problem, verify the workspace and qualify any
  permitted non-printing motion. Keep heating/extrusion disabled for the clogged
  test profile. No stock package is yet a validated rollback for this device.
- Complete production USB ownership, device access isolation and controlled
  recovery. The tested runtime is virtual-only.
- Complete manual privacy/attribution review, publish the pinned firmware and
  host PR stack, and update the explanatory page with actual public links.
- Keep real printing, adhesion, extrusion and print-resume quality outside the
  non-printing qualification until the nozzle is repaired and supervised tests
  pass. Never migrate the protected printer as part of this project.

See [recovery limits](docs/recovery.md), [interface scope](docs/interface.md),
[prior art](docs/prior-art.md), [contribution plan](docs/contributions.md) and
[publication checks](docs/publication.md). Each validation record pins its tested
revision; historical successes do not qualify later untested changes. Local
progress logs, physical-device records, recovery binaries and private Git history
are excluded from this package. No PR submission or maintainer acceptance is
claimed. Original host source is AGPL-3.0-or-later; third-party terms remain intact.
