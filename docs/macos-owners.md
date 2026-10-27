# Mac serial owner metadata

`MacOSOwnerInspector` supplies read-only owner inspection for a future Mac
transport. It never opens a printer node, changes settings, resets USB, or sends
commands. The physical Mac transport remains disabled.

The earlier operator diagnostic compared process IDs. Two descriptors in the
same process therefore looked like one owner. The reusable inspector preserves
each `(pid, fd)` pair, including both serial aliases. `is_busy(device)` rejects
pre-existing owners through `guarded_open`'s callback contract;
`check_owned(device, fd, lease)` requires exactly the expected descriptor, checks
the live controller lease before and after inspection, compares descriptor/device
identity, and rejects changed alias nodes. A missing descriptor is not success.
Assignment/topology validation remains the enclosing transport's responsibility.

The system `lsof` runs without a shell or serial opener, with numeric fields and
both exact alias paths. Warnings, inconsistent exit status, incomplete records,
duplicate records, missing visibility, excessive output, timeout and process
inheritance all refuse ownership authority. Both output streams are bounded;
failed children are killed and reaped. Raw process/path inventories stay private.

## Evidence and limits

The tests use mocked device metadata and a private ordinary file. The native
Mac check opens two descriptors to that file, observes both, closes one and then
both, and verifies each change through actual system `lsof`. This proves the
metadata path for that fixture, not coverage of inaccessible privileged handles
or actual serial-driver behavior. All 43 owner-component cases pass on the Mac. The focused installed-package
checks pass 113 cases with 22 Linux-only skips on Mac and 134 with one Mac-only
skip on Linux, with installed module bytes matched to the export. The 1,175-case
full-suite qualification remains the earlier snapshot; it was not rerun for this
unwired component. See the [qualification record](../validation/macos-owners-stage138.json).

`python scripts/qualify_macos_pty.py` creates its own software terminal; it accepts
no physical serial path. On macOS 15.2 / Darwin 24.2.0, user 501, `TIOCEXCL`
returned success but a second non-root process could still open that PTY. A
cooperating `flock` did reject its second lock attempt. This is a **PTY result**,
not a claim that Mac USB serial exclusion fails. It must not be used to authorize
physical access or justify removing the platform guard.

Apple publishes separate [PTY](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/tty_dev.c)
and [serial-client](https://github.com/apple-oss-distributions/IOSerialFamily/blob/main/IOSerialFamily.kmodproj/IOSerialBSDClient.cpp)
open paths. The latter contains its own exclusive-open checks. Those sources
explain why testing one path cannot qualify another; they do not identify the
installed printer driver or prove current physical isolation.

An owner snapshot is not atomic with a later open or write. It cannot displace a
pre-existing connection, prevent a new connection, establish physical readiness,
or confirm stopped motion/heaters. The bounded metadata query can take up to its configured timeout (at most two
seconds); it is not a replacement for the fast per-frame authority fence.
A production Mac transport still needs
qualified kernel/driver exclusion, device isolation, current USB assignment and
integration under the same journal lease. No such launcher is enabled here.
