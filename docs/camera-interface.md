# Development-printer camera interface

Status: tested software broker and typed tools, plus a CAD prototype. No live Logitech capture, image-based
fault detector or camera control has been commissioned. The Mac currently reports
only its built-in camera. Bench records identify a C270 elsewhere; confirmation
of the intended camera/connection is pending. Preserve an existing capture owner
and consume its snapshot interface where one already exists.

## Tools

| Tool | Contract | Status |
| --- | --- | --- |
| `camera.inspect` | Assigned camera identity/generation, supported numeric controls and latest accepted frame metadata; live profile/fault reporting requires a backend | Implemented against an injected capture-owner adapter; live backend pending |
| `camera.snapshot` | A bounded image plus capture/receive timestamps, frame hash/ID, camera generation and printer/view association; explicitly reject a stale or unavailable frame | Implemented against an injected capture-owner adapter; live backend pending |
| `camera.set_controls` | Capability-checked numeric exposure/gain/white-balance values; reject unsupported controls and out-of-range values. Boolean/enumerated modes require a future extension | Implemented against an injected capture-owner adapter; live backend pending |
| `monitor.inspect` | Latest visual observations with confidence, source frame IDs, freshness, job association and what remains occluded/unknown | Frame freshness/provenance implemented; visual inference pending |

Mutation envelopes use request ID, assigned camera ID, connection generation,
expected state version and deadline. Duplicate requests return the existing
receipt. Completion requires control readback; a successful API write alone does
not establish exposure or focus quality. A stream restart changes its generation
and invalidates pending frame/setting assumptions. Controls must respect an
existing camera owner rather than opening a second competing capture process.

The C270 is fixed-focus and manually aimed. Do not advertise motorized pan/tilt,
optical zoom or software autofocus. Exposure/white-balance/gain availability must
be queried on the actual camera and host backend; Mac AVFoundation and Linux
V4L2 do not promise identical control surfaces. Manual mount changes invalidate
the recorded view calibration. Audio is unnecessary for the initial monitor.

## Frame evidence

Each frame carries `camera_id`, `connection_generation`, `frame_id`, `sha256`,
`captured_at`, `received_at`, `width`, `height`, `crop`, `view_calibration_id`,
`mount_revision`, `assigned_printer_id` and associated `job_id` when established.
A view calibration includes actual corner-marker images at qualified bed
positions, focus/lighting checks, the supported height range and occlusions.
A familiar-looking printer is not sufficient camera-to-printer identity proof.

Keep reported facts separate from inferences: a frame timestamp and image hash
are facts; “possible spaghetti” is a model observation with uncertainty. A stale,
black or disconnected feed means monitoring unavailable. It does not prove the
printer stopped, its heaters are off or the job completed.

## Policy boundary

Initial monitoring reports observations. A future configured intervention policy
may request the existing typed `job.pause` operation after fresh corroborating
machine state; it must retain normal identity, deadline and pause-completion
checks. Camera output cannot dismiss thermal faults, home into a part, bypass a
physical-action requirement or silently resume a job. Firmware retains motion
and heater protection when the camera, host or LLM is unavailable.

Camera acquisition is separate from printer USB ownership. Adding the webcam does
not create another serial writer. The protected printer and any existing camera
service remain outside development access. Live images stay in owned protected
storage and are excluded from public source/PR evidence unless separately
sanitized for the requested delivery.

[Mount prototype and conditional visibility/clearance model](../hardware/camera-mount/README.md).
Physical camera/clip/desk fit, printer clearance, framing, blur and lighting remain
unverified until the actual installation check is completed.

## Broker implementation and adapter requirements

`printer_control.camera.CameraBroker` owns a separately bound journal and process lease; `CameraTools(broker, printer_router)` composes its tools with the normal printer router for JSON or MCP. Construction does not open a serial port or camera, and there is no tool to select a URL/device path. Inject the already assigned acquisition owner's adapter. This is software integration, not a live Logitech backend.

The adapter must implement `inspect()`, `snapshot(deadline=...)` and `set_controls(values, deadline=...)`. Inspect reports the exact camera/printer/capture-owner identities, a reconnect generation, monotonically changing state version, supported numeric control ranges and actual readback values. Snapshot returns an encoded PNG/JPEG bounded to 64 KiB and 4096 pixels per axis, capture/receive times, frame identity, mount/view identifiers and printer/job association. The broker hashes and returns bytes as base64; it checks signatures but does not decode images or validate optical quality. A production backend must perform bounded acquisition/encoding and truthful clock/profile readback. Python cannot cancel a blocked native camera driver; enforce deadlines inside that adapter.

A snapshot requires camera/generation/state/deadline, maximum age (at most 30 seconds) and exact job association (or null). The broker rechecks context after acquisition, rejects stale/future/mismatched frames and clears the cached observation on acquisition failure or control changes. `monitor.inspect` reports fresh provenance (at most five seconds), empty visual observations and explicit unknown printer health/motion/heater state. No detector or automatic pause policy is installed.

Controls require the same context plus request ID and a nonempty range-checked map. Intent/dispatch are persisted before one write attempt. Completion requires new-version readback of every requested value before deadline. Exceptions, generation change or missing readback produce durable `outcome_unknown`; duplicates return the original receipt. `camera.operation_get` retrieves that receipt. New control mutations are blocked while any uncertain receipt needs reconciliation, including after process restart; automatic reconciliation is not implemented. The camera journal cannot be reassigned or recover an existing nonempty printer journal. These controls never issue printer commands.

Run the focused software verification with the pinned MCP environment:

```sh
python -m pytest -q tests/test_camera.py tests/test_tools.py tests/test_service.py tests/test_export_review.py
```

Tests include a real MCP SDK client/server over memory streams, one-write duplicate/concurrent requests, ambiguous readback and restart, competing owners, wrong printer/camera/job, generation changes, expired frames and malformed/oversized images. Frames and control devices are fixtures. Actual capture, host-driver isolation, focus, mount fit/clearance/framing and physical operation remain unqualified.
