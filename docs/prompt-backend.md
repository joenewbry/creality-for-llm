# Journaled prompt transactions

`PromptBackend` adapts the prompt channel to `OperationService`. It supports
`prompt.inspect`, `prompt.bind` and the currently qualified generic user-wait
response shape: reason 2, one shown button, choice zero, exact prompt generation
and an asserted firmware wait flag. Other shapes are rejected before dispatch.
The adapter now supplies typed prompt operations and read tools to `ToolRouter`;
see `tools.md`. No physical transport factory or autonomous model integration is
provided. The job and prompt backends remain separate.

The service validates and durably claims the request before the adapter writes.
The original request deadline is passed through the backend dispatch contract to
the channel. Duplicate service requests return their existing journal result.
Restart recovery keeps unresolved commands outcome unknown rather than replaying.
All channel access by the enclosing transport must respect the backend guard;
its pump must return available bytes promptly, not wait for completion.

The backend context timestamp observes the host channel. It does not refresh
firmware telemetry: mutations separately require a fresh firmware snapshot.
Inspect and bind complete from matching protocol receipts. A response additionally
requires an inactive prompt and `W0`, the explicit firmware user-wait flag. An
accepted response lacking that evidence is outcome unknown. The recorded scope
is `firmware_prompt_protocol`, with `physical_recovery_qualified: false`.
A cleared firmware flag does not prove motion completion or successful printing.

Transport identity, exclusive device ownership and the physical action profile
must be qualified by the enclosing transport adapter before this can attach to
hardware. USER_CONTINUE can occur in filament/thermal workflows as well as M0;
its reason alone does not authorize a physical continuation. The current tests
use a generated generic wait in the actual native firmware handler, never a
physical UART. Heating, extrusion and screen-wide recovery remain unqualified.
The existing service owner lease is not a substitute for exclusive serial access.

Validation: 269 software tests pass, including a live native subprocess whose
writer asserts the journal already contains the running claim before any bytes
are sent. Tests exercise complete wait evidence, acceptance without resolution,
partial writes, deadlines, duplicate requests, restart recovery and invalid
choices/arguments. The firmware F401 build succeeds. The existing OctoPrint
virtual service test still executes all 80 moves exactly once through pause/resume
following the backend dispatch-signature change. Evidence is listed in
`docs/progress.md`. Neither physical printer was accessed or flashed.

Stage 20 status adds `invalidation_reason`, `blocking_event` (including sequence,
connection generation, observation time and parsed diagnostic), and
`automatic_fault_recovery_supported: false`. A recognized firmware fault or
sensor-suspect report inhibits the prompt channel. An unresolved journal
operation returns outcome_unknown with the actual invalidation reason rather
than a misleading prior acceptance reason. See prompt-channel.md for tested
ordering, fragmentation and physical limits. The latest suite passes 319 tests;
`evidence/tests-stage20.xml` contains the result.

## Prompt guidance and physical intervention

Status includes `prompt_guidance`: category, current prompt ID when known,
allowed responses, next action, and `requires_physical_action`. A null physical
requirement means unknown, not false. Only a fresh, bound, ready, one-button
generic wait offers choice zero. Stale snapshots and uncertain channels offer
no responses. This is structured guidance; the protocol does not transmit the
original screen message, so `firmware_message_available` is false.

The firmware can advertise Creality runout as a generic USER_CONTINUE prompt.
Its correlated rejection code 09 means physical action is required. The host
retains this requirement for that session/prompt identity, returns no allowed
responses, and refuses new attempts without writing. Repeating the original
request returns its existing failed receipt. A successful subsequent inspection
does not erase the requirement. The runout sensor stays enabled and firmware
keeps its user wait blocked in the native test.

A rejected receipt is diagnostic evidence, not an accepted actionable snapshot.
The host may report the retained intervention requirement while
`prompt_snapshot` is absent and `snapshot_fresh` is false. The initial profile
has no tool for declaring material reloaded or clearing this requirement; those
physical checks and a qualified recovery procedure remain separate work.
