# Bounded setup controls

The virtual job and combined job/prompt backends expose `printer.home` and
`printer.jog` through the same durable operation service and MCP tools. These
operations have no physical-device transport. Heating, extrusion, fan control,
physical calibration and persistent settings remain unqualified. The extended
hot simulator now exposes separate bounded temporary and saved settings tools;
see interface.md for their explicit software-only scope.

`printer.home` takes empty arguments and requests all XYZ axes. `printer.jog`
takes `axis` (`x`, `y` or `z`), `distance_mm` (absolute magnitude 0.001–10) and
`feedrate_mm_min` (1–600). A jog requires a previously completed home and unchanged
simulator coordinates. The software envelope is X/Y 0–220 mm and Z 0–270 mm;
it is a test envelope, not a measured obstacle-free hardware workspace.

Both operations require the idle sender before the first job in this process,
zero heater targets, millimeter units, an empty movement queue and no known
fault or uncertain setup operation. They are rejected during and after jobs,
including paused jobs: setup homing must not become a way to home into an
existing print or alter a pause checkpoint. Setup after cancellation and other
maintenance lifecycles still need explicit qualification.

The adapter sets absolute axis mode and submits a bounded command sequence,
then M400 and a unique M118 echo marker through the existing sender. It waits
for that exact marker, an empty simulated movement queue and matching internal
coordinates before completing the receipt. An acknowledgment or matching position
alone is insufficient. Missing proof expires to `outcome_unknown`; partial
command submission latches uncertainty and inhibits new ordinary operations.
Retries return the same journal receipt without repeating movement.

Home uses XYZ only, with no extrusion command. A jog leaves the absolute mode
and selected feedrate in effect; these are temporary setup changes, not saved
settings. The generated print file establishes its own initial modes. There is
no promise to restore a prior print state through these setup operations.

`printer.status.setup` reports the virtual position evidence, limits and
uncertainty. `physical_homing_qualified` is always false. OctoPrint's virtual G28
assigns coordinates; it does not model endstops, bed probing, collision avoidance
or actual homing motion. These controls must not be wired to hardware merely
because the virtual tests pass.

Validation includes an official MCP client driving real OctoPrint serial
simulation through home, forward/backward jogs, duplicate requests, invalid
requests, job start, pause and cancel. Separate cases cover a stale barrier,
undrained movement, missing position proof, timeout, partial queue submission,
heating, incompatible units, killed state and active jobs. Physical serial opens
are denied by the launcher and asserted zero in the integration test.

## Terminal ownership foundation

`printer_control.tty_ownership.open_assigned_tty` acquires a terminal descriptor
only after explicit assignment and busy-owner checks, then revalidates topology.
It requires a non-symlink character terminal, close-on-exec, nonblocking open,
an advisory lock and kernel `TIOCEXCL`. It closes on failed or interrupted
post-open validation. The eventual sender must use the owned descriptor without
opening the path a second time. Closing explicitly releases terminal exclusion
and the descriptor; repeated close cannot close a later reused descriptor.

This primitive is currently Linux-only. Actual software-terminal tests on Linux
aarch64 demonstrate that an ordinary competing open receives `EBUSY`, while the
other terminal remains usable. Tests also cover existing cooperating owners,
assignment rejection, topology races, symlinks and nonterminal paths. On macOS
15.2 a PTY accepted a competing open despite a successful `TIOCEXCL`; this
implementation therefore rejects macOS before opening any path. This observation
does not establish how the physical USB driver behaves.

Kernel exclusivity blocks subsequent ordinary opens, not descriptors that were
already open or privileged processes. See the [Linux terminal exclusivity
manual](https://man7.org/linux/man-pages/man2/TIOCEXCL.2const.html). It does not
replace OS device permissions, reliable busy-owner discovery, a controller lease
or physical identity checks. A race between opening and setting exclusion also
requires deployment-level isolation. Serial-device open itself can affect modem
signals; absence of application writes does not prove absence of a hardware
reset. Neither printer was used to qualify this primitive.

This is not a production sender factory: baud/modem-control configuration,
read/write integration, OS access isolation, disconnect reconciliation and
physical commissioning remain unfinished. No LLM tool exposes it yet. On macOS
the full host suite skips the six Linux PTY tests and separately verifies
rejection before open; run those six on Linux for ownership qualification.
