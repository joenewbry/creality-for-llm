# Contribution delivery and review stack

Updated October 27, 2026. Thirty-two host, six earlier Creality firmware and four maintained-source firmware pull requests are open as drafts in the owner's repositories. Each PR names its predecessor and validation evidence. They are review candidates, not merged releases or accepted upstream contributions.

## Repository relationships

[Host source](https://github.com/joenewbry/creality-for-llm) is one cross-platform codebase; `llm-control` contains the aggregate stack and `main` is the minimal review bootstrap. Mac/Linux differences belong to discovery, serial ownership/I/O and camera backends. There are no separate Mac and Linux forks.

[Firmware fork](https://github.com/joenewbry/Ender-3S1-llm) forks CrealityOfficial/Ender-3S1. Its `s1-pro-baseline` pins the reviewed `s1_pro_plus` base; `llm-control` contains our candidate. Firmware choice follows board, processor, screen and bootloader, independent of host OS. The native host tests fetch this public firmware at commit `98aa03248337b8039a09bbc1a29f33432aed08ca`.

## Host stack

| PR | Focus | Depends on base branch |
| --- | --- | --- |
| [H1](https://github.com/joenewbry/creality-for-llm/pull/1) | Typed operation core | `main` |
| [H2a](https://github.com/joenewbry/creality-for-llm/pull/2) | Serial observations and native prompt protocol | `stack/h1-core` |
| [H2b](https://github.com/joenewbry/creality-for-llm/pull/3) | Shared frame transport and prompt operations | `stack/h2a-observations` |
| [H2c](https://github.com/joenewbry/creality-for-llm/pull/4) | Pause policy and restoration planning | `stack/h2b-prompt-transport` |
| [H2d](https://github.com/joenewbry/creality-for-llm/pull/5) | Durable pause effects and restoration handoff | `stack/h2c-pause-policy` |
| [H2e](https://github.com/joenewbry/creality-for-llm/pull/6) | Virtual job and setup adapters | `stack/h2d-pause-effects` |
| [H2f](https://github.com/joenewbry/creality-for-llm/pull/7) | Streamed virtual runtime and managed resume | `stack/h2e-virtual-adapters` |
| [H3](https://github.com/joenewbry/creality-for-llm/pull/8) | MCP tools and model evaluation | `stack/h2f-virtual-runtime` |
| [H4a](https://github.com/joenewbry/creality-for-llm/pull/9) | Native firmware and package qualification | `stack/h3-llm-interface` |
| [H4b](https://github.com/joenewbry/creality-for-llm/pull/10) | Complete archives and assigned Linux descriptor transport | `stack/h4a-qualification` |
| [H4c](https://github.com/joenewbry/creality-for-llm/pull/11) | Read-only Mac USB connection discovery | `stack/h4b-package-and-transport` |
| [H4d](https://github.com/joenewbry/creality-for-llm/pull/12) | Revalidate terminal authority after readiness waits | `stack/h4c-macos-discovery` |
| [H4e](https://github.com/joenewbry/creality-for-llm/pull/13) | Qualify streamed restart and external-client concurrency | `stack/h4d-readiness-authority` |
| [H5](https://github.com/joenewbry/creality-for-llm/pull/14) | Camera interface, architecture and overview mount prototype | `stack/h4e-restart-and-clients` |
| [H6](https://github.com/joenewbry/creality-for-llm/pull/15) | Assigned camera broker and durable controls | `stack/h5-camera-design` |
| [H7](https://github.com/joenewbry/creality-for-llm/pull/16) | Upstream Marlin contribution and qualification evidence | `stack/h6-camera-broker` |
| [H8](https://github.com/joenewbry/creality-for-llm/pull/17) | Bounded capture-owner HTTP client and frame invalidation | `stack/h7-upstream-contribution` |
| [H9](https://github.com/joenewbry/creality-for-llm/pull/18) | Capture-owner API with atomic frame and control evidence | `stack/h8-capture-owner-http` |
| [H10](https://github.com/joenewbry/creality-for-llm/pull/19) | Lease-first backend startup and full goal audit | `stack/h9-capture-owner-api` |
| [H11](https://github.com/joenewbry/creality-for-llm/pull/20) | Compose assigned Linux prompt transport and reject late receipts | `stack/h10-startup-and-audit` |
| [H12](https://github.com/joenewbry/creality-for-llm/pull/21) | Preserve buffered prompt arrival age and current deadline checks | `stack/h11-assigned-prompt-transport` |
| [H13](https://github.com/joenewbry/creality-for-llm/pull/22) | Share one assigned receive and dispatch owner across job and prompt copies | `stack/h12-receive-arrival-age` |
| [H14](https://github.com/joenewbry/creality-for-llm/pull/23) | Attach pinned sender to one owned endpoint with fair bounded admission | `stack/h13-shared-receive-owner` |
| [H15](https://github.com/joenewbry/creality-for-llm/pull/24) | Journal assigned job and prompt tools with immutable staged bytes and scoped evidence | `stack/h14-assigned-sender` |
| [H16](https://github.com/joenewbry/creality-for-llm/pull/25) | Preserve assigned serial source identity and arrival age in durable reports | `stack/h15-assigned-job-tools` |
| [H17](https://github.com/joenewbry/creality-for-llm/pull/26) | Retire unqualified assigned resend and timeout recovery before further writes | `stack/h16-assigned-observations` |
| [H18](https://github.com/joenewbry/creality-for-llm/pull/27) | Record development identification, read-only observations and deferred inspection | `stack/h17-assigned-replay-boundary` |
| [H19](https://github.com/joenewbry/creality-for-llm/pull/28) | Retire assigned dispatch on source-bound sensor and firmware hazards | `stack/h18-development-identity` |
| [H20](https://github.com/joenewbry/creality-for-llm/pull/29) | Own assigned sender and prompt-consumer lifecycle | `stack/h19-source-hazard-interlock` |
| [H21](https://github.com/joenewbry/creality-for-llm/pull/30) | Verify stock recovery provenance and firmware feature limits | `stack/h20-assigned-lifecycle` |
| [H22](https://github.com/joenewbry/creality-for-llm/pull/31) | Revalidate dispatch admission after journal commitment | `stack/h21-manufacturer-recovery` |
| [H23](https://github.com/joenewbry/creality-for-llm/pull/32) | Keep the sender closed after competing errors | `stack/h22-dispatch-admission` |
| [H24](https://github.com/joenewbry/creality-for-llm/pull/33) | Inspect exact Mac owner descriptors without opening devices | `stack/h23-sender-error-close` |
| [H25](https://github.com/joenewbry/creality-for-llm/pull/34) | Correlate controller-variable reports with journaled inspect tools | `stack/h24-macos-owner-metadata` |
| [H26](https://github.com/joenewbry/creality-for-llm/pull/35) | Exercise native getter and reports through assigned LLM tools | `stack/h25-machine-state` |

## Firmware stack

| PR | Focus | Depends on base branch |
| --- | --- | --- |
| [F1](https://github.com/joenewbry/Ender-3S1-llm/pull/1) | Repair baseline S1 Pro build compatibility | `s1-pro-baseline` |
| [F1b](https://github.com/joenewbry/Ender-3S1-llm/pull/2) | Select the F401 development build configuration | `stack/f1-build-repairs` |
| [F2](https://github.com/joenewbry/Ender-3S1-llm/pull/3) | Bind remote prompt replies to the active firmware prompt | `stack/f1b-f401-candidate` |
| [F3](https://github.com/joenewbry/Ender-3S1-llm/pull/4) | Add bounded prompt frames and correlated UART receipts | `stack/f2-prompt-identity` |
| [F4](https://github.com/joenewbry/Ender-3S1-llm/pull/5) | Keep heater watch armed when its deadline wraps to zero | `stack/f3-prompt-transport` |
| [F5](https://github.com/joenewbry/Ender-3S1-llm/pull/6) | Backport Marlin near-target thermal protection handoff | `stack/f4-watch-deadline` |

## Validation and physical limits

The earlier public snapshot `ad89416ec5362244803ed6bc364021a1147fddfe` passes the complete installed-package Linux aarch64/Python 3.11.13 suite: **1,175 passed, zero skips**. Tests run outside the source tree; installed runtime bytes and all 99 locked dependencies are verified. [Earlier qualification](../validation/linux-package-stage137.json) includes post-commit admission and the dependent sender error-close correction. Fourteen admission regressions and three sender regressions reproduce against the prior source. Failed attempts remain recorded, including the long-held-lock timeout and an MCP pause-fixture race. The final software-only fixture gates a virtual move until actual pause dispatch; state/deadline/physical gates are not weakened. [Admission contract](dispatch-admission.md) and [sender lifecycle](sender-error-close.md) define the boundaries. Physical/provider/driver/printing qualification remains unfinished. The later Mac owner-metadata component is separately qualified and does not change the existing sender/service modules. It is outside this full-suite snapshot. See [Mac owner metadata](macos-owners.md) and its scoped validation record.

The first ten public review trees preserve their originally tested trees. [The goal audit](goal-audit.md) lists unmet requirements, and [the printing plan](printing-qualification-plan.md) separates post-repair physical testing from software qualification. The readiness-admission reference does not certify hardware or clear unknown receipts.

The development board, processor, screen, recovery procedure and sensor fault remain unresolved. No firmware is flashed and no physical production adapter is commissioned. The protected active printer remains excluded. The broker, pinned HTTP/TLS client and owner-side API pass 147 focused source/export checks and 138 camera/tool/service checks against an installed wheel. The owner module is not deployed; native acquisition/control callbacks, identity/clock/profile evidence and device isolation remain unqualified. the original CAD prototype passes four mesh checks and two conditional viewing checks, with all physical qualification flags false.

## Ecosystem contribution status

[Marlin PR 28753](https://github.com/MarlinFirmware/Marlin/pull/28753) submits the focused heater-watch zero-deadline fix and five regressions upstream. Candidate `c8139a8c96a83271bad5fe541ea15b68143fa2a0` is rebased onto `cce3c1218cba190168f432d5ffbedfb43d95515b`. The exact candidate passes 168 native cases and AVR mega2560 / 32-bit DUE builds, including the existing chamber-enabled configuration. [Submission evidence](../validation/upstream-submission-stage115.json) records scope and log hashes. It is open for upstream review; CI/maintainer review and acceptance are separate from local qualification. The AI assistance is disclosed. No physical heater or full simulator test is claimed.

The near-target handoff backport retains upstream attribution. Owner-fork review PRs do not imply Creality acceptance. The standalone Marlin PR depends on neither the host tools nor the old Creality source.

No generated patch or description will be submitted to OctoPrint: the checked contribution policy excludes predominantly AI-authored contributions. The virtual integration stays in this host project. PrintMCP and Toka remain comparison/adapter targets, with no submission claimed.

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



## Maintained S1 Pro v040 firmware stack

The owner's existing [Marlin fork](https://github.com/joenewbry/Marlin) retains
its default branch. `reference/toka-v040` pins ThomasToka's actual v040 release
source; this separate lineage retains input shaping. The earlier Creality stack
and the host's firmware-test pin are unchanged. These draft changes have no
physical commissioning or full thermal-manager qualification.

| PR | Focus | Depends on base branch |
| --- | --- | --- |
| [M1](https://github.com/joenewbry/Marlin/pull/1) | Add printing-only S1 Pro v040 build profile | `reference/toka-v040` |
| [M2](https://github.com/joenewbry/Marlin/pull/2) | Port session-bound LLM prompt control to S1 Pro v040 | `stack/v040-f1-profile` |
| [M3](https://github.com/joenewbry/Marlin/pull/3) | Carry heater watch protection fixes onto S1 Pro v040 | `stack/v040-f2-protocol` |
| [M4](https://github.com/joenewbry/Marlin/pull/4) | Complete thermal protection handoff at runtime | `stack/v040-f3-thermal` |

The first three source layers need the runtime correction in M4 to complete
the near-target backport. The corrected candidate builds with 13,600 bytes free;
32 actual-source thermal paths, 11 prompt cases, six watch-template scenarios,
three threshold checks and 66 host checks pass. All physical dependencies are recording stubs;
full manager/physical shutdown and printing remain unqualified. The current 1,175-test
installed Linux qualification still uses the older firmware-test pin; v040 has
separate focused native/build qualification.
[Detailed current source and limits](maintained-firmware.md).


Current host snapshot `c8d079c` passes 1,271 installed Linux tests with one Mac-native
file-owner fixture skipped, zero failures/errors; installed runtime bytes and all
99 locked dependencies are verified. [Qualification](../validation/linux-package-stage139.json)
includes the Mac metadata component and controller-state reader/parser/tools.
The [new firmware PR5](https://github.com/joenewbry/Marlin/pull/5) depends on4 and
supplies the optional state report; its source `0f8d1ca` separately passes four
getter variants, five state-wire cases, eleven existing prompt cases and the
F401 build. No host firmware-test repin or physical commissioning is implied.


[H26](https://github.com/joenewbry/creality-for-llm/pull/35) adds the explicit
16-file pinned native input and a combined actual getter/wire -> assigned sender
-> journaled read scenario. Exact focused snapshot `d585922` passes 171 installed
cases with no skips/failures; all 99 dependencies, all 16 native inputs and installed
runtime bytes match. Production runtime remains unchanged from the earlier full
`c8d079c` result. Hardware stubs and virtual motion are distinct models; this
proof does not complete the physical provider or commissioning.

## Assigned completion barrier follow-up

The operation-bound barrier primitive adds an actual shared-write trace and
durable source-marker correlation. Linux source validation passes 195 checks;
Mac passes 184 with 11 Linux-only checks skipped. Nine export checks pass.
See [implementation and integration limits](assigned-completion-barrier.md) and
[scoped validation](../validation/assigned-barrier-stage141.json). This is not a
new installed-package/full-suite result. Pinned-sender provider integration and
physical commissioning remain open; neither printer was accessed.

## Sender pause/cancel barrier integration

The optional assigned-job path now waits for its own source-correlated barrier
before completing a pause or cancellation receipt and rechecks it after external
evidence callbacks. The pinned-sender software-terminal scenarios exercise an
80-move pause/resume, separate cancellation marker, interleaving refusal and
maintained native prompt/state handlers with modeled movement/barrier commands.
Linux passes 203 source checks, Mac 188 with 15 Linux-only skips; nine export
checks pass. See [scoped validation](../validation/sender-barrier-stage142.json).
Physical provider, EOF lifecycle evidence, fresh resume and commissioning remain
open. Neither printer nor any camera was accessed.

## Fresh resume admission

The optional assigned path now keeps the sender paused until a new resume marker
and current checkpoint qualify. Source authority and original age are checked
after the provider and after acquiring the stream guard. Only the two exact
read-only query frames may explain transport-number advancement; file cursor and
all other proofs remain strict. The resume effect is issued once and its final
transition receipt retains admission evidence. Linux passes 213 source checks,
Mac 197 with 16 Linux-only skips. Plain and numbered sender scenarios complete all
80 original modeled moves once. See [qualification](../validation/fresh-resume-stage143.json)
and [design](assigned-completion-barrier.md). Physical provider, final print
completion evidence and commissioning remain open.

## Final lifecycle completion

The optional assigned path now measures a bounded final evidence window from the
original file-done callback, checks the consumed staged file, and requires a
completed start receipt, new source marker and external proof before completion.
The start dispatch deadline remains unchanged; command deadlines cannot use the
lifecycle window. Final evidence is rechecked after callbacks/persistence and
retained as a historical observation. Linux passes 233 source checks; Mac 215
with 18 Linux-only skips. A modeled 80-move job completes after the start deadline;
withholding its final marker leaves the job unknown even after all moves finish.
See [qualification](../validation/lifecycle-completion-stage144.json) and
[design](assigned-completion-barrier.md). Hardware provider and commissioning
remain unfinished.

## Bounded cold command screening

Added immutable cold command policy, strict nonprinting XYZ plan validation and
a source-backed preview using a completed bound inspect. The optional assigned
gate resamples source age after external preflight and before staging/start.
The read-only job.cold_preflight interface exposes neither raw commands nor policy
arguments. It never grants physical authority. Linux passes 323 source checks;
Mac 305 with 18 Linux-only skips, including an official Model Context Protocol
client over memory streams. The fresh-inspection coordinator and full hardware/
configuration/continuity provider remain unfinished. See [design and scope](cold-preflight.md)
and [qualification](../validation/cold-preflight-stage145.json).

## Controller-managed cold inspection

The optional automatic cold admission mode journals and dispatches a fresh child
inspection for stage/start. The existing controller loop screens the correlated
source and external proofs before a job effect, so admission no longer depends
on a model making its next call within one second. Original source-age limits
remain enforced, with no physical grant. The child shares the parent deadline,
and duplicates, lost workflow memory, missing state lines, stale source, provider
errors and interrupted effects are covered. Full hardware/configuration/continuity
qualification and ordered checkpoint inspection remain unfinished. See
[design](cold-preflight.md) and [evidence](../validation/fresh-inspection-stage146.json).

## Post-drain checkpoint inspection

Optional pause/resume admission now acquires distinct controller reports after
its fresh drain marker. A committed child inspect permits one exact, adjacent
prompt-lane frame; unrelated, partial or replayed frames still invalidate the
barrier. Source copies reach the required external checkpoint provider and are
rechecked afterward. Controller-loop progress supports delayed model collection.
Physical checkpoint proofs, existing immutable comparison and pause intervals
remain required. See [design and limits](checkpoint-inspection.md) and
[qualification evidence](../validation/checkpoint-inspection-stage147.json).

## Owned firmware/settings report input

An optional journaled pre-job inspection collects fixed M115/M503 reports between
unique markers on the same owned sender/capture. Complete write order and original
source times are checked; incomplete or uncertain intervals cannot complete.
An official protocol client exercises the bounded tool. Reported settings and
firmware declarations are retained with distinct source/settings digests, always
with complete-configuration/binary/readiness/resume qualification false. Runtime
idle timers, post-drain coverage and the actual physical provider remain open.
See [report design and limits](configuration-report.md) and
[qualification evidence](../validation/configuration-report-stage149.json).


## Fresh settings in pause/resume admission

Optional compound checkpoint mode acquires a fresh M115/M503 interval plus
controller state for each pause/resume parent. It rejects changed reported
settings before invoking the physical provider, copies both inputs and accounts
for only five exact numbered read frames plus one unnumbered state query.
Physical/configuration coverage remains separate and incomplete. No raw tool
is added. See [checkpoint design](checkpoint-inspection.md).

## Live controller inactivity input

The maintained firmware V2 report samples raw activity/M84/M85 counters, compiled
idle-axis mask and production predicates without resetting timers. The host
validates unsigned arithmetic and retires contradictory owned reports. V1 lacks
timer coverage explicitly. Actual native getter/wire/tool integration and candidate
cross-build evidence remain distinct from physical qualification. Firmware PR6
depends on5; the companion host change depends on44. See [state scope](machine-state.md).

## Cold evidence admission and pre-open checker

Implemented strict cold profile and immutable physical-observation contracts,
actual owned source/checkpoint mapping, transition evidence and pre-open readiness
validation. Physical grants are rechecked after persistence immediately before
file selection/start. Tests exercise the real assigned backend and journal with
synthetic qualification/physical inputs; no actual profile, producer or deployment
is installed. See [scope and integration](cold-provider.md).
