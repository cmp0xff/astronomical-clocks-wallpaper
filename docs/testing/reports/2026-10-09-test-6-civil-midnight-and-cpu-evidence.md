# Site-aware civil-midnight rollover and bounded screen-off CPU evidence (#6)

## Overview

A re-run of the physical-device qualification for [#6: Qualify lifecycle, accuracy and battery behavior](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/6), addressing two evidence gaps that review of [PR #123](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/123) found in the [final qualification report](2026-10-09-test-6-final-qualification.md):

1. **Civil-midnight rollover.** That report's two instants crossed **UTC** midnight while the service renders each instant through the saved site's zone, so no civil date changed. This run derives the four probe instants from the device's saved site and brackets that site's own civil midnight.
2. **Screen-off CPU.** That report's `20 CPU ticks` was a raw count with no measured interval and no clock-tick conversion. This run samples strictly inside a confirmed-off window, re-checks the screen across it and the process at the sleep transition, converts ticks by the device clock-tick rate, and bounds the CPU time against a budget.

This is a new run with new measurements; the earlier report keeps its own numbers, corrected only editorially.

## Hardware & Environment Attribution

- **Target Platform**: Physical device running Android 16 (API 36), display 1080x2408 (480 dpi), locale `de-DE`.
- **Privacy Policy Compliance**: Hardware serial number, OEM name, marketing model, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy).
- **Saved observing site**: `Europe/Prague` (source `MANUAL`, latitude `50.08`, longitude `14.42`), read from `shared_prefs/observing_location.xml`. The device's own default timezone is also `Europe/Prague`; the run's evidence uses the saved site, as the raw output notes.
- **Clock-tick rate**: `getconf CLK_TCK` returns `100` on this device.
- **Test Build**: Local debug `app-debug.apk`, SHA-256 `3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`; the freshly built APK and the APK pulled back from the device re-hashed to the same value, so no reinstall was performed.
- **Harness revision under test**: the `test/6-lifecycle-qualification` working tree carrying this report — the site-aware four-instant `phase_midnight_rollover` and the bounded `phase_screen_off_wake` in `scripts/device_qualification.py`, with `parse_saved_site_zone`, `read_saved_site_zone_id`, `read_device_timezone`, and `read_process_cpu_clock_ticks` in `scripts/device_layer.py`. App sources are unchanged from `021ee34a2f81255111cb36167dd598142ce16149` (`git diff 021ee34 -- app/` is empty).

## Run

Command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 8192
```

Harness output (verbatim), exit code 0:
```
=== Starting Device Checks on target: <withheld> ===
Run start timestamp: 10-09 16:39:54.000
Initial wallpaper PID: 21667

--- Phase 0: Environment Wake & Unlocking ---
Detected dial palette: Dark
Baseline hand angle t0: 69.836°

--- Phase 1: Screen-Off / Wake Navigation ---
Screen-off state observed; hand detected after wake at 69.884°.

--- Phase 2: Preview Navigation ---
Returned from preview; hand visible on home screen at 69.940°.

--- Phase 3: Surface Recreation ---
Surface recreated via 1080x2000; hand rendered at 69.982°

--- Phase 4: Process Recreation (kill -9 simulation) ---
PID transition: 21667 -> 27576

--- Phase 5: Virtual Time Travel (+30m, +12h) ---
+30m advance: 7.536° (expected: 7.500°, residual: +0.036°)
+12h advance: 180.433° (expected: 180.000°, residual: +0.433°)

--- Phase 6: Midnight Date Rollover ---
Midnight rollover in Europe/Prague (saved site): offsets [0.0, 11.365, 11.435, 22.571], residuals [0.0, 0.157, 0.144, 0.071]

--- Phase 7: Total PSS Growth ---
Total PSS: 40495 kB -> 46095 kB (growth +5600 kB; budget 8192 kB)

--- Restoring device state in finally block ---

--- Phase 8: Renderer Log Scan ---
Renderer log scan inconclusive: no matching warning records.

=======================================================
              PHYSICAL-DEVICE CHECK REPORT             
=======================================================
| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-09 | visible dial baseline | Dial rendered (Dark palette), hand at 69.836° |
| 2026-10-09 | screen-off / wake navigation | wallpaper reported hidden (mVisible=false); 0 CPU ticks over the measured 4.01s screen-off window at 100 ticks/s (<0.010s CPU, <0.25% of the window; budget 0.05s); hand detected after wake at 69.884° |
| 2026-10-09 | preview navigation | returned to home and detected the active wallpaper hand at 69.940°; preview-engine cleanup was not inspected |
| 2026-10-09 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 69.982° |
| 2026-10-09 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 70.019°; saved preference values were not inspected |
| 2026-10-09 | time travel (+30m, +12h) | +30m moved hand 7.536° (residual +0.036°); +12h moved 180.433° (residual +0.433°) |
| 2026-10-09 | midnight date rollover | zone Europe/Prague (saved site); samples 2026-06-20T21:15:00Z, 2026-06-20T21:59:50Z, 2026-06-20T22:00:10Z, 2026-06-20T22:45:00Z advanced the hand 22.571° across civil midnight (residuals +0.000°, +0.157°, +0.144°, +0.071°); the boundary is bracketed within 10s, not resolved |
| 2026-10-09 | total PSS sample | 40495 -> 46095 kB over 10s (growth +5600 kB; budget 8192 kB); not battery or CPU evidence |
| 2026-10-09 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |
=======================================================

Configured checks passed; renderer log scan was inconclusive.
```

The device's post-run state was re-read independently: `wm size` back to `Physical size: 1080x2408` with no override, night mode `yes` (unchanged), wakefulness `Dozing` (the run found the screen off), keyguard dismissed, and the wallpaper process rebound at PID `27576`.

## Zone source and probe method

The four probe instants are built with `zoneinfo.ZoneInfo` from the zone the service renders civil time with. That zone is read directly from the app's own preference record: `run-as io.github.godaniya.astronomicalclockswallpaper.debug cat shared_prefs/observing_location.xml`, taking the `location` string, JSON-parsing it, and reading its `zoneId`. If that read fails, the harness falls back to `getprop persist.sys.timezone`; if both fail, the phase fails loudly rather than passing without verifying anything. The record on this device is:

```xml
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="location">{&quot;version&quot;:1,&quot;latitude&quot;:50.08,&quot;longitude&quot;:14.42,&quot;source&quot;:&quot;MANUAL&quot;,&quot;zoneId&quot;:&quot;Europe\/Prague&quot;}</string>
</map>
```

For `Europe/Prague` (UTC+2 in summer) and the reference date 2026-06-20, the probe yields the local wall-clock samples 23:15:00, 23:59:50, 00:00:10 (next day), and 00:45:00, i.e. the UTC instants `2026-06-20T21:15:00Z`, `2026-06-20T21:59:50Z`, `2026-06-20T22:00:10Z`, `2026-06-20T22:45:00Z`. The two middle samples straddle the site's civil midnight by 10 seconds each side; the two 45-minute shoulders give the hand a displacement the probe can actually resolve.

Each sample's expected clockwise hand offset from the first is its local-time delta × 15°/h: 0.000°, 11.208°, 11.292°, and 22.500°. Measured offsets were 0.000°, 11.365°, 11.435°, and 22.571°, leaving residuals +0.000°, +0.157°, +0.144°, and +0.071° — all inside the 0.5° tolerance. The two middle samples sit only 0.083° apart, below the probe's ~0.15° jitter, so they are asserted against the ~11° offsets they occupy, not ordered against each other; the two 11°-plus shoulders are what reject a frozen hand.

## Screen-off CPU observation

The screen was confirmed off (`read_screen_on` false, `mVisible=false`) before and after a measured 4.01-second window; `time.monotonic()` bracketed the window, the wallpaper PID was re-read after confirming off to reject a rebound process, and the combined utime+stime ticks from `/proc/<pid>/stat` moved by **0 ticks**. This recorded harness did not re-read the PID at the window's end, so it does not establish that the process was unchanged across the sample window itself; that gap was fixed and the harness re-run in [2026-10-09-test-6-window-pid-and-site-validation.md](2026-10-09-test-6-window-pid-and-site-validation.md). With `CLK_TCK = 100`, zero whole ticks bound the CPU time below one tick — under `0.010s`, i.e. under `0.25%` of the 4.01-second window — against a `0.05s` budget. This is a bounded window observation, not a duty cycle or a battery measurement.

## Limitations

- **The civil-midnight boundary is bracketed, not resolved.** The two 10-second samples cannot be individually distinguished by a probe whose jitter is the same order as their 0.083° step; the run demonstrates that the hand advances smoothly across the site's midnight from 11° before to 11° after, not the exact instant of the date change.
- **The CPU row is a bounded window observation.** It bounds the wallpaper process's CPU time over one confirmed-off window on this device; it is not a duty-cycle, frame-accounting, or battery-drain measurement, and no producer-buffer trace was taken.
- **The renderer log scan is inconclusive.** No matching warning or error records were present, which the harness records as inconclusive rather than as proof of a clean log.
- **Single run.** These are one run's measurements on one device; they are not repeated or averaged.
- Only the multi-phase qualification harness was re-run; `device_smoke.py` was not re-run in this pass.
