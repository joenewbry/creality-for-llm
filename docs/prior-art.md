# Prior art and reuse decision

Reviewed October 25, 2026. These are documented capabilities, not hardware tests performed by this project.

| Project | Existing work | Relevance and remaining work |
| --- | --- | --- |
| [OctoPrint job API](https://docs.octoprint.org/en/main/api/job.html) | USB job control, explicit pause and resume actions | Candidate streaming engine. Always specify the action: the default toggles. Restart starts the file again. An HTTP success is not a physical completion receipt. |
| [OctoPrint G-code scripts](https://docs.octoprint.org/en/main/features/gcode_scripts.html) | Pause/resume hooks and an example that saves and restores position | Hooks default empty. The documented example applies to single-extruder, non-SD printing and needs position logging. Our firmware/profile must qualify parking, modes, thermal and motor behavior. |
| [OctoPrint virtual printer](https://docs.octoprint.org/en/main/development/virtual_printer.html) | Bundled software printer for communication testing | Prefer exercising a maintained sender with this simulator over proving only a homegrown mock. Keep simulator instances unable to open real serial ports. |
| [Klipper pause/resume](https://www.klipper3d.org/G-Codes.html#pause_resume) and [Moonraker](https://moonraker.readthedocs.io/en/latest/external_api/printer/) | Capture/restore position and structured printer APIs | Strong alternative architecture. Requires a separate firmware migration and screen/board qualification; not selected for flashing. |
| [ThomasToka S1 Pro firmware](https://github.com/ThomasToka/MarlinFirmware) | Maintained S1 Pro/Plus firmware and screen integration | Compare against its current source before upstreaming fixes to old Creality code. Screen type and release instructions matter. |
| [PrintMCP](https://github.com/SourceBox-LLC/PrintMCP) | Model Context Protocol tools over OctoPrint, including pause/resume/cancel | LLM printer control already exists. Evaluate an adapter/contribution rather than duplicating its whole pipeline. Our focus is state-bound actions and observable outcomes. |
| [mcp-3D-printer-server](https://github.com/DMontgomery40/mcp-3D-printer-server) | Multiple printer backends | Its README explicitly excludes pause/resume; cancel is not a resumable pause. Backend hardware qualification varies. |
| [klipper-mcp](https://github.com/MaxEllis/klipper-mcp) | LLM tools through Moonraker | Relevant if we choose Klipper; it is not a direct USB Marlin bridge. |

## Decision

Keep the isolated Marlin source investigation. Evaluate OctoPrint as the streaming engine and virtual-printer test bed before implementing another sender. Keep the typed operation journal, device assignment checks, and prompt protocol independent of that engine. This is a direction for an integration spike, not a claim that the adapter has shipped.

The unresolved problem is not merely sending a pause command. A reliable LLM interface needs idempotent requests, verified printer identity, stale-prompt rejection at firmware execution time, pause checkpoints, bounded recovery, and distinct accepted/completed/unknown outcomes. A host-generated prompt ID cannot make the stock tokenless M876 protocol race-free.

Do not propose old-version fixes upstream until reproducing them on the target project's current code and reading its contribution instructions. Public contributions must omit device logs, local paths, private network data and credentials.

Contribution targets and current policy checks are tracked in [contributions](contributions.md). ThomasToka's download repository points to [its separate source fork](https://github.com/ThomasToka/Marlin/tree/MARLIN-E3S1PROFORK-BYTT); do not target source changes at the binary repository.
