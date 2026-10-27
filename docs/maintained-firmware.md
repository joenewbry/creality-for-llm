# Maintained S1 Pro source candidate

The earlier Creality .24F4 experiment lacks the input shaping reported by the
installed .28F4 firmware. A second, unflashed candidate now ports the prompt
protocol onto ThomasToka's S1 Pro v040 release source. This is engineering
progress, not permission to install it or a stock-equivalent printing claim.

## Repository and branch relationships

- `joenewbry/creality-for-llm`: one host codebase, including Mac/Linux adapters.
- `joenewbry/Ender-3S1-llm`: earlier Creality-derived protocol experiment. Its six
  draft PRs and `98aa032` host test pin remain unchanged.
- `joenewbry/Marlin`: the owner's existing Marlin fork, reused for a separate
  v040-based review stack. Its name and default branch are preserved.
- `ThomasToka/Marlin`: maintained source. Use release branch
  `MARLIN-E3S1PROFORK-BYTT-RELEASE-CODE-v040` at
  `0114371a8644ab58e254865f2c0345b061704b39`; the generic development branch is
  not the matching S1 Pro release configuration.
- `ThomasToka/MarlinFirmware`: release binaries, screen packages and release
  instructions. These are distinct from the controller source repository.

The new PR chain is based on `reference/toka-v040`: printing profile, protocol
port, heater-watch parity, then the runtime handoff correction. The checked-in qualification JSON records exact
commits, artifact hashes, dependencies and validation boundaries. This lineage
is unrelated to a Mac/Linux split: either host OS speaks the same printer
protocol, while firmware selection depends on the installed hardware.

## Implemented and checked

The unmodified v040 S1 Pro bilinear profile builds with input shaping on both
axes but leaves only 2,812 application-flash bytes. The printing-only profile
removes laser support, retaining shaping, leveling, preview and thermal guards;
it leaves 16,064 bytes before the new protocol is added.

The protocol port builds with 13,704 bytes free. It adapts the newer HostUI and
split emergency parser, explicitly registers its source file with the feature
build system, and fences both ordinary-dispatcher and IRQ legacy M108/M876
paths. Wire-v1 reason IDs are mapped explicitly because v040 inserts a new
internal reheat reason. That unsupported reason fails closed. Remote continue
cannot disable a triggered runout sensor. Stale or duplicate replies cannot
repeat a completed prompt action.

Eleven native scenarios compile actual HostUI, wire, emergency parser and
ordinary M108/M876 sources with hardware substitutes. Sixty-six existing host
channel/backend checks also pass against the new native executable. The thermal
layer separately tests the actual HeaterWatch template's arming/expiry and
zero-wrap behavior plus hotend/bed/chamber threshold selection. Full thermal
manager shutdown on this baseline remains unqualified.

These results are distinct from the previously qualified installed Linux host
snapshot (1,156 tests, zero skips) against the older firmware pin. The full host
suite has not been repinned or requalified against v040. Software-only native
checks do not establish physical print quality, real serial interrupts, screen
behavior or heated pause/resume.

## Reproduce the focused native integration checks

Build the candidate without uploading:

```
pio run -e STM32F401RC_creality_e3s1pro_llm -j 2
python3 buildroot/tests/llm_prompt_native.py
python3 buildroot/tests/llm_heater_watch_native.py
```

For the 66 host checks, run from the host checkout using its Python environment.
Replace `candidate` with a checkout of the exact candidate commit in the record:

```python
from pathlib import Path
import importlib.util
import sys
import pytest
sys.path[:0] = [str(Path.cwd()), str(Path('tests').resolve())]
p = Path('candidate/buildroot/tests/llm_prompt_native.py')
spec = importlib.util.spec_from_file_location('candidate_native', p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
import test_firmware_host_actions
# This process only; the checked-in firmware pin remains unchanged.
test_firmware_host_actions.build_native_harness = module.build_native_harness
raise SystemExit(pytest.main(['-q', 'tests/test_prompt_channel.py',
                             'tests/test_prompt_backend.py']))
```

## Before commissioning

F401 family selection remains a strong inference from the installed F4 version,
not a readback of chip markings. Physical chip inspection stays deferred and
does not gate software, simulation or bounded read-only work. Before flashing,
qualify target/layout, bootloader, compatible recovery, physical screen variant
and its paired firmware, settings migration, probe geometry and configuration.
Source availability alone cannot establish those installed facts. Do not
prepare update media or update the screen from these build results.

The unresolved invalid nozzle reading prevents heating and operational
commissioning. Existing manufacturer recovery provenance is established;
physical rollback and settings restoration are not. The original printer stays
protected regardless of whether it is currently printing. Logitech work remains
entirely deferred. No printer or camera was accessed for this source port.

[Structured qualification](../validation/maintained-firmware-stage135.json)


## Runtime handoff correction

The first three layers were not the complete near-target backport. Candidate
`7569d4a`'s watch template disarms at the hysteresis boundary, while TRFirstHeating
still waits for the exact target. An actual-source runtime harness reproduced
six regressions (nozzle/bed boundary hold, boundary drop and above-boundary
drop); a later drop could evade both protection branches. These are software
reproductions, not physical fault experiments.

The [dependent runtime PR #4](https://github.com/joenewbry/Marlin/pull/4) ports the
missing condition from upstream [Marlin PR #28695](https://github.com/MarlinFirmware/Marlin/pull/28695),
merged as c27424fa51053dd3b0febde2f03b287499403d39. All 32 extracted-path scenarios
pass after correction; other extracted source snippets and header/config hashes
are unchanged. Actual error/disable/loud-kill functions run with recording IO.
Assertions verify targets/PWM/pin writes and autotemp are off before park or
final kill, no park when unhomed, one kill on repeated faults, correct boundary
protection, heating stalls, wrap and idle behavior.

Current source `09162eb` builds at 183,008/196,608 application flash
(13,600 bytes free), RAM 39,860. Eleven prompt cases, six template cases/three
typedef checks and 66 host checks
pass. The previous layer requires this correction before commissioning
consideration. No source has been flashed.

Run `python3 buildroot/tests/llm_thermal_paths_native.py` from the firmware
checkout; `--source-root` permits reproducing the six failures against `7569d4a`.
Use `--output` to save detailed results and source/snippet hashes. The harness
does not execute the complete temperature task, PID, ADC, interrupts or real
screen/motion/kill/HAL. Physical shutdown remains unqualified, as do the target,
screen, settings and recovery path. [Full structured evidence](../validation/thermal-handoff-stage136.json).


## Controller-state layer

[Draft PR5](https://github.com/joenewbry/Marlin/pull/5) depends on4 and adds the
optional read-only `LLM_MACHINE_STATE` report in source `0f8d1ca`. Its F401 build
uses 183,960 / 196,608 application flash bytes and 39,860 / 65,536 RAM bytes; four actual getter
configurations, five wire cases and eleven existing prompt cases pass with
hardware stubs. It copies variables under a critical section and serializes
afterward without changing settings, releasing waits or actuating hardware.
The source-correlated host read is described in [state evidence](machine-state.md).
No physical sampling/interrupt timing, full state-to-checkpoint provider, sensor
health, motor power, part continuity, recovery or commissioning is qualified.
The host's older `98aa` firmware-test pin remains unchanged.
