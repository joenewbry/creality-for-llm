# Contribution delivery and review stack

Updated October 26, 2026. Sixteen host and six firmware pull requests are open as drafts in the owner's repositories. Each PR names its predecessor and validation evidence. They are review candidates, not merged releases or accepted upstream contributions.

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

| [H7](https://github.com/joenewbry/creality-for-llm/pull/16) | Upstream Marlin submission and qualification evidence | `stack/h6-camera-broker` |

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

The full installed-package suite on Linux aarch64/Python 3.11.13/Clang 15.0.7 passed 876 tests with zero skips. [Pinned qualification](../validation/linux-package-stage111.json) retains initial failures and focused correction evidence. The first ten public review trees match their tested local review trees exactly; later runtime/test files match the final qualified source. The public-source focused Mac check passed 55 tests with 16 Linux-only skips. These tests use virtual printers, native hardware substitutes and software terminals.

The development board, processor, screen, recovery procedure and sensor fault remain unresolved. No firmware is flashed and no physical production adapter is commissioned. The protected active printer remains excluded. The camera broker and tools pass 92 focused software checks in development and clean export; native capture/control is pending. the original CAD prototype passes four mesh checks and two conditional viewing checks, with all physical qualification flags false.

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

