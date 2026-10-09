# Window-end process identity and saved-site validation (#6)

## Overview

A second re-run of the physical-device qualification for [#6: Qualify lifecycle, accuracy and battery behavior](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/6), fixing two harness defects that the second review round of [PR #123](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/123) found in the [site-aware rollover and bounded-CPU report](2026-10-09-test-6-civil-midnight-and-cpu-evidence.md):

1. **Saved-site validation.** `parse_saved_site_zone` accepted any JSON object with a non-empty `zoneId`, but the app's `LocationStore.load()` rejects a record whose `version` is not the integral `1`, whose `latitude`/`longitude` are not in-range JSON numbers, or whose `source` is not `CURRENT_COARSE`/`MANUAL`. An invalid record would therefore have made the harness probe a civil midnight the wallpaper never renders. The parser now mirrors `LocationStore`/`ObservingLocation` field by field and returns no zone for a rejected record, so the phase falls back to the device timezone exactly as the app does. Review then showed a second defect in the first fix: reading "Python cannot build this zone id" as "no saved site" was unsafe, because the app's `ZoneId.of` accepts fixed offsets (`+02:00`), `Z`, and `UT` that Python's `ZoneInfo` rejects, and the picker can store such an id. A stored zone the harness cannot build now fails the phase loudly instead of being silently replaced by the device zone, which could have passed on the wrong civil midnight.
2. **Window-end process identity.** `phase_screen_off_wake` read the wallpaper PID before the sleep and again after the screen settled, but never after the closing `ticks_end` read, so a rebound inside the ~4-second window could attribute the closing ticks to a different process. The phase now re-reads the PID at the window's end and fails when it differs or is unreadable.

Both fixes carry host regression tests. This is a new run with new measurements; the earlier reports keep their own numbers, corrected only editorially. App sources are unchanged, so the APK is identical to the previous runs'.

## Editorial note (2026-10-09, third review round)

This report's measurements are unchanged, but one of its interpretive claims is superseded. The rollover check recorded here compared every sample against the first, so the "residuals" below are *relative offsets*, and a constant angular error on all four samples would also have passed. The harness now compares each measured angle with the instant's **absolute civil-hand angle** in the resolved zone, with a constant-shift regression test, and re-runs the phase; that evidence is in [`2026-10-09-test-6-absolute-rollover-and-probe-errors.md`](2026-10-09-test-6-absolute-rollover-and-probe-errors.md). In the same round, `read_saved_site_zone_id` gained the distinction noted but not implemented here: a *failed* saved-site probe is now raised (`ProbeError`) and fails the phase, rather than being read as "no saved site" and silently replaced by the device timezone.

## Hardware & Environment Attribution

- **Target Platform**: Physical device running Android 16 (API 36), display `1080x2408`, locale `de-DE`.
- **Privacy Policy Compliance**: Hardware serial number, OEM name, marketing model, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy).
- **Saved observing site**: `Europe/Prague` (source `MANUAL`, latitude `50.08`, longitude `14.42`), read from `shared_prefs/observing_location.xml`. The device's own default timezone is also `Europe/Prague`; the run's evidence uses the saved site, as the raw output notes.
- **Clock-tick rate**: `getconf CLK_TCK` returns `100` on this device.
- **Test Build**: Local debug `app-debug.apk`, SHA-256 `3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The freshly built APK, the APK pulled back from the device, and the installed copy re-hashed to the same value, so no reinstall was performed. App sources are unchanged from `021ee34a2f81255111cb36167dd598142ce16149` (`git diff 021ee34 -- app/` is empty).
- **Harness revision under test**: the `test/6-lifecycle-qualification` working tree carrying this report, on top of `9a45a2ae22f7e252b41d9c6c0819d6fa6d5b9057` — the validated `parse_saved_site_zone`/`saved_record_zone_id` in `scripts/device_layer.py`, and `confirm_pid_unchanged` on both the sleep transition and the sample window in `scripts/device_qualification.py`.

## Run

Command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 8192
```

Harness output (verbatim; the serial is replaced by the privacy policy's `<withheld>` marker), exit code 0:
```
=== Starting Device Checks on target: <withheld> ===
Run start timestamp: 10-09 17:12:39.000
Initial wallpaper PID: 29645

--- Phase 0: Environment Wake & Unlocking ---
Detected dial palette: Dark
Baseline hand angle t0: 78.024°

--- Phase 1: Screen-Off / Wake Navigation ---
Screen-off state observed; hand detected after wake at 78.084°.

--- Phase 2: Preview Navigation ---
Returned from preview; hand visible on home screen at 78.129°.

--- Phase 3: Surface Recreation ---
Surface recreated via 1080x2000; hand rendered at 78.159°

--- Phase 4: Process Recreation (kill -9 simulation) ---
PID transition: 29645 -> 32125

--- Phase 5: Virtual Time Travel (+30m, +12h) ---
+30m advance: 7.573° (expected: 7.500°, residual: +0.073°)
+12h advance: 180.432° (expected: 180.000°, residual: +0.432°)

--- Phase 6: Midnight Date Rollover ---
Midnight rollover in Europe/Prague (saved site): offsets [0.0, 11.365, 11.435, 22.571], residuals [0.0, 0.157, 0.144, 0.071]

--- Phase 7: Total PSS Growth ---
Total PSS: 30418 kB -> 33066 kB (growth +2648 kB; budget 8192 kB)

--- Restoring device state in finally block ---

--- Phase 8: Renderer Log Scan ---
Renderer log scan inconclusive: no matching warning records.

=======================================================
              PHYSICAL-DEVICE CHECK REPORT             
=======================================================
| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-09 | visible dial baseline | Dial rendered (Dark palette), hand at 78.024° |
| 2026-10-09 | screen-off / wake navigation | wallpaper reported hidden (mVisible=false); 0 CPU ticks over the measured 4.01s screen-off window at 100 ticks/s (<0.010s CPU, <0.25% of the window; budget 0.05s); hand detected after wake at 78.084° |
| 2026-10-09 | preview navigation | returned to home and detected the active wallpaper hand at 78.129°; preview-engine cleanup was not inspected |
| 2026-10-09 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 78.159° |
| 2026-10-09 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 78.201°; saved preference values were not inspected |
| 2026-10-09 | time travel (+30m, +12h) | +30m moved hand 7.573° (residual +0.073°); +12h moved 180.432° (residual +0.432°) |
| 2026-10-09 | midnight date rollover | zone Europe/Prague (saved site); samples 2026-06-20T21:15:00Z, 2026-06-20T21:59:50Z, 2026-06-20T22:00:10Z, 2026-06-20T22:45:00Z advanced the hand 22.571° across civil midnight (residuals +0.000°, +0.157°, +0.144°, +0.071°); the boundary is bracketed within 10s, not resolved |
| 2026-10-09 | total PSS sample | 30418 -> 33066 kB over 10s (growth +2648 kB; budget 8192 kB); not battery or CPU evidence |
| 2026-10-09 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |
=======================================================

Configured checks passed; renderer log scan was inconclusive.
```

The device's post-run state was re-read independently: `wm size` back to `Physical size: 1080x2408` with no override, night mode `yes` (unchanged), wakefulness `Dozing` (the run found the screen off), keyguard dismissed, and the wallpaper process rebounded at PID `32125`.

## Saved-site validation

The zone the rollover phase probes must be the one the app's own `LocationStore.load()` renders, so the parser reproduces that store's field acceptance test rather than trusting a present `zoneId`:

- XML parses and a `<string name="location">` holds non-empty text; the text JSON-parses to an object (Python's `json` rejects trailing data, as Kotlin's `parseRecord` does).
- `version` is an integral JSON number equal to `1` — Python `bool` is excluded explicitly, since `True` is an `int`; `"1"` and `1.0` fail on the same check.
- `latitude`/`longitude` are JSON numbers (not bools or strings, and `NaN` fails the comparison) within the inclusive `±90`/`±180` bounds `ObservingLocation` enforces.
- `source` is exactly `CURRENT_COARSE` or `MANUAL` (case-sensitive).
- `zoneId` is present and a non-empty string. An absent, empty, or non-string `zoneId` is the store's "no stored zone": the parser returns `None` and `resolve_rollover_zone` falls back to `getprop persist.sys.timezone`.

A record failing any of the first four checks returns `None`, matching the store's device-zone fallback. A present `zoneId` is not validated here but resolved by the phase, and one the harness cannot build fails the phase loudly rather than being read as "no saved site". That distinction matters because the app's `ZoneId.of` accepts strings Python's `ZoneInfo` rejects — fixed offsets such as `+02:00`, `Z`, and `UT` — and the picker can hand the app such an id: `TimeZoneLookup.pickerZoneIds` includes `currentZone.id`, documented as possibly "a bare offset inherited from a migrated record". Substituting the device zone there could have sampled the wrong civil midnight while the then-relative residual check still passed, because those expected offsets depended only on elapsed time; the harness now compares each sample with the absolute civil-hand angle in the resolved zone, which does depend on the zone's identity (see the editorial note). The host suite adds regression cases for every field, the boundary values (latitude `90`, longitude `180` valid; `90.0001` rejected), and a case pinning that a present-but-unbuildable id is returned for the phase to reject.

On this device the record parses to `Europe/Prague`, and the run's Phase 6 line confirms the same (`Europe/Prague (saved site)`).

## Zone source and probe method

The four probe instants are built with `zoneinfo.ZoneInfo` from the zone the service renders civil time with. That zone is read from the app's own preference record (`run-as … cat shared_prefs/observing_location.xml`), now validated as above; if that read fails, the harness falls back to `getprop persist.sys.timezone`; if both fail, the phase fails loudly rather than passing without verifying anything. The record on this device is:

```xml
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="location">{&quot;version&quot;:1,&quot;latitude&quot;:50.08,&quot;longitude&quot;:14.42,&quot;source&quot;:&quot;MANUAL&quot;,&quot;zoneId&quot;:&quot;Europe\/Prague&quot;}</string>
</map>
```

For `Europe/Prague` (UTC+2 in summer) and the reference date 2026-06-20, the probe yields the local wall-clock samples 23:15:00, 23:59:50, 00:00:10 (next day), and 00:45:00, i.e. UTC instants `2026-06-20T21:15:00Z`, `2026-06-20T21:59:50Z`, `2026-06-20T22:00:10Z`, `2026-06-20T22:45:00Z`. Each sample's expected clockwise hand offset from the first is its local-time delta × 15°/h: 0.000°, 11.208°, 11.292°, and 22.500°. Because every sample fixes an exact instant, the measured offsets (0.000°, 11.365°, 11.435°, 22.571°; residuals +0.000°, +0.157°, +0.144°, +0.071°) reproduce the earlier run's to the reported decimal, which is expected for a deterministic instant-to-hand function. These residuals are relative offsets from the first sample, as the editorial note records; the current harness instead compares each sample with the instant's absolute civil-hand angle.

## Screen-off CPU observation

The screen was confirmed off (`read_screen_on` false, `mVisible=false`) before and after a measured 4.01-second window, and `time.monotonic()` bracketed the window. The wallpaper PID is now read three times — before the sleep request, after the screen settled, and at the window's end — through `confirm_pid_unchanged`, which fails the phase whenever the end reading is unreadable or differs from the sampling PID. This run exited 0 with no PID failure row, so the closing tick reading is attributable to the process that opened the window. The harness prints only the initial PID (`29645`) and the later rebound (`32125`), not the intermediate readings, so the recorded evidence is that the three-way check passed, not the raw intermediate values. The combined utime+stime ticks from `/proc/<pid>/stat` moved by **0 ticks**: with `CLK_TCK = 100`, zero whole ticks bound the CPU time below one tick — under `0.010s`, i.e. under `0.25%` of the 4.01-second window — against a `0.05s` budget. This is a bounded window observation, not a duty cycle or a battery measurement.

## Limitations

- **The window-end PID check is a passed assertion, not a printed reading.** The harness does not emit the intermediate PID, so the record shows the check succeeded and the phase exited 0; it does not show the three raw values.
- **The civil-midnight boundary is bracketed, not resolved.** The two 10-second samples cannot be individually distinguished by a probe whose jitter is the same order as their 0.083° step; the run demonstrates that the hand advances smoothly across the site's midnight from 11° before to 11° after, not the exact instant of the date change.
- **The residual check recorded here was relative, not absolute.** The residuals above are offsets from the first sample, so a constant angular error on every sample would also have passed. The harness now checks each measured angle against the instant's absolute civil-hand angle in the resolved zone (see the editorial note).
- **The rollover probes only the civil hand.** No astronomy or date-element probe exists in the harness, so `#6`'s "civil time **and astronomy** from the same instant" is only half evidenced; the astronomy half is tracked in [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125).
- **A saved zone the harness cannot build stops the rollover check.** The app's `ZoneId.of` accepts fixed-offset ids (`+02:00`), `Z`, and `UT` that Python's `ZoneInfo` cannot construct; a site saved with such an id makes the phase fail loudly instead of being verified. Extending the resolver to Java's offset grammar is not done here and is tracked in [#125](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/125).
- **The CPU row is a bounded window observation.** It bounds the wallpaper process's CPU time over one confirmed-off window on this device; it is not a duty-cycle, frame-accounting, or battery-drain measurement, and no producer-buffer trace was taken.
- **The renderer log scan is inconclusive.** No matching warning or error records were present, which the harness records as inconclusive rather than as proof of a clean log.
- **Single run.** These are one run's measurements on one device; they are not repeated or averaged.

Co-Authored-By: deepseek-v4-flash-vision-exp <noreply@deepseek.com>
