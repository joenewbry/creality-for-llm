# Post-drain controller inspection

Opt in with `attach_completion_barrier(checkpoint_inspection=True)` before sender
start, after owned source capture is attached. The default state-only mode
requires the external evidence provider method
`checkpoint_from_controller(identity, report)`. Default behavior
and the existing provider interface remain available when this mode is off.

Pause and resume each acquire their own post-drain report. The assigned sender
queues the fixed M400/unique M118 marker pair. Only after its exact source marker
is received and fresh can the controller journal/claim a child `prompt.inspect`
with the parent deadline. The barrier durably links that child and its predicted
wire request before permitting one exact prompt-lane inspection frame. It checks
the actual complete write, adjacent write sequence and original reply arrival.
A generic extra read, different request, duplicate, bind/respond, partial frame
or intervening command cannot qualify the tail. Supported prompt operations
can still run before draining begins; a pending earlier receipt is awaited
before claiming the inspection child, without extending marker age. A new barrier cannot abandon an
unfinished inspection. No arbitrary G-code interface is added.

The child receipt must complete and its coherent state line must correlate to
the same session/request/capture/generation. The state must arrive after the
marker and inspection write, with zero reported planner blocks. Both marker
and report keep their original one-second age limits and parent deadline.
A report that follows its prompt receipt may wait within that deadline; missing
or stale input never becomes completion. Source and operation authority are
rechecked after durable reads/updates and the external provider callback.

The provider receives a copy of the actual report. It still must independently
qualify board/firmware/configuration, coordinate interpretation, position, motor
hold, sensor health and part continuity. Native commanded coordinates may require
conversion into the qualified checkpoint frame. Homed/trusted/enabled flags and
reported temperatures are not independent physical proofs. Neither the report
nor a successful drain marker grants physical readiness or resume authority.
The existing immutable checkpoint, file/cursor/mode/configuration comparison and
physical proof requirements remain enforced.

Resume uses a new marker and inspection after the pause, rather than reusing
its saved report. Only the two verified numbered barrier frames may account
for transport numbering changes; the prompt-lane inspect is unnumbered and
cannot excuse another line-number increment. File cursor and all other
checkpoint fields remain strict. The original pause interval must still be
valid; this mode does not implement restart or power-loss recovery.

The existing controller loop advances these pending operations without another
model call, retaining terminal outcomes for later collection. It uses the same
backend guard and trusted external provider callback behavior as other admission.
External callbacks must be bounded; arbitrary external code is not preempted.
On a late return the original source/deadline checks refuse further action.
Runtime timing still requires qualification. Restart never replays a claimed
child or incomplete barrier; existing journal recovery requires reconciliation.

Tests use the actual journal, shared writer, recorder, channel, state reader,
backend and controller loop with synthetic packets and explicitly synthetic
external physical proofs. They cover separate pause/resume reports, numbered
barrier frames, delayed model collection, stale/missing state, interference,
duplicate/partial queries, provider refusal/delay, report-copy isolation,
durable intent, lost workflow state and unexplained line-number changes.
They do not establish a physical checkpoint or qualify an actual printer.

Full hardware/configuration/continuity evidence, development commissioning and
nonprinting demonstration remain unfinished. Mainboard photos are deferred until
needed for firmware commissioning; the historical negative nozzle reading is
unresolved. Neither printer is opened by this change. The original printer stays
protected. Logitech work remains deferred. Printing is a separate post-repair
qualification.

## Settings alongside the controller report

Opt in with `checkpoint_inspection=True, checkpoint_configuration=True` when
attaching the barrier. This requires the external provider method
`checkpoint_from_sources(identity, controller_report, configuration_report)`.
The adapter does not fall back to the legacy callback for this mode.

Each pause/resume parent journals one fixed five-frame group: M400, unique begin
marker, M115, M503 and unique end marker. After that report interval completes,
the parent journals its one correlated prompt-lane state inspection. The exact
five commands and one adjacent inspect are the only permitted writes. Both
marker receipts and the later state report retain their original source time,
one-second age bounds, identity/capture/session and parent deadline. The native
state must follow the configuration end marker and inspect write. Collection
uses the existing bounded report parser and durable assigned source, not caller
hashes or a cached pre-job report.

Pause stores the reported-settings digest with its qualified checkpoint. Resume
acquires another report and rejects a changed firmware declaration, capability
or reported replay setting before calling the physical provider or releasing
motion. This remains a conservative reported-change check, not complete printer
configuration identity. Matching reports do not establish binary identity,
runtime timeout state or physical continuity; the provider still supplies those
facts. Both source dictionaries are copied before the external callback and
source authority is checked again afterward. The staged file is checked before
a checkpoint can be returned.

Numbered transports may advance only by these five exact verified frames;
the prompt inspection is unnumbered. File cursor, modes, configuration identity,
physical proofs and pause interval remain strict. Unexplained numbering changes,
missing/interleaved/replayed input or a slow provider keep resume paused and
require reconciliation. Two-cycle and official tool-client tests exercise the
same existing job.pause/job.resume interface with synthetic physical proofs.
This mode adds no raw-command tool and does not enable the standalone pre-job
configuration tool during an active or paused job.

A new resume operation acquires the fresh admission evidence while the sender
stays paused. Re-reading the completed pause receipt does not refresh it. Submit
one resume request and poll its returned operation ID; do not toggle pause or
replay commands to make the old evidence appear current.
