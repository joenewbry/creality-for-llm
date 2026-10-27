# Admission after journal commitment

A durable request claim must precede device dispatch. Committing that claim can
outlive a deadline or the external evidence used during validation. Validation
before the transaction therefore cannot authorize dispatch afterward by itself.

The controller now checks ownership, deadline, current connection, state version
and observation age after commitment, repeats backend admission, and checks the
context again before invoking the dispatch or workflow callback. Backend
validation may run more than once: it must not write to the machine or consume
a one-shot dispatch permission. Backend write-time device/evidence interlocks
remain necessary; this is not proof of physical readiness.

If the refreshed admission rejects the request, its immutable terminal receipt
says `dispatched: false`. Unavailable admission yields a failed receipt with the
same boundary. No dispatch callback ran for this request; this does not establish
that other buffered work or heaters stopped. Duplicate requests return the
same receipt and do not revalidate or retry.

Ownership loss is different. A former owner does not dispatch or write a terminal
receipt. The committed running intent remains for the successor's existing
restart reconciliation. A crash at that boundary remains conservative uncertainty.

Fourteen regressions reproduce failures in the prior service. They cover commit
latency, changed connection/state, stale/future observations, readiness withdrawal,
evidence-source failure, changed staged bytes, slow second validation and lease
loss. Three cases exercise the actual assigned-job adapter with a recording
sender; the tests do not connect to hardware. The exact aggregate passes 1,175 installed Linux tests with zero skips; see
[the qualification record](../validation/linux-package-stage137.json).

This does not implement the physical evidence provider. Current source-bound
serial reports can describe temperatures, controller-reported position and
connection provenance. They do not prove homing, motor holding, planner drain,
sensor validity or that a part remains undisturbed. Physical start/resume still
requires those independently qualified facts and the existing hardware gates.
