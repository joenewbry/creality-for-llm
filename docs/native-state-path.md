# Native state reports through assigned LLM tools

The combined fixture now runs the compiled maintained-source report across the
software serial link, actual pinned sender, source recorder and journaled tool
interface. Earlier tests checked the native serializer and assigned reader
separately; the bridge had only forwarded prompt receipts, dropping state lines.
It now forwards state lines without normalizing them or deciding their validity.
The real host parser retains responsibility for malformed/replayed reports.

The run starts a modeled job, holds a native firmware user wait at move ten, and
requests a pause. While that pause is pending, journaled inspect/bind/inspect
requests establish a session and obtain its source-correlated machine state.
`printer.machine_state` returns the native report for that inspect operation.
Reading again preserves the report's identity and arrival time, emits no new
prompt frame, leaves the ordinary sender line number unchanged, and does not
release the native wait. An explicit supported prompt response releases it;
the job reaches paused, resumes and finishes all 80 original modeled moves once.
The source file is changed after staging to exercise the immutable staged copy.

## Reproduce

Use the pinned Python 3.11 environment in [reproduction](reproduce.md), prepare
both fixture inputs, then run:

```
python scripts/prepare_state_fixture.py
python -m pytest -q tests/test_state_fixture.py tests/test_native_state_path.py
```

The combined scenario requires Linux's qualified software-terminal ownership
primitive. Mac can run the materializer/unit/native compiler checks but the
assigned software-terminal scenario is skipped. This is unrelated to which
firmware the physical printer needs.

`state-fixture-source.json` pins 16 specific public source files and their sizes
and SHA-256 values at maintained firmware `0f8d1ca`. This is a small native fixture,
separate from the existing `98aa` firmware-test source. The materializer accepts
no serial path, never uploads firmware and refuses an existing destination.
Downloads are explicitly invoked, not hidden in pytest. Input paths, bounds,
identity, digests and symlinks are checked. The builder verifies all inputs,
compiles verified Python source bytes rather than cached `.pyc` files, runs the
native checks, links the actual getter to the wire executable, and verifies the
source inputs again afterward. Six linked-getter/wire cases include a real
HostUI wait changing only after a supported response. This is trusted
pinned test code, not a sandbox for arbitrary local code.

## Evidence scope

The combined executable links the actual state getter and wire serializer.
Its sampling dependencies use hardware stubs; movement is modeled by a separate
virtual printer. Their position values deliberately differ in the test. The
native report therefore proves the source/transport/tool path, not an accurate
mechanical model or a physical checkpoint. The job provider remains explicitly
software only and still reads virtual-peer fields. Physical readiness, actual
configuration, operation-correlated completion barriers, motor power, sensor
health and part continuity remain required from the unfinished physical provider.
No printer or camera is discovered/opened and no firmware is installed.

The production runtime modules are unchanged from the full installed Linux
snapshot `c8d079c` (1,271 passed, one Mac-native fixture skipped). The new combined
fixture has its own focused qualification in [the record](../validation/native-state-path-stage140.json).
