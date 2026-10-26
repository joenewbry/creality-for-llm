# Development-printer camera interface

Status: interface design and CAD prototype. No live Logitech capture, image-based
fault detector or camera control has been commissioned. The Mac currently reports
only its built-in camera. Bench records identify a C270 elsewhere; confirmation
of the intended camera/connection is pending. Preserve an existing capture owner
and consume its snapshot interface where one already exists.

## Tools

| Tool | Contract | Status |
| --- | --- | --- |
| `camera.inspect` | Assigned camera identity/generation, active profile, resolution/crop, frame age, view calibration, supported controls and faults | Planned |
| `camera.snapshot` | A bounded image plus capture/receive timestamps, frame hash/ID, camera generation and printer/view association; explicitly reject a stale or unavailable frame | Planned |
| `camera.set_controls` | Capability-checked exposure, gain, white balance or auto/manual modes; reject unsupported controls, out-of-range values and unknown fields | Planned |
| `monitor.inspect` | Latest visual observations with confidence, source frame IDs, freshness, job association and what remains occluded/unknown | Planned |

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
