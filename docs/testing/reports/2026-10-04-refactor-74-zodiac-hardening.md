# Zodiac hardening verification (#74)

Test build: local debug `app-debug.apk` from `refactor/74-zodiac-hardening` at faa4d04 (APK
SHA-256 61d95b92a8c440fe3f2bfdd2ccc1f18d1d23f4904d87ef8aea1076d93f6ea264), the artifact installed on
the device below, built from a clean tree at that revision.

The pass below predates the original `refactor/74-zodiac-hardening` branch's rebase onto `main`,
which brought in the Sun rendering (#27):
`faa4d04` and that APK SHA-256 name the pre-rebase build that was actually installed, and the rebase
re-created the commit as `eb8fe7a` without repeating the device checks.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

This is the light follow-up spot-check to PR #81. The refactor (discriminant clamp, `try-finally`
canvas restore, and test hardening) changes no visual output, so the device check confirms the
structural output is unchanged from the [device-verified #43 build](2026-10-03-feat-43-zodiac-compartments.md),
rather than re-running the full geometry pass. The site was entered through the settings app's coordinate fields as manual Prague
`50.08, 14.47`.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place |
| 2026-10-04 | compartments, Prague | Twelve sign labels (ARI through PIS) around the ring, each centred in its own compartment, with a gold radial divider on every boundary between them |
| 2026-10-04 | star against a divider, Prague | The vernal-equinox boundary carried about 2.4x the gold of a plain divider in the night band, so the star is drawn rather than only the divider |
| 2026-10-04 | repaint cadence, preview | Producer buffer acquisitions 0.988-1.013 s apart, so 1.000 Hz |
| 2026-10-04 | renderer log | `adb logcat --pid=<pid>` empty over a 31 s steady 1 Hz soak, and no "skipping frame" warning appeared |

The device was left on the saved manual Prague coordinates after the pass.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.

The renderer-log observation above is withdrawn as clean-log evidence: a completed empty scan is
inconclusive, and that run was not repeated with the hardened collection checks. Original
APK/source attribution is retained.
