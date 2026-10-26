# Firmware prompt identity checkpoint

October 25, 2026. The experimental token protocol is now integrated into the
selected development firmware build; see `prompt-wire.md`. It is unflashed,
physically unqualified, and not exposed by a host `prompt.respond` tool yet.

## Implemented behavior

Firmware owns each prompt generation. A replacement increments it; closing or
successfully answering invalidates it. Responses must match the bound 128-bit
session and generation, wait for the complete button list, and select an
available choice. Counter exhaustion rejects rather than wrapping. The host
must generate a fresh random session for every connection and boot. Binding any
session preserves generations and exhaustion. This lets local wait cleanup
recognize the same prompt across a rebind and prevents a rebind from clearing
an exhausted counter. Exhaustion requires a firmware restart before new prompts
can accept remote responses.

`host_token_response` validates in the main loop and preserves the sensor check:
a remote Continue cannot disable latched filament runout. Creality also labels
its nozzle-parked runout wait USER_CONTINUE; that response now refuses while the
runout latch is set. Its return value
means handler acceptance, not completed physical recovery.

The emergency parser puts legacy M876 replies in a one-entry mailbox rather
than running screen/planner recovery from the receive context. The main idle
loop drains it, including during supported blocking waits. Replies carry the
generation observed on receipt; replacement prompts make queued replies stale.
An occupied mailbox rejects another entry without overwriting the first.
Binding a token session disables this legacy response path.

The generation shared with the receive context is volatile; mailbox publication
and consumption use firmware critical sections. Native harnesses stub those
sections and do not prove interrupt concurrency or hardware timing. Build
evidence is for the selected F401 configuration only.

The common user-wait function captures the prompt generation and ends that
prompt after timeout or local dismissal. A replacement prompt survives cleanup;
a session rebind of the same prompt does not. The selected RTS screen's direct
stop and continue paths now invalidate the prompt immediately. Filament-load
and confirmation loops close their prompts on exit, including the RTS abort
path. These are prompt lifecycle changes, not a new screen recovery protocol.

## Remaining integration

- The bounded experimental command grammar/codec is now in `prompt-wire.md`.
  The main-loop dispatcher and acceptance/rejection receipts are implemented;
  fresh-session reconnect negotiation and physical-resolution events remain.
- Route all serial entry points through token enforcement. The alternate
  experimental runtime now gates M108; alternate non-emergency configurations
  are excluded by build checks and remain outside this implementation.
- Complete the wait/fault inventory, including other screen configurations and
  fault shutdown paths. The selected RTS paths and common M0/M1 wait cleanup are
  covered below; this does not qualify every Creality alert or physical recovery.
- Keep stop access independent of negotiation. Preserve sensor/thermal faults
  and physical-intervention requirements. Never advertise generic dismissal.
- Test malformed/truncated frames, queue saturation, screen/serial races,
  resets and disconnects through host tools, then qualify hardware.

## Evidence and limits

Native tests compile actual `host_actions.cpp`, headers and emergency parser
with physical dependencies stubbed. Cases cover missing/wrong sessions,
replacement, duplicate response, incomplete prompt assembly, invalid choice,
explicit close, local response, rebind, runout refusal, unsupported reason,
reboot, deferred handling, stale queued replies and bound-session legacy
rejection. Boundary tests cover generation and button-count exhaustion.

The next checkpoint additionally compiles the extracted actual common wait
function and the RTS ResumePrintKey switch case with stubbed clock, idle,
screen, sensor, planner and heater dependencies. Tests cover timeout, manual
dismissal, already-answered waits, replacement and session rebind; screen
continue/runout actions with asserted and cleared sensor pins; and remote
USER_CONTINUE refusal with a latched runout. The screen cases assert that these
tested paths do not queue commands or request heating. They do not exercise
the full touchscreen firmware, physical sensor timing or a real print.

Current evidence: `evidence/tests-stage7.xml`, `evidence/prompt-lifecycle-f401.log`
and `evidence/prompt-lifecycle-build.json`. Prior identity-only evidence is
retained in `evidence/tests-stage6.xml` and `evidence/prompt-identity-build.json`.
No physical printer was accessed or
flashed. Source/installed-version mismatch and recovery qualification remain open.
