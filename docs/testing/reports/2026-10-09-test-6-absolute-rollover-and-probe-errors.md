# Absolute civil-hand rollover and saved-site probe errors (#6)

## Overview

A third re-run of the physical-device qualification for [#6: Qualify lifecycle, accuracy and battery behavior](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/6), fixing two harness defects that the third review round of [PR #123](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/123) found in the [window-end PID and saved-site report](2026-10-09-test-6-window-pid-and-site-validation.md):

1. **A failed saved-site probe was read as "no saved site".** `read_saved_site_zone_id` returned `None` both for a failed `run-as … cat` probe and for a successful read with nothing saved, so a transient or permission failure silently fell back to the device timezone. If the saved site's zone differed from the phone's, the harness would then probe the wrong civil midnight and could still pass. The read now returns `None` only when it succeeded and the store holds no loadable site — including the prefs file never having been written, recognised by the `No such file or directory` strerror token *naming the prefs path itself* — and otherwise raises `device_layer.ProbeError`. A failure about any other path is a probe error, and the phase fails loudly instead of falling back.
2. **The rollover check only verified relative movement.** `evaluate_rollover` subtracted the first sample's angle and compared the result against offsets that depend only on elapsed seconds, so a constant angular error (for example +30° on every sample) still passed; it never checked that the hand showed the resolved zone's civil time. It now compares each measured angle against the instant's **absolute** civil-hand angle in the resolved zone — the same mapping the renderer uses — with circular residuals against the shared 0.5° tolerance, and a constant-shift regression test.

Both fixes carry host regression tests. This is a new run with new measurements; the earlier reports keep their own numbers, corrected only editorially. App sources are unchanged, so the APK is identical to the previous runs'.

## Hardware & Environment Attribution

- **Target Platform**: Physical device running Android 16 (API 36), display `1080x2408`, locale `de-DE`.
- **Privacy Policy Compliance**: Hardware serial number, OEM name, marketing model, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy).
- **Saved observing site**: `Europe/Prague` (source `MANUAL`, latitude `50.08`, longitude `14.42`), read from `shared_prefs/observing_location.xml`. The device's own default timezone is also `Europe/Prague`; the run's evidence uses the saved site, as the raw output notes.
- **Clock-tick rate**: `getconf CLK_TCK` returns `100` on this device.
- **Test Build**: Local debug `app-debug.apk`, SHA-256 `3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The freshly built APK and the copy installed on the device (in `/data/app/…/base.apk`) re-hashed to the same value, so no reinstall was performed. App sources are unchanged from `021ee34a2f81255111cb36167dd598142ce16149` (`git diff 021ee34 -- app/` is empty).
- **Harness revision under test**: the `test/6-lifecycle-qualification` working tree carrying this report, on top of `a0f1dc380cc48bc1d50cf125ea544c42770305c6` — the `ProbeError`-raising `read_saved_site_zone_id` and `PREFS_FILE_ABSENT_MARKER` in `scripts/device_layer.py`, and the absolute `evaluate_rollover`/`expected_rollover_angles_deg` and the `ProbeError` catch in `phase_midnight_rollover` in `scripts/device_qualification.py`.

## Run

Command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 8192
```

Harness output (verbatim; the serial is replaced by the privacy policy's `<withheld>` marker), exit code 0:
```
=== Starting Device Checks on target: <withheld> ===
Run start timestamp: 10-09 17:38:51.000
Initial wallpaper PID: 4936

--- Phase 0: Environment Wake & Unlocking ---
Detected dial palette: Dark
Baseline hand angle t0: 84.578°

--- Phase 1: Screen-Off / Wake Navigation ---
Screen-off state observed; hand detected after wake at 84.654°.

--- Phase 2: Preview Navigation ---
Returned from preview; hand visible on home screen at 84.693°.

--- Phase 3: Surface Recreation ---
Surface recreated via 1080x2000; hand rendered at 84.725°

--- Phase 4: Process Recreation (kill -9 simulation) ---
PID transition: 4936 -> 6843

--- Phase 5: Virtual Time Travel (+30m, +12h) ---
+30m advance: 7.524° (expected: 7.500°, residual: +0.024°)
+12h advance: 180.403° (expected: 180.000°, residual: +0.403°)

--- Phase 6: Midnight Date Rollover ---
Midnight rollover in Europe/Prague (saved site): expected absolute angles [168.75, 179.958, 180.042, 191.25], measured [168.838, 180.203, 180.274, 191.409], residuals [0.088, 0.245, 0.232, 0.159]

--- Phase 7: Total PSS Growth ---
Total PSS: 39730 kB -> 42603 kB (growth +2873 kB; budget 8192 kB)

--- Restoring device state in finally block ---

--- Phase 8: Renderer Log Scan ---
Renderer log scan inconclusive: no matching warning records.

=======================================================
              PHYSICAL-DEVICE CHECK REPORT             
=======================================================
| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-09 | visible dial baseline | Dial rendered (Dark palette), hand at 84.578° |
| 2026-10-09 | screen-off / wake navigation | wallpaper reported hidden (mVisible=false); 0 CPU ticks over the measured 4.00s screen-off window at 100 ticks/s (<0.010s CPU, <0.25% of the window; budget 0.05s); hand detected after wake at 84.654° |
| 2026-10-09 | preview navigation | returned to home and detected the active wallpaper hand at 84.693°; preview-engine cleanup was not inspected |
| 2026-10-09 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 84.725° |
| 2026-10-09 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 84.759°; saved preference values were not inspected |
| 2026-10-09 | time travel (+30m, +12h) | +30m moved hand 7.524° (residual +0.024°); +12h moved 180.403° (residual +0.403°) |
| 2026-10-09 | midnight date rollover | zone Europe/Prague (saved site); samples 2026-06-20T21:15:00Z, 2026-06-20T21:59:50Z, 2026-06-20T22:00:10Z, 2026-06-20T22:45:00Z each matched the zone's absolute civil-hand angle (expected 168.750°, 179.958°, 180.042°, 191.250°; residuals +0.088°, +0.245°, +0.232°, +0.159°); the boundary is bracketed within 10s, not resolved |
| 2026-10-09 | total PSS sample | 39730 -> 42603 kB over 10s (growth +2873 kB; budget 8192 kB); not battery or CPU evidence |
| 2026-10-09 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |
=======================================================

Configured checks passed; renderer log scan was inconclusive.
```

The device's post-run state was re-read independently: `wm size` back to `Physical size: 1080x2408` with no override, night mode `yes` (unchanged), wakefulness `Dozing` and `Display State=OFF` (the run found the screen off), keyguard not showing, and the wallpaper process rebounded at PID `6843`.

## Saved-site probe errors

The zone the rollover phase probes must be the one the app's own `LocationStore.load()` renders, so a *failed* read of the saved-site preference must not be read as a successful read that found nothing. `read_saved_site_zone_id` now distinguishes the two:

- The read succeeded and the store holds no loadable site (including the prefs file never having been written) → `None`, and the phase falls back to `getprop persist.sys.timezone`, exactly as the app does.
- Any other failure — a non-debuggable package, a dead transport, a timeout, a marker-bearing error about some *other* path — raises `device_layer.ProbeError`. `phase_midnight_rollover` catches it, appends a failure naming the cause, and returns, so the phase fails loudly rather than sampling a zone the wallpaper may not be rendering. Later phases and the `finally` restore still run.

The prefs-file-absent case is recognised by the `No such file or directory` strerror token only when that stderr also names `LOCATION_PREFS_PATH`; the direction of the mistake is deliberate — a miss produces a loud `ProbeError`, never a silent fallback to the device timezone. On this device the record parses to `Europe/Prague` and the run's Phase 6 line confirms the same (`Europe/Prague (saved site)`); no probe error occurred, so the phase took its normal path.

Host regression tests cover the branch matrix: a missing prefs file (absent token naming the path) reads as `None`; a readable file whose record the store rejects reads as `None`; unrelated stderr, an absent token naming another path, `OSError`, and `subprocess.TimeoutExpired` each raise `ProbeError`; and a phase-level test pins that a `ProbeError` appends exactly one failure, appends no result row, and never calls the device-timezone fallback or broadcasts.

## Absolute civil-hand rollover

The four probe instants are built with `zoneinfo.ZoneInfo` from the zone the service renders civil time with, read from the app's own preference record over `run-as … cat` (validated field by field against `LocationStore`), falling back to `getprop persist.sys.timezone` only when a successful read found no saved record. For `Europe/Prague` (UTC+2 in summer) and the reference date 2026-06-20, the samples land at local 23:15:00, 23:59:50, 00:00:10 (next day), and 00:45:00, i.e. UTC instants `2026-06-20T21:15:00Z`, `2026-06-20T21:59:50Z`, `2026-06-20T22:00:10Z`, `2026-06-20T22:45:00Z`.

Each instant's **expected** angle is the absolute civil-hand angle the renderer derives for that local time — `(seconds_since_local_midnight / 240 + 180) mod 360`, clockwise from screen up (midnight 180°, noon 0°), mirroring `CivilDialConstants.kt`/`ClockState.kt` and pinned by `ClockStateTest.kt`. `evaluate_rollover` compares each measured angle against that expected angle as a circular residual, against the shared `ANGLE_TOLERANCE_DEG = 0.5°`:

| Instant (UTC) | Expected | Measured | Residual |
| --- | ---: | ---: | ---: |
| 2026-06-20T21:15:00Z | 168.750° | 168.838° | +0.088° |
| 2026-06-20T21:59:50Z | 179.958° | 180.203° | +0.245° |
| 2026-06-20T22:00:10Z | 180.042° | 180.274° | +0.232° |
| 2026-06-20T22:45:00Z | 191.250° | 191.409° | +0.159° |

All four residuals are within 0.5°, so the hand showed the resolved zone's civil time at every sample, not merely 90 minutes of movement. Because the expectation depends on the zone, a probe that resolved the wrong zone would land far outside the tolerance; and a constant angular error on every sample — which the earlier relative check accepted — now fails, which the host suite pins with a +30° constant-shift regression. The monotonicity guard is retained: an adjacent pair is ordered only across a gap wider than the probe jitter, and the two 10-second samples that bracket midnight sit 0.083° apart (below the tolerance), so they are checked as a bracket, not ordered against each other.

## Screen-off CPU observation

The screen was confirmed off (`read_screen_on` false, `mVisible=false`) before and after a measured screen-off window, and `time.monotonic()` bracketed the window. The wallpaper PID is read three times — before the sleep request, after the screen settled, and at the window's end — through `confirm_pid_unchanged`, which fails the phase whenever an end reading is unreadable or differs from the sampling PID; this run exited 0 with no PID failure row, so the closing tick reading is attributable to the process that opened the window. The combined `utime + stime` ticks from `/proc/<pid>/stat` moved by **0 ticks**: with `CLK_TCK = 100`, zero whole ticks bound the CPU time below one tick — under `0.010 s`, i.e. under `0.25%` of the 4.00-second window — against a `0.05 s` budget. This is a bounded window observation, not a duty cycle, frame-accounting, or battery measurement.

## Memory and renderer-log observations

Total PSS moved `39730 -> 42603 kB` (growth `+2873 kB`) over the 10-second sample, inside the agreed `8192 kB` budget; this is a memory-growth observation, not battery or CPU evidence. The renderer log scan was **inconclusive**: it found no matching warning or error records, which the harness records as inconclusive rather than as proof of a clean log.

## Limitations

- **The window-end PID check is a passed assertion, not a printed reading.** The harness does not emit the intermediate PID, so the record shows the check succeeded and the phase exited 0; it does not show the three raw values.
- **The civil-midnight boundary is bracketed, not resolved.** The two 10-second samples cannot be individually distinguished by a probe whose jitter is the same order as their 0.083° step; the run demonstrates that the hand matched the site's civil time 11° before and 11° after midnight, not the exact instant of the date change.
- **The rollover probes only the civil hand.** No astronomy or date-element probe exists in the harness, so `#6`'s "civil time **and astronomy** from the same instant" is only half evidenced; the astronomy half is tracked in [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125).
- **The absolute check shares the detector's orientation bias.** The expected angle assumes a bias-free detector, so a residual partly reflects the hand detector's orientation-dependent bias rather than the renderer alone. On this run the largest absolute residual was `+0.245°` against the `0.5°` tolerance — larger than the earlier relative-offset residuals, but still inside it, and the tolerance was not widened to accommodate it.
- **A saved zone the harness cannot build stops the rollover check.** The app's `ZoneId.of` accepts fixed-offset ids (`+02:00`), `Z`, and `UT` that Python's `ZoneInfo` cannot construct; a site saved with such an id makes the phase fail loudly instead of being verified. Extending the resolver to Java's offset grammar is not done here and is tracked in [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125).
- **The "absent prefs file" marker is Android- and locale-dependent.** It is anchored to the prefs path so an unrelated `ENOENT` cannot be read as absence, and a miss fails the phase loudly rather than passing, but the token is not locale-invariant.
- **The CPU row is a bounded window observation.** It bounds the wallpaper process's CPU time over one confirmed-off window on this device; it is not a duty-cycle, frame-accounting, or battery-drain measurement, and no producer-buffer trace was taken.
- **The renderer log scan is inconclusive.** No matching warning or error records were present, which the harness records as inconclusive rather than as proof of a clean log.
- **Single run.** These are one run's measurements on one device; they are not repeated or averaged.

Co-Authored-By: deepseek-v4-flash-vision-exp <noreply@deepseek.com>
