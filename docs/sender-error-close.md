# Terminal sender state after competing errors

A failed transport write and a firmware error can reach the pinned sender from
separate threads. Its inherited error handler changes state to Error before
calling close. If another error arrives after closure, close returns early
because closure has already begun; the later Error state can remain in place.
The installed integration run reproduced this during a thermal-fault scenario.

The assigned sender now serializes explicit close and error handling under a
reentrant lock. One closing error owns the transition to Closed with error;
late handlers cannot overwrite it or repeat the error-close callback. A
non-closing diagnostic does not consume the later closing transition. The
inherited error/close implementation remains responsible for cleanup.

Deterministic tests execute the actual pinned error and close functions with
recording serial/callback dependencies. Late and competing errors fail against
the previous sender and pass after correction. They also cover a non-closing
error followed by a real error close. No serial port is opened by these tests.

This is sender lifecycle evidence, not a printer shutdown guarantee. The shared
connection's existing fault gate still blocks subsequent frames, and received
source-bound fault records remain separate from sender callbacks. Nothing here
adds a reset, retry, emergency command, reopen or automatic print recovery.
Physical motion and heater shutdown remain unqualified.
