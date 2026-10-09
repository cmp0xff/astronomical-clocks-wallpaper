# Lifecycle, accuracy, and battery qualification (#6)

## Overview

Physical-device qualification run for [#6: Qualify lifecycle, accuracy and battery behavior](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/6) (Milestone **v0.3 — Personal APK**). It demonstrates the subset of #6's acceptance list that the harness actually measures; the criteria it does not evidence are carried by [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125) and are not v0.3 blockers.

This final qualification synthesizes and builds upon the merged device and lifecycle work addressing aspects of #6:
- Initial live-wallpaper feasibility and engine transitions (issue #2, PR #103)
- Debug virtual clock and device smoke test (PR #85)
- Shared device harness layer with teardown restoration (PR #110)
- Screen-off visibility, wake recovery, and PSS sampling (PR #111)

This run records three observations:
1. **Screen-off CPU inactivity**: Combined user and system CPU ticks sampled from `/proc/<pid>/stat` while the display was sleeping and the engine was hidden (`mVisible=false`) recorded **20 CPU ticks**, a raw observation whose confirmed-off interval and clock-tick conversion were never captured, so it carries no CPU rate (see the correction below).
2. **Midnight date rollover**: Two instants straddling `2026-06-20T23:59:50Z` to `2026-06-21T00:00:10Z` advanced the hand 0.079° against 0.083° expected (residual −0.005°). Those instants cross **UTC** midnight, not the saved site's civil midnight, and the general 0.5° tolerance cannot reject a frozen hand on a 0.083° step, so this did not establish civil-date rollover (see the correction below).
3. **Full multi-phase lifecycle qualification**: Executing all nine phases (Phase 0–8) of `scripts/device_qualification.py` with clean exit code 0.

### Corrected after review (2026-10-09)

Review of [PR #123](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/123) found that this report's original summary overstated two of its rows. Every raw output line, number, PID, PSS figure, date, and the APK SHA-256 above and below are unchanged; only the interpretation is corrected here:

- **The screen-off CPU row is a raw observation, not a rate.** The 20-tick sample began before the sleep request; no confirmed-off interval and no clock-tick rate (`getconf CLK_TCK`) were recorded, and the count was never converted to CPU seconds. It therefore establishes no CPU percentage and no "dormancy". A bounded re-run supplies that evidence in [2026-10-09-test-6-civil-midnight-and-cpu-evidence.md](2026-10-09-test-6-civil-midnight-and-cpu-evidence.md).
- **The midnight row did not establish civil rollover.** Its two instants cross UTC midnight; with the saved `Europe/Prague` site they land at 02:00 local, where no civil date changes, and the general 0.5° tolerance accepts a frozen hand for the 0.083° step. The site-aware four-instant re-run in the same new report supersedes it.
- **The "zero warnings/errors" claim is withdrawn.** This run's own log scan was inconclusive (no matching records), as the raw output below shows; that is recorded as a limitation, not as a passing criterion.
- **The acceptance matrix now follows issue #6's own criteria.** The previous matrix graded proxy categories (frame rate, memory, themes) that are not #6's acceptance list; section 3 is rebuilt from #6's eleven criteria, marking each as demonstrated or pending, and the rows the harness does not evidence are deferred to [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125).

## Hardware & Environment Attribution

- **Target Platform**: Physical device running Android 16 (API 36), display 1080x2408 (480 dpi).
- **Locale & Timezone**: Locale `de-DE`, default timezone `Europe/Prague`.
- **Privacy Policy Compliance**: Hardware serial number, OEM name, marketing model, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy).
- **Test Build**: Local debug `app-debug.apk` built from clean source tree (APK SHA-256 `3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`), verified via `scripts/verify-apk.sh` and signed with Android Debug certificate.
- **Active Settings**: Dark appearance palette (`DialStyle.RIM` `#1C2C39`), default Prague manual location (`50.08, 14.42`), standard Orloj dial layers (Zodiac, Sun, Moon active).

---

## 1. Automated Smoke Verification (`device_smoke.py`)

Run command:
```sh
python3 scripts/device_smoke.py
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-09 | virtual time travel (+30m) | Hand advanced 7.542° against 7.500° expected, residual +0.042°; broadcast confirmed by the service log |
| 2026-10-09 | surface recreation | effective 1080x2408, `wm size 1080x2000` (active as requested) then `wm size reset` (verified); hand drawn afterwards |
| 2026-10-09 | renderer log | Inconclusive: no matching warning records; rendering was not verified |

Result: Smoke test passed with clean exit code 0.

---

## 2. Multi-Phase Device Qualification Verification (`device_qualification.py`)

Run command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 8192
```

Harness execution output:
```
=== Starting Device Checks on target: <withheld> ===
Run start timestamp: 10-09 15:44:45.000
Initial wallpaper PID: 16386

--- Phase 0: Environment Wake & Unlocking ---
Detected dial palette: Dark
Baseline hand angle t0: 56.020°

--- Phase 1: Screen-Off / Wake Navigation ---
Screen-off state observed; hand detected after wake at 56.099°.
WARNING: wallpaper process consumed 20 CPU ticks while screen off.

--- Phase 2: Preview Navigation ---
Returned from preview; hand visible on home screen at 56.145°.

--- Phase 3: Surface Recreation ---
Surface recreated via 1080x2000; hand rendered at 56.152°

--- Phase 4: Process Recreation (kill -9 simulation) ---
PID transition: 16386 -> 17477

--- Phase 5: Virtual Time Travel (+30m, +12h) ---
+30m advance: 7.593° (expected: 7.500°, residual: +0.093°)
+12h advance: 180.496° (expected: 180.000°, residual: +0.496°)

--- Phase 6: Midnight Date Rollover ---
Midnight rollover advance: 0.079° (expected: 0.083°, residual: -0.005°)

--- Phase 7: Total PSS Growth ---
Total PSS: 30138 kB -> 32953 kB (growth +2815 kB; budget 8192 kB)

--- Restoring device state in finally block ---

--- Phase 8: Renderer Log Scan ---
Renderer log scan inconclusive: no matching warning records.
```

### Measured Qualification Results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-09 | visible dial baseline | Dial rendered (Dark palette), hand at 56.020° |
| 2026-10-09 | screen-off / wake navigation | device reported screen off after the 4s sleep interval; wallpaper reported hidden (mVisible=false); 20 CPU ticks while asleep (see editorial correction); hand detected after wake at 56.099° |
| 2026-10-09 | preview navigation | returned to home and detected the active wallpaper hand at 56.145°; preview-engine cleanup was not inspected |
| 2026-10-09 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 56.152° |
| 2026-10-09 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 56.190°; saved preference values were not inspected |
| 2026-10-09 | time travel (+30m, +12h) | +30m moved hand 7.593° (residual +0.093°); +12h moved 180.496° (residual +0.496°) |
| 2026-10-09 | midnight date rollover | 20s midnight step (2026-06-20T23:59:50Z -> 2026-06-21T00:00:10Z) moved hand 0.079° (residual -0.005°); crossed UTC midnight, so no civil rollover is established (see editorial correction) |
| 2026-10-09 | total PSS sample | 30138 -> 32953 kB over 10s (growth +2815 kB; budget 8192 kB); not battery or CPU evidence |
| 2026-10-09 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |

Result: `Configured checks passed; renderer log scan was inconclusive.` Clean exit code 0.

---

## 3. Acceptance matrix for Issue #6

The rows below follow issue #6's own acceptance criteria, in its order. A row is **Demonstrated** only where the numbered criterion is met by recorded physical-device evidence; rows the harness does not exercise are **Pending** and are carried by [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125) rather than claimed here.

| # | Criterion (abridged from #6) | Evidence | Status |
|---|---|---|:---:|
| 1 | Zero rendering while hidden and while the screen is off | `mVisible=false` from `dumpsys activity service`, plus the bounded screen-off CPU window (0 ticks over a measured 4.01 s at `CLK_TCK=100`, <0.010 s against a 0.05 s budget) in [2026-10-09-test-6-civil-midnight-and-cpu-evidence.md](2026-10-09-test-6-civil-midnight-and-cpu-evidence.md) and the window-identity re-run. A bounded window observation, not a producer trace. | **Demonstrated (bounded)** |
| 2 | Time, astronomy, location, settings restored after waking, lock/unlock, surface recreation, process recreation, **reboot** | Wake recovery, surface recreation, and `SIGKILL` rebind are recorded here; a **reboot** was never run. | **Partly — reboot pending** |
| 3 | System-time changes and date rollover update civil time **and astronomy** from the same instant | The site-aware rollover sweep establishes the civil hand; no astronomy or date-element probe exists anywhere in the harness. | **Partly — astronomy pending** |
| 4 | Site DST transition updates its civil clock and events without changing coordinates or zone identity | No phase crosses a DST boundary. | **Pending** |
| 5 | Phone-timezone change after saving a site leaves the site unchanged; before selection the civil clock follows the phone zone and site-dependent astronomy stays hidden | Host-tested only. | **Pending** |
| 6 | Changing the observing site updates civil clock and astronomy, including remote coordinates | Not exercised on device. | **Pending** |
| 7 | Preview and active instances coexist and clean up callbacks/resources without leaks, duplicate loops, or crashes | Preview navigation returned to home; cleanup was explicitly "not inspected". | **Pending** |
| 8 | Render-failure handling: a `drawDial` throw is logged, not rethrown to the main `Handler`, via a narrowly-typed catch (detekt `TooGenericExceptionCaught`) | Not exercised; the log scan finds no matching records. | **Pending** |
| 9 | Astronomy reference checks for representative locations, including hemisphere and polar cases | Not re-run on the current build. | **Pending** |
| 10 | Battery/CPU measurement with recorded firmware, revision, brightness, duration, baseline, screen-on/off conditions, and an agreed budget | Only a bounded screen-off CPU window and a PSS delta exist; the comparison protocol is undefined and unrun. | **Pending** |
| 11 | Reproducible device test matrix with pass/fail evidence and unresolved defects | This matrix and the re-run reports it links. The rows above stay pending. | **Partly published** |

No row is claimed as more than its evidence supports. The two overstatements this report originally carried — a rate inferred from a raw tick count, and civil rollover inferred from a UTC-crossing step — are corrected above and superseded by the re-runs.

## Conclusion

This report records the harness-covered subset of **Issue #6** with physical-hardware evidence, and the re-runs supersede its two weakest rows. The criteria the harness does not evidence — reboot restoration, same-instant astronomy, DST, phone-zone/no-site behavior, site changes, preview/active cleanup, render-failure handling, representative/polar astronomy, and the full battery comparison protocol — are **not** claimed as satisfied; they are carried by [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125) and no longer block Milestone v0.3. Independently, the renderer log scan was inconclusive in this run (no matching records), so it establishes no clean-log claim.
