# Contribution targets and review stack

Checked October 25, 2026. This is our project's contribution plan, not an
upstream submission or a claim that maintainers have accepted these changes.
No PRs have been opened. Hardware commissioning and other goal requirements
remain unfinished; publish the requested sanitized stack at the end.

## Verified targets

| Project | Checked revision / branch | Disposition |
| --- | --- | --- |
| CrealityOfficial/Ender-3S1 | `7fffa9270ff8fb5ee208a5b04f832e20bab19c60`, `s1_pro_plus` | Still matches our pinned controller base. Keep the original attribution and license. Prepare our firmware stack against this branch; do not use the different default `main` as its base. |
| Creality S1 Pro screen | `25b16c4cfb240c5a889dc59352beda3f6f3f4caf`, `3S1_PRO_SCREEN` | Still matches the pinned screen project. Full screen rebuild and physical behavior are not qualified. |
| ThomasToka/MarlinFirmware | `cd34c7556edac985879816594e2fc3700d3f3071`, `Firmware-Binaries` | Download/release repository, not the source contribution target. Its README points to the separate source fork below. |
| ThomasToka/Marlin | `306bcfdb3b53002da08ae3880de9ce39ca751d2d`, `MARLIN-E3S1PROFORK-BYTT` | Actual source comparison target. Read its inherited contribution guide; a port/reproduction against this source is still needed before proposing our old-source fixes. |
| SourceBox-LLC/PrintMCP | `fee17fb2615e638848b840fc0c5a5a98906ecfd2`, `master` | Potential typed-operation adapter target. No AGENTS or CONTRIBUTING file found in the complete tree at this revision. That is not evidence of maintainer agreement. Adapter compatibility work remains. |
| OctoPrint/OctoPrint | `64d8cd115f2a1283351ec66ab4ef9357ceb86c24`, `dev` | Do not submit our AI-generated patch or issue. Current contribution policy requires predominantly human authorship and prohibits AI-written PR descriptions. Keep our local virtual-only integration adapter separate. |

Source links: [Creality branches](https://github.com/CrealityOfficial/Ender-3S1/branches),
[ThomasToka source pointer](https://github.com/ThomasToka/MarlinFirmware/blob/cd34c7556edac985879816594e2fc3700d3f3071/README.md),
[ThomasToka contribution guide](https://github.com/ThomasToka/Marlin/blob/306bcfdb3b53002da08ae3880de9ce39ca751d2d/.github/contributing.md),
[PrintMCP source](https://github.com/SourceBox-LLC/PrintMCP/tree/fee17fb2615e638848b840fc0c5a5a98906ecfd2),
[OctoPrint contribution policy](https://github.com/OctoPrint/OctoPrint/blob/64d8cd115f2a1283351ec66ab4ef9357ceb86c24/CONTRIBUTING.md#on-ai-use-when-contributing-to-octoprint).

## OctoPrint pause scheduling finding

The verified race and local adjustment target OctoPrint 1.11.8 only. At the
checked dev revision, `octoprint.util.comm` is now a deprecated re-export of
`octoprint.plugins.serial_connector.serial_comm`. A syntax-tree comparison finds
`sendCommand`, `setPause`, and `cancelPrint` unchanged from our pinned version;
`_continue_sending` differs. This is a source comparison, not reproduction of
the race in the current runtime. Do not present it as a confirmed current bug.

Pointers for a human contributor: [serial connector](https://github.com/OctoPrint/OctoPrint/blob/64d8cd115f2a1283351ec66ab4ef9357ceb86c24/src/octoprint/plugins/serial_connector/serial_comm.py),
[agent-use rules](https://github.com/OctoPrint/OctoPrint/blob/64d8cd115f2a1283351ec66ab4ef9357ceb86c24/AGENTS.md),
and [contribution guide](https://github.com/OctoPrint/OctoPrint/blob/64d8cd115f2a1283351ec66ab4ef9357ceb86c24/CONTRIBUTING.md).
The guide also requires current-version reproduction, checking existing issues,
unit tests and pre-commit checks. We have not performed those upstream checks.
Our forced early-ack regression and existing adapter are evidence for our own
integration, not a ready-to-submit OctoPrint patch.

## Intended stack in our repositories

Use clean publication branches rather than pushing the private working history.
Keep firmware and host repositories separate. Each numbered PR must name its
base, dependency links, exact validation, and unqualified behavior. The table
identifies review units; final boundaries may change to keep each PR buildable.

| PR | Base / dependencies | Reviewable change | Required evidence |
| --- | --- | --- | --- |
| F1: Baseline build repairs | Pinned Creality controller | Independent build-script repairs, no protocol feature | Original failure and repaired build; verify fixes are still applicable to any upstream target |
| F1b: Development board configuration | F1 | Explicit F401 build selection; local development candidate only | Build evidence; no claim that the connected board is F401; omit this selection from unrelated upstream fixes |
| F2: Prompt identity and lifecycle | F1b | Stable prompt identity, stale-response rejection, wait/screen invalidation | Native parser, wait, and RTS screen-handler tests |
| F3: Bounded prompt transport | F2 | Versioned frames, mailbox, idle dispatch, correlated receipts, primary-UART routing | Framing/overflow/duplicate tests, ordinary-ack isolation, emergency-parser tests, F401 build hash |
| H1: Typed operation core | Clean host base | Device assignment gates, one-owner lease, journal, request validation and checkpoint model | Isolation/idempotency/restart/invalid-checkpoint tests; no hardware discovery fallback |
| H2: Virtual jobs and native prompts | H1; pinned F5 source for current native tests | Job and prompt adapters, shared writer/receiver, combined authority, virtual-only sender adjustment | Full blocker matrix and native/virtual integration; explicit unsupported cases |
| H3: MCP and model demonstration | H2 | Typed tool server, bounded model runner, independent evaluator | Actual MCP client tests, retained failed and successful model evidence, spend accounting |
| H4: Development release and explanation | F5 and H3 | Reproducible setup, result matrix, rollback/qualification instructions and page links | Sanitized export rebuild, hardware checks where feasible, visual page review and final PR links |

Passing regressions do not make all required evidence in the table complete. Further setup/maintenance operations, extended pause policy,
remaining blockers and physical commissioning still need work.

## Publication checks

Before creating public branches, export only intended source, tests, manifests
and sanitized documentation; include the correct licenses and dependency
attributions. Exclude local device assignments/transcripts, network addresses,
credentials, private evidence and unrelated history. Replace machine-specific
paths with reproducible setup instructions. The current test harness expects a
local firmware checkout, so the public setup must fetch its exact published
revision before tests can be called reproducible.

Build and test every stack boundary in an isolated checkout. Confirm GitHub
base/head branches and dependency links after creation. Suitable upstream
contributions require target-specific reproduction and policy checks; our own
PR stack does not establish upstream acceptance. Do not route generated work
through another person or repository to evade OctoPrint's authorship policy.

Use [the committed source exporter](publication.md) to prepare a review directory without private history. Its allowlist and pattern checks supplement manual review; they do not establish publication readiness.


Review of actual firmware history separates the board selection from independent
repairs. Candidate boundaries are F1`362508639f39f501dc44c608e42e74e930a0b578`,
F1b`93ea5539e3b30689c730bb4e5e691564d44882a3`,
F2`7ec664f7ceb5d3bfb754aa0778ab786dde953274`, and
F3`a5c1cabc478e52c5e2ec00a00686e2b573a912e4`.
These are local review inputs, not published branches. Their independent builds
are recorded below. The F401 selection changes both configuration and the default
build environment; it must not be described as a generic upstream bug fix.

An earlier clean host export reproduced 641 tests at pinned host `8d8453f`;
see validation/clean-export-8d8453f.json. This validates the complete exported
snapshot, not each H1–H4 boundary. Boundary-specific builds/tests, manual privacy
review, public firmware availability and final dependency links remain required.

Stage 76 now independently builds F1, F1b, F2 and F3 in detached worktrees,
using each boundary's default environment. All four passed with unchanged
tracked source. See [boundary build instructions and limits](firmware-stack-builds.md)
and [sanitized evidence](../validation/firmware-stack-stage76.json).
This supersedes the earlier statement that these boundaries lacked fresh builds;
it does not supply native behavior tests at every boundary, host-stack boundary
tests, physical qualification, public branches or upstream compatibility.

Stage 77 reproduced the later complete host export at `86bde0b`: 151 allowlisted
files and 716 passing tests in a new environment, with unchanged source hashes.
See [the clean-export record](../validation/clean-export-86bde0b.json). This is
still complete-snapshot validation, not independently tested H1–H4 boundaries.

F4 candidate: `126e171` reserves the disabled-watch sentinel when the initial
heating deadline wraps to zero. Native nozzle and bed regressions fail on F3 and
pass with the correction; the full F401 build passes. The two-line patch applies
cleanly to the pinned Creality base and is logically independent of prompt
transport, despite following F3 in the local history. Keep it separate from board
selection and protocol features in publication. Check maintained target source,
contribution guidance and reproduction before proposing it upstream. Evidence:
[native watch qualification](../validation/native-watch-stage79.json).


## Heating-watch upstream review

The zero-deadline defect reproduces in extracted `HeaterWatch` templates from
Marlin `a49fef81141d91d4b261a64479e46a00092ea05a` (`bugfix-2.1.x`) and
ThomasToka `306bcfdb3b53002da08ae3880de9ce39ca751d2d`. The candidate sentinel
correction passes synthetic 40/180-second cases for exact-zero expiry, ordinary
expiry, counter wrap, rise thresholds and intentional disarm. Downloaded headers
remain unchanged; the checker patches only a temporary compilation input.
Evidence: [target-specific results](../validation/upstream-watch-stage80.json).

Reproduce against an explicitly downloaded, revision-pinned header:

```sh
python3 scripts/check_heater_watch.py --header /path/to/temperature.h --output /path/to/result.json
```

These are isolated template checks with synthetic parameters, not target firmware
builds or full-manager simulations. Current Marlin changes the near-target watch
behavior, so the old Creality extraction cannot stand in for its runtime tests.

[Marlin's current contribution policy](https://github.com/MarlinFirmware/Marlin/blob/a49fef81141d91d4b261a64479e46a00092ea05a/.github/contributing.md)
accepts AI-assisted work and requires reviewable changes and honest validation.
Its [agent guidance](https://github.com/MarlinFirmware/Marlin/blob/a49fef81141d91d4b261a64479e46a00092ea05a/AGENTS.md)
requires working-branch contributions, affected-environment builds and a unit
regression where possible. Prepare the zero-deadline fix independently, with
upstream-format tests and builds, before submission. No upstream PR is open.

Searches found [merged PR 28695](https://github.com/MarlinFirmware/Marlin/pull/28695),
a distinct fix that aligns the initial-heating watch and runaway thresholds near
target. Our older Creality source and the checked ThomasToka template lack this
change. Backport and qualify it separately, preserving upstream attribution;
do not present it as our discovery or fold it into the zero-deadline patch.
It remains a physical-heating release prerequisite. Searches are not exhaustive:
the broad heater/wrap query returned only the first 30 of 63 matches.


F5 backport candidate: firmware `98aa032` applies upstream PR28695's near-target
handoff fix while preserving the F4 zero-deadline correction. This is attributed
upstream work for our older Creality fork, not a new fix to resubmit to Marlin.
Ten combined native nozzle/bed scenarios pass after eight failed on the prior
revision. See [handoff evidence](../validation/native-handoff-stage81.json).
Chamber/adaptive-fan and full-manager/physical behavior remain unqualified.


## Standalone Marlin timer candidate

The local review branch `fix/heater-watch-zero-deadline` is based directly on
Marlin `a49fef81141d91d4b261a64479e46a00092ea05a`. Candidate commit
`7c3c9ee84de78d3cb59b377a518beee507bf7970` changes only two lines in the current
HeaterWatch implementation. It carries none of our Creality board, prompt or
transport changes. The [mailbox patch](../contributions/marlin-heater-watch-zero.patch)
is a review artifact, not a submitted PR. Its Marlin source remains GPL-3.0-or-later.

AVR `mega2560` with committed defaults and 32-bit `DUE` with the upstream
`buildroot/tests/DUE/config-01.ini` configuration build successfully. The DUE
profile includes a heated chamber. Exact candidate/configuration/artifact hashes
and limits are in [the build record](../validation/upstream-watch-builds-stage82.json).
These cross-builds do not constitute physical or full simulator testing.

Target-native regression results for the updated candidate are recorded below.
The Mac build environment lacked GNU GCC and a usable Docker executable;
native testing therefore used an isolated Linux environment without replacing
global compiler links on the Mac.

For DUE, put both the Python build environment and repository `buildroot/bin`
on PATH before `mftest -t DUE -n 1 -y`. The helper's final false `IS_MEGA_1`
condition returns shell status 1 even when PlatformIO succeeds. The same
configured build was therefore verified directly with `pio run -e DUE`, which
returned zero. The initial missing-`opt_set` attempt and corrected run are both
retained. Do not generalize a wrapper failure into a compiler failure or ignore
its exit code without checking the actual cause.


## Native upstream regression completed

Candidate `2e93c50` adds five target-native `MARLIN_TEST` cases and an optional
`now` argument to `HeaterWatch::restart`, defaulting to `millis()`. Existing calls
remain valid; tests can supply exact timestamps without global clock overrides.
The [test patch](../contributions/marlin-heater-watch-native-tests.patch) follows
the earlier two-line patch; these belong in one focused upstream PR, not separate
PRs that leave the fix without its regression.

An isolated Linux aarch64 environment with GCC 11.4.0, PlatformIO 6.1.18 and Unity
2.6.1 ran `make unit-test-single-local UNIT_TEST_CONFIG=default`. Removing only
the guard produced exactly two failures (zero deadlines for nozzle/bed periods)
and 166 passes. Restoring it produced 168 passes, including all five new cases.
The updated method signature also passes Mega2560 and DUE/config-01 cross-builds.
See [native before/after evidence](../validation/upstream-watch-native-stage83.json).
This supersedes the earlier pending-native-test status for the default config.

Other native configurations and the full simulator have not run. No physical
printer was accessed. Final review, branch refresh, complete duplicate search,
clean PR packaging and upstream submission remain outstanding. No maintainer
acceptance is implied by these checks.

Stage 85 reproduces the complete host snapshot `4b768f9`: 170 allowlisted files,
775 passing tests, a fresh environment and independent pinned F5 firmware clone.
[The record](../validation/clean-export-4b768f9.json) verifies unchanged source
hashes and firmware worktree. It does not validate individual host PR boundaries
or publication readiness.

## Refreshed single-commit review candidate

Candidate `36406aafd8029b8375281d298ae983c373c399af` combines the timer fix and
its five regressions into one commit on checked `bugfix-2.1.x` base
`b84243d8f3337586ff0d9771a92a7828d5678731`. Its tree differs from the native-tested
candidate `2e93c50` only in two upstream distribution-date headers. Fresh Mega2560
and DUE/config-01 builds pass; the worktree is clean afterward. Native tests have
not rerun on this refreshed commit. The older patches remain historical evidence.

[Combined patch](../contributions/marlin-heater-watch-combined.patch),
[complete PR-template draft](../contributions/marlin-heater-watch-pr.md), and
[review/build evidence](../validation/upstream-review-stage87.json) are local
review artifacts. Nothing has been submitted. Native rerun, final review and
submission at the requested end of the project remain pending.

The latest search retrieved all 63 results for the earlier heater/wrap query,
plus all results for HeaterWatch, next_ms and two title/body queries. Related
changes concern the near-target gap, autotune, arithmetic width or unrelated
static-analysis fixes; no equivalent deadline-zero fix was identified in the
reviewed candidates. This supersedes the earlier first-30-only search limitation,
not the general limitation that searches cannot prove absence of duplicates.
Exact query strings, returned counts and scope are in the review record.

Stage 88 closes the exact-commit native-test gap for candidate `36406aa`:
a fresh isolated Linux aarch64 checkout passed all 168 default-configuration
cases, including the five heater-watch regressions. The restored worktree is
clean. See [exact-candidate native evidence](../validation/upstream-native-stage88.json).
The PR draft now distinguishes this run from the earlier before/after guard
experiment. Full simulator, other native configurations and physical tests
remain unperformed. Final review and submission remain pending; no PR is open.

## First independently assembled host boundary

`host-review-boundaries.json` explicitly maps H1 to committed allowlisted source.
`scripts/prepare_review_boundary.py` uses the sanitized exporter before creating
a new boundary directory; it refuses existing destinations and does not include
private Git history. H1 has its own README and package metadata, retains source
and license provenance, and contains no simulator or physical sender.

```sh
python3 scripts/prepare_review_boundary.py --revision 0acf2f5 --boundary H1 --destination /path/to/new-h1-review
```

At that source revision H1 contains 21 mapped files plus its boundary manifest.
It builds and installs in a new Python 3.11.13 environment. All 79 core tests pass
both from source and from a separate test directory importing the installed
package. Exported hashes remain unchanged. Clean local review root commit
`91e1d4b9022fbc059cb3c5a4726dadfd2da023e3` is independent of private development
history. See [boundary validation](../validation/host-core-boundary-stage90.json).
No remote or PR was created. This qualifies H1's operation core only; it does not
qualify optional thermal/restore integrations or any physical transport. H2–H4
still require their own boundaries and dependency validation before publication.

## H2 split into focused dependent reviews

H2 spans too many independent behaviors for one review. H2a now adds serial
observations, prompt-wire encoding/decoding and native prompt-handler tests to
H1. Virtual jobs, thermal/restore execution and the remaining adapters will be
subsequent H2 boundaries; H3/H4 retain their existing purposes.

```sh
python3 scripts/prepare_review_boundary.py --revision 3d65537 --boundary H2a --destination /path/to/new-h2a-review
```

H2a builds/installs independently and passes 164 tests both from source and from
a separate test directory importing the installed package. Native tests use
pinned firmware `98aa032` and substituted physical dependencies. No printer is
opened. The installed test run reuses that firmware fixture.

Local review commit `374913bf12ae1529f83330bc87e1b77e324eb2c9` has tested H1 commit
`91e1d4b9022fbc059cb3c5a4726dadfd2da023e3` as its actual Git parent, not merely a
stated dependency. Exported source hashes remain unchanged and the review
worktree is clean. [Validation record](../validation/host-observations-boundary-stage91.json).
The source boundary inherits H1 packaging metadata; this is not a distinct
published package version. Public firmware availability, later boundaries and
final privacy/attribution review remain publication requirements. No PR is open.

## H2b: shared frames and correlated prompt operations

H2b adds the shared complete-frame writer, bounded receive copy, prompt channel
and durable prompt backend. It supports the generic one-button user-wait profile;
it does not expose unrestricted G-code or claim that clearing a wait resumes a
physical print. Enclosing adapters must enforce identity and route all writes
through the shared boundary.

```sh
python3 scripts/prepare_review_boundary.py --revision b764a19 --boundary H2b --destination /path/to/new-h2b-review
```

Fresh package build and source tests pass all 256 cases. A separate test directory
imports the installed package and passes the same 256 cases, reusing pinned
native firmware inputs. Local review commit `d54d5eebe4b4ad8229e59950f63f43c8d5a51e27`
has H2a `374913bf12ae1529f83330bc87e1b77e324eb2c9` as its actual parent. Source
hashes remain unchanged and the review worktree is clean. See
[the boundary evidence](../validation/host-prompt-transport-boundary-stage92.json).
No public branch or PR is created. Virtual-job, pause/restore and remaining
runtime boundaries are still pending, as are physical commissioning and final
publication. This boundary's tests use pipes and native physical stubs, not USB.

## H2c: powered-pause policy and restoration planning

H2c adds command-mode tracking, cold-job preflight, bounded thermal-pause policy
and immutable in-place restoration planning. Native comparisons exercise
extracted mode/restoration functions; substituted parsers and physical stubs do
not qualify full firmware or real printer behavior.

```sh
python3 scripts/prepare_review_boundary.py --revision d04474a --boundary H2c --destination /path/to/new-h2c-review
```

Fresh source and separately installed-package test runs each pass all 355 cases.
The first export failed one test because a function-local preflight import was
missing; that failed result is retained, and the corrected export includes the
dependency. Review commit `1de3b57d698b7f09ea70da528e30d980a3d41b71` directly
follows H2b `d54d5eebe4b4ad8229e59950f63f43c8d5a51e27`. Inherited code is unchanged;
only boundary README/package metadata are replaced. Export hashes are unchanged
and the review worktree is clean. [Validation](../validation/host-pause-policy-boundary-stage93.json).
Command execution, sender integration, remaining runtime boundaries and physical
commissioning still need qualification. No public branch or PR was created.

## H2d: durable pause effects and restoration handoff

H2d adds stream evidence and one-use grants, durable thermal effects, a pause
monitor and restoration dispatch/completion handling. Stub-backend handoff tests
require fresh evidence and durable receipts before continuing a job. The full
managed workflow and actual virtual sender remain later integration units.

```sh
python3 scripts/prepare_review_boundary.py --revision 754d839 --boundary H2d --destination /path/to/new-h2d-review
```

All 412 boundary tests pass both from source and from an independently installed
package. Review commit `7f7b494aa27073b8eb6f3055600dd585bac47866` directly follows
H2c `1de3b57d698b7f09ea70da528e30d980a3d41b71`; inherited code remains unchanged.
The review worktree is clean and exported hashes are unchanged. See
[boundary evidence](../validation/host-pause-effects-boundary-stage94.json).

Eight combined-virtual interruption cases were moved intact into
`tests/test_combined_pause_interrupt.py` to keep core and backend integration
reviews independent. Their function and decorators are unchanged; the complete
project still passes 782 tests with six Linux-only skips on macOS. Those six
ownership cases were separately qualified on Linux in stage 89. The move does
not remove or weaken the complete integration suite. No physical access or
public PR is implied by these software results.

## H2e: virtual job and bounded setup adapter components

H2e adds the virtual job/combined-prompt backends, cold setup operations and
resend guard. Tests cover modeled completion, setup evidence, combined grant
revocation and durable host-crash boundaries. Three additional admission cases
reject a nonvirtual port/type combination before job access using inert objects.
They open no serial device.

```sh
python3 scripts/prepare_review_boundary.py --revision ba0e77f --boundary H2e --destination /path/to/new-h2e-review
```

With pinned OctoPrint 1.11.8 dependencies, H2e builds/installs independently and
passes all 480 tests from source and from a separate installed-package test
directory. Review commit `4753a2e6bfc345aeb9ed93d70d7289be9d8a55f5` directly follows
H2d `7f7b494aa27073b8eb6f3055600dd585bac47866`. Inherited code and exported hashes
are unchanged; the review worktree is clean. [Validation](../validation/host-virtual-adapters-boundary-stage95.json).
These are component tests with controlled sender/device substitutes, not a
qualification of the full serial runner. The full sender/managed-resume runtime
and later tool/release boundaries remain pending. No public PR is open.

## H2f: streamed virtual runtime and managed resume

H2f adds the virtual serial sender integration, typed JSON tools, managed resume,
pause status, thermal effects and virtual calibration. Ten end-to-end scenarios
exercise the actual OctoPrint sender plus virtual/native fixtures, alongside
component tests. They cover resume, native prompts, two pauses, managed hot
resume, cancellation, service shutdown, disconnect/reset, resend and fault
reporting. Service shutdown is not confirmed heater shutdown.

```sh
python3 scripts/prepare_review_boundary.py --revision 03804fc --boundary H2f --destination /path/to/new-h2f-review
```

Source and installed-package runs each pass all 577 tests. All twenty scenario
reports (ten per run) record zero physical serial-open attempts. Initial isolated
script invocation hid sibling helper imports and failed seven cases; corrected
standard invocation with inherited Python path overrides removed passes all.
Failed evidence is retained. The final export differs from tested source
`5902327` only in README wording; all code, tests and package inputs are verified
byte-identical. [Evidence](../validation/host-virtual-runtime-boundary-stage96.json).

Final local review commit `b7d305c47c95d9ec9a10c06d9f8a28c23295f08f` directly follows
H2e `4753a2e6bfc345aeb9ed93d70d7289be9d8a55f5`. The earlier local candidate
`2680453` is retained but superseded by the README correction. No public branch
or PR exists. H3 supplies the MCP adapter/model client; remaining qualification,
physical commissioning and final publication are still pending.

## H3: MCP tools and model evaluation

H3 adds the MCP adapter, actual MCP client/server tests, bounded model-demo code
and independent outcome evaluators. The ordinary suite invokes virtual/native
subprocesses and fixture evaluators, not a paid model API. Earlier paid model
runs remain separate evidence; no new live-model result is claimed here.

```sh
python3 scripts/prepare_review_boundary.py --revision 868ffd0 --boundary H3 --destination /path/to/new-h3-review
```

A fresh environment with all 99 locked dependencies builds/installs H3. All 659
tests pass from source and in a separate installed-package test directory, with
no failures or skips. Installed imports are verified from site-packages; native
firmware fixtures are reused for the second run. Source hashes and inherited
H2f code remain unchanged. Review commit `75e55da7e4e624f733da7a9a6009343b2a91c610`
directly follows H2f `b7d305c47c95d9ec9a10c06d9f8a28c23295f08f`; its worktree is
clean. [Qualification record](../validation/host-llm-interface-boundary-stage97.json).
H4's remaining qualification/release packaging, physical commissioning and final
public branches/PR/page delivery are unfinished. No public PR is open.

## H4a: qualification package, not a commissioned release

H4a collects the remaining native firmware-protection tests, Linux terminal
ownership primitive, release/recovery documentation, research, sanitized evidence
and contribution preparation. It explicitly leaves hardware qualification and
public delivery incomplete.

```sh
python3 scripts/prepare_review_boundary.py --revision bd83169 --boundary H4a --destination /path/to/new-h4a-review
```

Fresh source and installed-package runs each pass 795 tests with six Linux-only
skips on macOS. The four ownership source/test hashes match their earlier Linux
qualification exactly. No physical access or new model API call occurred. Review
commit `4e4392b5ffac95cffa0fdbfb05f88f0bb4fc0dea` directly follows H3
`75e55da7e4e624f733da7a9a6009343b2a91c610`. All nine local review commits have
verified linear ancestry, without private development history. The top commit
successfully re-exports its 209 allowlisted files. [Evidence](../validation/host-qualification-boundary-stage98.json).

Build/test qualification does not complete manual privacy/attribution review,
firmware publication, production USB integration, board/recovery validation,
physical non-printing checks, or final PR/page delivery. No public PR is open.
Later evidence additions update the explicit H4a mapping; the command above pins
the tested snapshot rather than claiming every subsequent documentation revision
was tested as a new release.
