# Pure-read location storage and startup migration verification (#35)

Test build: local debug `app-debug.apk` from `refactor/35-pure-location-store-read` at
`43929d5efbadcfc7f4e43fb2559bd10fd92eb732` (APK SHA-256
`0234c82610a47a87ff8218a462a5d1350bd899561da17d5c18cb456bc496e5c6`), built from a clean tree at
that revision and installed with `adb install -r` over the previous build.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, device timezone
`Europe/Prague`. Firmware build: withheld (embeds the model identifier).

`AstronomicalClocksApplication.onCreate()` runs `LocationStore.migrateAndRepair()` once per process
start, and `LocationStore.load()` never writes. Each startup state was written straight into
`shared_prefs/observing_location.xml` with `run-as` while the process was alive, the process was then
killed as its own uid (`run-as <pkg> kill -9 <pid>`, which keeps the wallpaper binding, unlike
`am force-stop`), the framework re-created it, and the log and file were read back. The owner's saved
Prague site was backed up before the pass and restored afterwards.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-05 | install over a valid record | `adb install -r` replaced the previous build in place; `dumpsys wallpaper` still named the app's service, and the saved version-1 record was left byte-identical (file mtime unchanged) with no `LocationStore` line: a valid supported record is not rewritten at startup |
| 2026-10-05 | legacy flat record | Three legacy string keys on disk (`latitude`, `longitude`, `source`) became one version-1 record carrying `zoneId` `Europe/Prague`, with no warning logged |
| 2026-10-05 | invalid `zoneId` | A version-1 record with `zoneId` `Bad/Zone` and an unknown `retainedField` was repaired once at startup: logcat carried `W LocationStore: repaired invalid observing location timezone 'Bad/Zone'; using Europe/Prague`, and on disk `zoneId` became `Europe/Prague` while `retainedField` was preserved |
| 2026-10-05 | second launch after repair | Killing and re-creating the process again logged no `LocationStore` line and left the file mtime unchanged, so the repair runs once |
| 2026-10-05 | malformed record | A record of `{` was left byte-identical on disk (mtime unchanged) and produced two identical warnings at process start, consistent with startup maintenance and the wallpaper's initial read, both `W LocationStore: ignoring malformed observing location record` |
| 2026-10-05 | restored site | The owner's Prague record was written back and a restart logged nothing; the home screen redrew the dial |

**Limitations.** The device timezone is `Europe/Prague`, the same as the saved site, so the repaired
fallback zone cannot be distinguished from the site's geographic zone by its value; the repair is
established by `zoneId` changing from `Bad/Zone` to the device zone, not by the two differing. The
process was re-created by killing it as its own uid rather than by a reboot, so the framework's service
restart stands in for a cold boot. Only the four seeded startup states were exercised. No OEM, model,
firmware, or serial identifiers are recorded.

No device settings were changed for this pass: no `svc power stayon`, `screen_off_pocket`, `font_scale`,
`wm size`, or night-mode change was needed, and the screen was woken only to capture the dial. The
wallpaper binding survived every step; the app was never force-stopped or cleared.
