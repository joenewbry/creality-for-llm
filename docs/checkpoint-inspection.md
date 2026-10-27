# Post-drain controller inspection

Opt in with `attach_completion_barrier(checkpoint_inspection=True)` before sender
start, after owned source capture is attached. The external evidence provider
must implement `checkpoint_from_controller(identity, report)`. Default behavior
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
