# Reproduce software qualification

This procedure runs software-only tests. It does not connect to a printer, build
an upload target, or establish that a firmware binary is safe to flash.
Use Python 3.11, Git, a C++ compiler (`clang++` for the current native harness),
and an isolated environment. The combined dependency snapshot is
`requirements-print-mcp.lock`.

The host repository alone is insufficient for native tests: they compile code
from our modified firmware. `firmware-test-source.json` pins its exact commit,
tree and upstream ancestor. The pinned modified firmware is published at
`https://github.com/joenewbry/Ender-3S1-llm.git` on draft review branches. A clone
of official Creality alone does not contain our required revision.

From a clean host checkout, with `uv` already installed:

```sh
clang++ --version
uv venv --python 3.11 .venv-print-mcp
uv pip sync --python .venv-print-mcp/bin/python requirements-print-mcp.lock
.venv-print-mcp/bin/python scripts/prepare_firmware.py --repository https://github.com/joenewbry/Ender-3S1-llm.git
.venv-print-mcp/bin/python scripts/prepare_state_fixture.py
.venv-print-mcp/bin/python -m pytest -q tests tests_mcp tests_model tests_print_mcp --junitxml=software-tests.xml
```

Replace the repository argument with the reviewed public fork URL after its
publication. Preparation refuses any existing destination, verifies commit,
upstream ancestry and tree, and checks out detached into a newly reserved
`firmware` directory. It does not import source working changes or share object
alternates. It does not replace the development firmware worktree. Use another
clean host checkout when an existing destination must be preserved.

The ordinary pytest invocation does not call a paid model API. Actual model runs
are separate, require credentials and a task-wide spend allowance, and must
retain failed attempts. See model-demo.md. Compilation uses native test stubs
for physical dependencies; successful results do not qualify real movement,
heating, extrusion, screen behavior or extended physical pauses.

Firmware binary builds remain a separate process described in source-audit.md.
The pinned screen project is not required for these native tests and is not
redistributed by this setup. Public release packaging, supported host operating
systems and fully independent package-download reproducibility still require
qualification.

Verified October 25, 2026 at host commit `1dd9f5d`: a separate Git clone, fresh
CPython 3.11.13 environment with all 99 locked packages, and independent firmware
clone passed 395 tests with no skips. The firmware preparation verified commit
`a5c1cabc478e52c5e2ec00a00686e2b573a912e4` and tree
`fbd322f08cde2eb184cd6d8122d134abf9df46df`. Local evidence:
`evidence/stage32-reproduction.json` and `evidence/tests-stage32-clean.xml`.
The package cache was available and the Git repositories were local. This is
not yet a public-fork or offline-distribution reproduction, nor a firmware binary
rebuild. Those distinctions remain part of the release checklist.

Run the streamed hot-pause software experiments with the combined locked test
environment (physical serial opens are denied by the runner):

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario hot_resume
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario hot_resume_temp_loss
```

Results are written to `evidence/hot_resume.json` and
`evidence/hot_resume_temp_loss.json`. These fixtures use scripted temperatures
and virtual continuity proofs; see pause-resume.md for their scope. No printer
connection or model API credential is needed.

To exercise two hot pauses in the same fixed simulated job:

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario hot_resume_twice
```

`evidence/hot_resume_twice.json` includes each checkpoint/cursor, retired-resource
state, thermal effects and resume receipt, plus complete-job move accounting.

Paused hot-job fault variants:

```sh
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario hot_pause_disconnect
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario hot_pause_reset
.venv-print-mcp/bin/python tests/octoprint_virtual_smoke.py --service --scenario hot_pause_missing_receipt
```

Each writes its named JSON record under `evidence/`. They use only virtual serial
and explicitly report heater shutdown as unconfirmed.


A fresh sanitized export of host `8d8453f8bf334d194348ec941fe9b4de26378a56`
was also reproduced:141 allowlisted files, new Python 3.11.13 environment,
99 pinned packages, independent pinned firmware checkout, and641 passing tests
with zero failures/errors/skips. All exported source hashes remained unchanged
through testing. Sanitized evidence is in
[the validation record](../validation/clean-export-8d8453f.json).
This used a local package cache and the reviewed local firmware repository;
it is not yet a public-fork reproduction or a firmware binary rebuild. The
record names the tested source commit, not whatever later HEAD contains.

For separate firmware binary builds, use the [firmware stack build procedure](firmware-stack-builds.md).
It records each review boundary and the required virtual-environment activation.

A later clean export at host `86bde0b8e49b62a43ba5b304ecbb55f0097d6bf5`
reproduced all 716 tests with zero failures, errors or skips in 68.93 seconds.
It contained 151 allowlisted files, used a new Python 3.11.13 environment with
99 locked packages, and prepared an independent clone of the pinned firmware.
Every exported source hash remained unchanged. See
[the stage 77 clean-export record](../validation/clean-export-86bde0b.json).
The local package cache and reviewed local firmware repository were available;
this still does not establish public-fork or independent-download reproduction.
No model API call or physical serial access was part of the test run.

The stage 85 export at host `4b768f956a7ebd90e6f297a0149963d057187723`
contains 170 allowlisted files and passes all 775 tests with zero failures,
errors or skips in 70.52 seconds. A new Python 3.11.13 environment installed all
99 locked packages and independently cloned pinned firmware `98aa032`; its
worktree and every exported source hash remained unchanged after tests.
See [the stage 85 clean-export record](../validation/clean-export-4b768f9.json).
This includes the continuous machine-event capture and later native thermal
regressions. It used the local package cache and reviewed local firmware Git
repository. Public-fork reproduction, independent PR-boundary testing, firmware
binary rebuilding and physical commissioning are separate outstanding checks.
No paid model call or physical printer test was performed.

## Linux installed-package qualification

Export `1a72ba7` passed all **876 tests with zero skips** on Linux aarch64,
CPython 3.11.13 and Clang 15.0.7. All 99 locked dependency versions were verified.
The package was built and installed, and the complete suite ran outside the
source checkout with imports verified from site-packages. All installed runtime
module bytes matched the reviewed export; all 219 exported source hashes remained
unchanged. The pinned native firmware checkout was independently prepared and
verified clean at `98aa032`/tree `70ec40f`.

The first run lacked the explicitly required Clang compiler: 62 native setup
errors and 18 skips are retained, alongside three external-client timing failures.
A task-local Clang extraction resolved native setup. External test clients now
use the existing slower virtual fixture and refresh only explicitly rejected
stale-state requests under bounded deadlines with new IDs. They do not retry
uncertain operations. The corrected clients pass on Mac too.

[Exact qualification record](../validation/linux-package-stage111.json). This
closes the earlier supported-Python Linux test gap for this export. It does not
qualify a physical USB connection, surviving-printer recovery or firmware flash.


The second materializer downloads only 16 hash-pinned source files from maintained
firmware `0f8d1ca` for the combined native state-report fixture. It does not replace
the primary `98aa` firmware-test pin, build/upload firmware or open serial devices.
It refuses existing destinations and corrupt/changed inputs. Pytest performs no
automatic source download. See [combined state path](native-state-path.md).
