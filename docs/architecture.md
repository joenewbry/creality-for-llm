# Controller architecture and repository relationships

The host has one codebase. Its virtual printer tests and Model Context Protocol
interface run on the controller computer. Operating-system differences belong
in discovery, serial ownership/I/O and camera backends. They do not require a
Mac-specific or Linux-specific firmware image. Firmware selection depends on
the printer board/processor, screen and bootloader.

```mermaid
flowchart LR
    L[LLM or operator] --> T[Typed tools]
    T --> S[Controller service and state guards]
    S --> J[Durable journal and receipts]
    S --> W[One serial writer and receive path]
    W --> F[Marlin firmware candidate and touchscreen]
    C[Planned camera broker and monitor] -. fresh observations .-> S
    S -. capability gated camera requests .-> C
    U[Assigned Logitech webcam] -. separate USB camera connection .-> C
```

The host repository contains the tool service, journal, assignment gates,
simulators, native test harnesses, read-only Mac discovery, Linux descriptor I/O
and camera/mount design. The firmware fork contains changes to the pinned
Creality/Marlin controller source. A separately prepared focused Marlin fix is
intended for upstream review. No OctoPrint upstream submission is made because
its contribution rules exclude this generated adapter; installed OctoPrint is
used as a dependency with preserved attribution.

Linux/Python 3.11.13 passed the complete installed-package suite (876 tests,
zero skips) at the recorded snapshot. Those tests include native firmware
functions with hardware substitutes and software-terminal I/O. Mac has earlier
virtual/model demonstrations and tested read-only USB registry discovery. Mac
physical serial control remains disabled pending actual-driver qualification.
Neither operating system has a commissioned physical printer adapter here.
There are no separate published Mac and Linux forks.

The firmware candidate builds for an F401 configuration, but the development
unit's exact board, processor, screen and recovery route remain unconfirmed.
No candidate has been flashed. Camera capture, controls and visual monitoring
are planned interfaces; the CAD mount is a geometrically checked prototype.
