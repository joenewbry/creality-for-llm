# Owned firmware and settings report

Opt in with `attach_configuration_report()` after attaching the completion
barrier and before the sender starts. The journaled tool
`printer.configuration.inspect` takes the existing operation context and only
`arguments: {job_id}`. It exposes no command, device path or settings-write input.
The sender must be idle, the prompt session bound and no job previously started
in this backend. Active/paused/finished-job queries are explicitly unsupported.

The operation commits its intent before queueing exactly:

1. M400.
2. A unique begin marker through M118.
3. M115.
4. M503.
5. A unique end marker through M118.

The existing shared writer observes every actual complete frame, including
numbering/checksums. The assigned source recorder must receive both exact markers
in order and the report between them, under the same operation/job/hash,
host/session/generation/capture and deadline. Intervening or replayed writes,
partial frames, missing/repeated markers, capture gaps or changed identity leave
the operation unknown. Neither a G-code ok nor unbracketed earlier settings can
complete it. Required replay rows for the maintained profile include units,
steps, maximum feed/acceleration and advanced motion settings. A different or
incomplete report fails rather than receiving default values.

Capture is bounded to 256 interior source lines and 16 KiB. Both markers retain
the original one-second source age, including after persistence. This imposes a
real response-size/latency limit; slow hardware remains unqualified and is not
rescued by extending the clock. The controller loop collects pending results
without another model poll. A later operation.get returns the durable result.
After a complete interval is verified, its writer trace is retired so ordinary
pre-job traffic can proceed. Restart or lost workflow state never replays a
claimed interval. Other command operations cannot overlap collection.

## What the result identifies

The result retains actual firmware declaration/capability lines, reported replay
settings, both marker receipts, exact write hashes, source event range and original
end arrival. The transcript digest covers exact interior source bytes, including
line endings. A separate canonical digest covers ordered reported settings and
firmware declaration. Unsolicited temperature/ack lines remain in the transcript
but are excluded from the reported-settings digest.

This is a reported-settings identifier, not complete configuration identity.
In the pinned maintained source, MarlinSettings::report prints current RAM
settings and calls individual report methods. It does not identify the installed
binary or all runtime state. For example, stepper and maximum inactivity timers
are maintained separately in the G-code subsystem. Board/bootloader, physical
geometry, coordinate interpretation, feature coverage and motor/sensor/part
continuity still need their actual qualification/producer. Firmware text is a
declaration, not an attestation or binary readback. No digest may be substituted
for those facts in a physical checkpoint.

The result always reports configuration_identity_complete=false,
firmware_binary_identity_verified=false, physical_readiness_qualified=false and
resume_authorized=false. It is an input for the unfinished physical provider,
not a startup admission or action grant. Collecting it before the first job does
not establish that settings remain unchanged during a later pause. Post-drain
configuration collection/coverage and physical provider integration remain open.

Tests exercise actual journal/writer/capture/channel/backend/controller loop and
an official Model Context Protocol client, with synthetic firmware/settings
replies. Plain and numbered frames, missing/stale/replayed reports, partial or
extra writes, changed job, slow persistence, delayed model collection, immutable
intent and schema rejection are covered. The existing native/sender regressions
remain separate coverage. This is not execution of a complete physical M503 or
a new installed-package/full-suite qualification. Neither printer is opened.
The original printer stays protected; Logitech work remains deferred.
