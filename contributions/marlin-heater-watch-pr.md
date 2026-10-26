### Description

When an armed `HeaterWatch` deadline wraps to exactly zero, `elapsed()` treats
it as disabled. For a 40-second watch, starting at unsigned millisecond time
`2^32 - 40000` computes `next_ms == 0`; the initial heating watch then never
expires unless another path restarts it. This concerns a deadline landing on
zero, not every timer rollover.

Reserve zero for intentional disarm by changing that one computed deadline to
1. The watch remains armed and expires one millisecond later. Ordinary deadlines,
rise checks and intentional off/near-target disarm retain their behavior.

Five native regression cases cover zero deadlines for 40- and 180-second watch
periods, ordinary expiry and temperature rise, nonzero rollover, and intentional
disarm. An optional `now` argument to `restart` defaults to `millis()` so existing
callers remain unchanged and tests can inject exact timestamps without a global
clock override. No configuration defaults change.

### Requirements

A configuration using `HeaterWatch` for a hotend, bed or chamber. There is no
board or display dependency. Existing firmware callers need no changes.
The shared template is covered with synthetic hotend/bed parameters; chamber
behavior is not independently exercised at runtime.

### Benefits

The zero sentinel cannot silently disable an otherwise armed initial-heating
watch. This fixes one timer edge case; it does not establish complete thermal
protection or qualify physical heaters.

### Configurations

Candidate `36406aafd8029b8375281d298ae983c373c399af` is one focused commit on
`bugfix-2.1.x` base `b84243d8f3337586ff0d9771a92a7828d5678731`.

Native validation: Linux aarch64, GCC 11.4.0, PlatformIO 6.1.18 and Unity 2.6.1,
`make unit-test-single-local UNIT_TEST_CONFIG=default` (`test/001-default.ini`).
A fresh checkout of this exact candidate passed all 168 cases, including all
five new heater-watch regressions, in 109.967 seconds. Configuration restoration
left the worktree clean.

Before/after regression evidence was also collected on predecessor
`2e93c507baa1acefac579597539261125fc20e3f`: removing only the zero guard yielded
166 passes and exactly two failures (the zero-deadline cases); restoring it
passed all 168. Only two upstream distribution-date headers differ between that
tested tree and this candidate.

Fresh cross-builds of this candidate passed with committed defaults for
`mega2560` (10.296 seconds) and upstream `buildroot/tests/DUE/config-01.ini` for
`DUE` (16.242 seconds, includes a heated chamber). Configuration defaults are
unchanged by the patch. No physical printer, full simulator or other native
test configuration has been tested. AI assistance was used for implementation
and validation.

### Related Issues

No equivalent zero-sentinel fix was identified in the targeted issue/PR searches.
Search coverage is not proof that no duplicate exists. Related but distinct:

- [PR 28695](https://github.com/MarlinFirmware/Marlin/pull/28695) aligns near-target
  watch and runaway thresholds; it is already in this base and is unchanged.
- [PR 21743](https://github.com/MarlinFirmware/Marlin/pull/21743) changes autotune
  watch behavior, not this deadline sentinel.
- [PR 1309](https://github.com/MarlinFirmware/Marlin/pull/1309) addresses an older
  arithmetic-width problem in thermal runaway timing.

This description is a local review draft; no PR has been submitted.
