# Cold job evidence and pre-open readiness

`ColdAssignedEvidence` implements the assigned backend's preflight, start,
checkpoint and transition callbacks using owned source reports and explicit trusted
physical inputs. `ColdStartupReadiness` validates trusted observations before
`OperationService.from_backend_factory` can construct a backend. Neither component
opens a device, supplies missing facts, creates a hardware qualification or exposes
an LLM approval tool. No development-printer qualification or live physical producer
is installed; physical commissioning remains unqualified.

## Qualified cold scope

A `ColdMachineQualification` is immutable and hash addressed. It requires the unit's
board, processor, bootloader, screen, installed firmware hash, full qualified
configuration and coverage references, assignment/protected-device exclusion,
qualified host driver and recovery references. It binds an immutable `ColdJobPolicy`,
position comparison tolerance and maximum powered pause interval. References must
come from checked artifacts and qualification work; filling the schema or matching
a hash does not establish their truth.

The initial provider supports absolute millimeter XYZ, zero home/workspace offsets,
no leveling, tool zero, zero targets/fan, ordinary feed/flow overrides and explicit
cold command bounds. E is preserved as a controller coordinate and receives no
extrusion commands. Physical position and motor observations cover XYZ; this is not
an extrusion/printing qualification. It requires V2 state with M84/M85 inactivity
intervals zero. It does not send commands to disable those timers. Enabling a
nonzero timeout or using another pause policy requires a separately implemented
and qualified policy; matching M503 rows cannot fill that gap.

Fresh source reads retain their original request, session, sequence, capture and
arrival time. Changing calculated arrival age does not change source identity;
age limits are still checked by each actual read. The pre-job M115/M503 baseline
comes from a completed journaled owned inspection, with matching printer/job,
host session, capture, firmware session and qualified reported-settings digest.
It remains historical. Full configuration and installed artifact verification
must also come from the current trusted physical producer. There is no conversion
from an incomplete report digest into a full configuration hash.

## Trusted physical producer contract

Operator-side code constructs the qualification and supplies `observe(request)`.
A physical request contains the complete job identity, qualification digest,
operation/phase, fresh request nonce, request time and canonical source-context
digest. Its source dictionaries are copies; modifying them changes no capture.
The digest binds that context, not a claim of a new binary readback.

The producer must return a `ColdPhysicalObservation` for that exact request,
using current unit-specific evidence. It supplies independently verified XYZ
position and motor-power facts, actual cold temperatures/targets, sensor health,
clearance, part continuity, fault freedom, and installed artifact/configuration
verification. Transition phases additionally need independent confirmation that
the identified job actually started, resumed, completed or cancelled. Sender
state, a reference ID, model output or a reused checkbox is insufficient.

Every current observation is at most one second old, has a positive expiry no
more than one second after observation, and must follow its request. Producers
must preserve actual observation times and relevant continuity intervals; they
cannot refresh a stale measurement by setting the time when returning a receipt.
The nonce prohibits rebinding an old observation to another call. Unknown facts
must be false or unavailable, not defaulted to true. Static qualification records
remain distinct from current physical observations. Trusted code must validate
the referenced qualification and measurement sources before asserting facts;
these adapters do not authenticate arbitrary external JSON or sign attestations.

Only trusted startup code installs this producer. No tool submits these objects,
changes the qualification, waives a sensor fault, selects another printer or opens
a port. Tests use explicitly synthetic qualifications and physical observations.
The actual device's unresolved hardware/sensor facts cannot be replaced with them.

## Admission and checkpoint flow

1. Attach owned capture, automatic cold inspection, the compound completion barrier
   and the owned pre-job configuration collector. Construct the backend with this
   provider, then bind it once before starting. Complete a real pre-job settings
   inspection after the qualified connection opens.
2. Stage/start combine fresh controller inspection, the historical settings baseline,
   qualified profile and current physical observations. Accepted physical claims are
   durably recorded. The optional `before_effect` hook binds the committed parent
   operation, rechecks original source and physical expiry after persistence, and
   consumes the admission immediately before file selection or print start.
3. Pause/resume require their own actual M400/marker/settings interval and linked
   state inspection. The provider compares controller coordinates with the sender's
   pause position within the qualified precision, checks transmitted modes, and
   requires independently matching physical position and motor/part continuity.
   Resume must cover the original pause interval and cannot extend its expiry.
4. Start/resume confirmations retain admission as historical context: a moving
   printer's new position must not be compared with its pre-motion position.
   Fresh physical transition evidence is still required. Completion/cancellation
   additionally require the exact current drain marker; source and physical expiry
   are rechecked after the external callback and journal work. Cancellation can
   be observed even if the job file has changed; completion cannot ignore its hash.

The provider never falls back to the virtual peer or the legacy checkpoint
callback. Its accepted observations are durable evidence inputs, not restart or
replay authority. The assigned backend continues to report physical control
unqualified until the entire deployment is commissioned. The callback currently
runs synchronously: expiry checks reject late results, but do not preempt a blocked
producer. Bounded acquisition or a guarded asynchronous physical producer remains
required before qualifying live responsiveness.

## Pre-open checker and remaining work

`ColdStartupReadiness` binds a fresh observation to the exact `StartupState`, unit
qualification, request nonce and time. It checks actual unit identity, development
assignment, protected-device exclusion, matching driver/recovery references,
qualification facts, cold temperatures/targets and clear workspace. Prior history
requires explicit current reconciliation. Expired, partial, wrong-unit or wrong-state
inputs refuse before backend construction. This checker does not implement a Mac
serial driver or obtain any of these facts by opening a printer early.

No default physical factory/launcher, qualified machine profile or actual measurement
producer is enabled. Production Mac transport/startup, sensor diagnosis, exact unit
identity/bootloader/recovery, development commissioning and the separate post-repair
printing qualification remain open. The protected printer remains permanently
excluded, and all Logitech work remains deferred.
