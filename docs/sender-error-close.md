# Terminal sender state after competing errors

A failed transport write and a firmware error can reach the pinned sender from
separate threads. Its inherited error handler changes state to Error before
calling close. If another error arrives after closure, close returns early
because closure has already begun; the later Error state can remain in place.
The installed integration run reproduced this during a thermal-fault scenario.

The assigned sender now uses a short error-entry latch. One closing error owns
retirement, inherited close and notification. Duplicate handlers return while
close is in progress. The latch is released before inherited code runs, avoiding
cross-thread lock cycles. A non-closing diagnostic does not consume the later
closing transition.

The error path retires the owned connection and deliberately does not invoke
inherited automatic emergency/temperature writes, resets or retries. Configured
sender emergency behavior does not authorize writes through an uncertain
connection. Explicit qualified stop remains a separate operation. Closing the
connection does not prove that printer motion or heating stopped.

Deterministic tests execute the assigned error handler and the actual inherited
close function with recording serial/callback dependencies. Late and competing
errors fail against the previous sender and pass after correction. A lock probe
requires a duplicate error to return while close is blocked; no new lock remains
held across inherited shutdown. Tests also cover a non-closing error followed
by a closing error. No serial port is opened by these tests.

The first full installed run exposed the competing-error closure failure. An
initial long-held-lock candidate then timed out in the Linux fault scenario;
that run is retained and is not a qualification. The final implementation uses
the short latch and passes a fresh 1,175-test installed Linux qualification with
zero skips. [Full record](../validation/linux-package-stage137.json).

Already received source-bound fault records remain separate from sender
callbacks. This adds no automatic print recovery or physical shutdown claim.
Physical motion and heater shutdown remain unqualified.
