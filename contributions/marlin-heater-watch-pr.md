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

Candidate `c8139a8c96a83271bad5fe541ea15b68143fa2a0` is one focused commit on
`bugfix-2.1.x` base `cce3c1218cba190168f432d5ffbedfb43d95515b`.

Native validation: Linux aarch64, GCC 11.4.0, PlatformIO 6.1.18 and Unity 2.6.1,
`make unit-test-single-local UNIT_TEST_CONFIG=default` (`test/001-default.ini`).
A fresh checkout of this exact candidate passed all 168 cases, including all
five new heater-watch regressions, in 104.702 seconds. Configuration restoration
left the worktree clean.

Before/after regression evidence was also collected on predecessor
`2e93c507baa1acefac579597539261125fc20e3f`: removing only the zero guard yielded
166 passes and exactly two failures (the zero-deadline cases); restoring it
passed all 168. The current candidate reuses the same two-file fix/test diff, rebased onto the October 26 working branch; current-revision native and cross-build results are reported here.

Fresh cross-builds of this candidate passed with committed defaults for
`mega2560` (7.135 seconds) and upstream `buildroot/tests/DUE/config-01.ini` for
`DUE` (18.392 seconds, includes a heated chamber). Configuration defaults are
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

Configurations are unchanged. Reproduce the AVR build with `pio run -e mega2560`; for Due, copy the committed `buildroot/tests/DUE/config-01.ini` to `Marlin/config.ini`, then run `pio run -e DUE` and restore configs. No custom printer configuration or binary is required for this fix.

Submitted as [Marlin PR 28753](https://github.com/MarlinFirmware/Marlin/pull/28753) on October 26, 2026. Open for upstream review; not merged. The two smaller historical patches are retained as provenance; the combined patch pins this submitted candidate.
