# Firmware stack build qualification

Each proposed firmware PR boundary is built in a separate detached worktree,
using that revision's default environment. This checks that the review units
compile independently. It does not qualify a connected board, prove prompt
behavior at every boundary, or establish a safe recovery image.

| Boundary | Commit | Default build environment |
| --- | --- | --- |
| F1: generic build repairs | `362508639f39f501dc44c608e42e74e930a0b578` | `STM32F103RET6_creality` |
| F1b: development board selection | `93ea5539e3b30689c730bb4e5e691564d44882a3` | `STM32F401RC_creality` |
| F2: prompt identity and lifecycle | `7ec664f7ceb5d3bfb754aa0778ab786dde953274` | `STM32F401RC_creality` |
| F3: bounded prompt transport | `a5c1cabc478e52c5e2ec00a00686e2b573a912e4` | `STM32F401RC_creality` |

The public [owner firmware fork](https://github.com/joenewbry/Ender-3S1-llm) contains these revisions and all six dependent draft PRs. For each row, substitute its commit and a new destination:

```sh
python3.11 -m venv /path/to/build-venv
. /path/to/build-venv/bin/activate
python -m pip install platformio==6.1.18
export PLATFORMIO_CORE_DIR=/path/to/isolated-platformio-cache
git -C /path/to/reviewed-firmware worktree add --detach /path/to/new-boundary COMMIT
cd /path/to/new-boundary
pio run
```

Activate the environment before invoking PlatformIO. Calling its executable by
absolute path alone does not put `python` on the child process search path.
The F103 build invokes `python Marlin/src/HAL/STM32F1/build_flags.py`; without
activation it failed here with `python: command not found`. Repeating with the
virtual environment on PATH allowed that build to complete, without source edits.
The failed attempt remains in local evidence.

These commands build only; never add an upload target. The F103 and F401 outputs
are different board candidates, not interchangeable printer updates. The actual
development board and recovery procedure are still unqualified.

Builds retain warnings from this older source. A successful compiler exit is not
proof of warning-free code or runtime correctness. Native and host behavior tests
are a separate qualification; the latest full host suite used F3, not F1 or F2.
Local caches were available. This is not an independent package-download test or
a claim of reproducible binary bytes across build dates and paths.

All four boundaries passed in the recorded run. Exact commits, trees, sizes,
resource use and hashes are in [the build record](../validation/firmware-stack-stage76.json).
Tracked source was unchanged in every worktree. Build logs remain local; their
hashes are included for correlation. No artifact was uploaded or flashed.

The fresh F3 binary has the same 165,320-byte size as the retained candidate
but a different hash. Comparison found exactly four changed bytes, all in the
M115 build-time string (`13:44:37` versus `17:07:32`). Substituting that string
makes the files identical. The source embeds `__TIME__`; builds are therefore
not byte-identical by default. The earlier candidate remains preserved.
