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

## Capture-owner HTTP adapter

`CaptureOwnerHTTP` in `printer_control.camera_http` is a concrete client backend for an existing acquisition owner; it does not open a webcam or start/restart a service. Configure the origin and a pinned numeric destination address outside the LLM tool arguments. HTTPS validates the origin hostname and certificate using the standard trust store (a private CA can be supplied through `SSL_CERT_FILE`). Unencrypted HTTP requires explicit opt-in and a loopback destination, for an already secured local bridge. No DNS, environment proxies, redirects, arbitrary paths, embedded URL credentials or tool-selected endpoints are used. Optional authorization headers stay in operator configuration and are excluded from error results.

Every exchange has a maximum five-second default timeout (configurable up to ten seconds), capped by its operation deadline. An absolute timer interrupts a drip-fed socket response. Responses are identity-encoded JSON bounded to 110,000 bytes; duplicate keys, nonfinite values and unsupported protocol versions are rejected. The broker still enforces frame association/freshness and post-write readback. Inspection has its own bounded timeout; the broker rechecks the mutation deadline before dispatch. These are transport/software bounds, not a real-time firmware stop guarantee.

The owner must implement this narrow interface under its existing access gate:

| Method/path | Required envelope | Meaning |
| --- | --- | --- |
| GET `/llm-camera/v1/state` | `{protocol_version: 1, state: {...}}` | Exact broker state fields: camera/printer/owner identities, reconnect generation, state version, numeric control ranges and current readback values |
| GET `/llm-camera/v1/snapshot` | `{protocol_version: 1, frame: {...}}` | Exact broker frame fields, substituting `data_base64` for raw `data`; capture/receive times and actual job/view association must come from the owner |
| POST `/llm-camera/v1/controls` | Request: camera ID, generation, expected state version, deadline and controls. Response: `{protocol_version: 1, accepted: true}` | Owner validates context/deadline and makes one bounded attempt. Response acknowledges dispatch only; later state readback determines completion |

The owner must assign identities from verified configuration, change generation after reconnect/restart and advance state version whenever controls, profile, mount/view or association change. It must not infer job association merely from a printer index. Frame timestamp semantics and clock alignment need qualification. A byte signature is not image decoding or optical verification. Use this adapter only behind `CameraBroker`, which supplies durable mutation intent and uncertainty handling.

The inspected existing Print room endpoint `/api/cameras/{printer_id}/snapshot.jpg` supplies JPEG bytes and a frame timestamp but not this identity/generation/job envelope. It is deliberately incompatible with the strict client; the legacy-refusal test confirms that. Its source/service/configuration has not been changed or deployed by this work. An owner-side metadata upgrade and live qualification remain necessary before connecting the actual Logitech. No bridge to the active printer or existing camera is activated.

An accepted frame now records the capture-owner state version. An external version change invalidates the cached frame even if the USB generation is unchanged. This prevents fresh-looking observations from surviving a changed exposure/profile/view.

[HTTP/broker validation](../validation/camera-owner-http-stage116.json) includes real local HTTP and TLS exchanges. These tests do not establish physical camera acquisition, optical quality or host-driver isolation.

## Owner-side API implementation

`CaptureOwner` in `printer_control.camera_owner` supplies the in-process owner state and atomic publication/control fences. `CaptureOwnerAPI` in `printer_control.camera_owner_api` implements the three versioned ASGI routes expected by the HTTP client. Neither creates a listener, opens a device, starts capture, or restarts an existing service. The inspected Print room checkout has unrelated edits and is untouched. These modules are an integration candidate, not an activated Print room upgrade.

The operator supplies a verified camera/printer/owner assignment, output profile, mount/view identity and a bounded acquisition pipeline. Construction creates a fresh process generation. An owner inherited across a fork refuses calls before acquiring its inherited lock or invoking driver callbacks; create the owner within the single qualified capture process. This process check is not an operating-system camera-device lease. Call `begin_capture()` for each acquisition restart/reconnect; it changes generation and invalidates frames. Before capturing a frame, obtain `inspect()` context. Publish the resulting bytes with that generation/version, actual capture/receive timestamps and dimensions. Publication rejects old contexts, unqualified/malformed time/geometry envelopes, oversize bytes and out-of-order frames. Job/view/profile changes go through the trusted in-process `configure_view` method, which advances version and clears prior evidence; no HTTP tool changes this assignment or calibration.

A frame is served only while at most five seconds old. Metadata defaults to an unassociated job until the owner establishes the exact job ID. Do not infer camera identity from a printer index, reuse an old process generation, or relabel a legacy receipt timestamp as measured capture time. Signature/dimension envelopes remain weaker than decoded image or optical qualification. The current legacy `frame`/`at` pair is not sufficient proof to activate this path.

Read-only integrations advertise no image controls. Numeric controls require both actual readback and a bounded write callback. Owner operations refresh readback and invalidate frame evidence on external changes. An atomic context fence advances state version before the one write attempt, so an old HTTP request cannot dispatch twice. Completion checks actual requested-value readback and deadline; exceptions or missing readback latch uncertainty. The durable broker remains responsible for cross-process request receipts, no blind replay and reconciliation requirements. Owner-side latches have no HTTP clear/override endpoint.

Embed the ASGI adapter behind the existing owner's authorization/access gate, with a mandatory synchronous authorization callback that defaults to denying access. It is called before reading a request body or owner metadata. A trusted outer middleware can set a scope-state authorization decision; an HTTP header by itself must not manufacture that decision. Existing non-camera paths delegate to the supplied application. Configure server request/concurrency limits as part of commissioning; this module does not alter networking, TLS termination or credentials.

Control JSON bodies are capped at 8 KiB and have an absolute two-second receive budget. Owner methods run in worker threads, preserving the application's event loop. Native read/write callbacks must still implement their own hardware I/O bounds; Python worker cancellation cannot retract an uncertain camera effect. The software test blocks a control callback and confirms that an unrelated application route still runs.

The complete tested path is broker → pinned HTTP client → ASGI routes → owner core → fixture frame/readback callbacks. It passes actual local HTTP and authorization tests; it does not establish live acquisition, correct driver ownership, timestamps, focus, printer visibility or physical clearance. An owner-side module now exists, while deployment and native callback qualification remain pending. [Qualification record](../validation/camera-owner-api-stage117.json).
