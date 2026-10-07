# Dial caching and render-failure containment verification (#39)

Test build: local debug `app-debug.apk` from `perf/39-dial-caching-resilience` at 8935527 (APK
SHA-256 8dd818135b89cd8810c78347fe3a61d51aa72585bbebcafcfaeb41bfa58da213), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The caching commit (5272a51) and its review fixes (8935527) change the steady-state render path from
re-sampling plate contours to redrawing pre-built `Path` objects, and route the first visible frame
through the same `runTick()` error containment as the posted ticks. This pass re-runs the cadence,
rendering, nesting, and lifecycle checks of the [Orloj foundation](2026-10-03-feat-4-orloj-foundation.md)
and [southern plate](2026-10-03-feat-4-southern-plate.md) reports against that build, so a
change to either path would show up against the pre-caching record rather than against inspection.

The twelve zodiac sign glyphs are drawn in `DialStyle.HAND`, so the hand angle was measured with the
zodiac ring off, then the ring was restored; every other check ran with the ring on. The screenshot
pipeline's colour transform still applies, so these checks are structural rather than exact-colour,
except the hand angle, which is a principal-axis measurement of the hand pixels.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` passed; `scripts/verify-apk.sh` verified the application ID, SDK levels, debug flag, permissions, wallpaper declaration, Astronomy Engine notice, and APK Signature Scheme v2 |
| 2026-10-03 | repaint cadence, preview | 6 producer frames in the 6 s trace window, deltas 0.999-1.002 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, home | 6 in 6 s, deltas 0.998-1.001 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, lit lock screen | 6 in 6 s, deltas 0.999-1.001 s, so 1.000 Hz |
| 2026-10-03 | 24-hour scale, home | XII top, XXIV bottom, VI left, XVIII right, one hand; 24 hour ticks at 15° spacing; day, twilight, and night fills, and the zodiac ring with its twelve sign glyphs |
| 2026-10-03 | 24-hour scale, lit lock screen | Same scale, ticks, fills, and ring as home at the same instant |
| 2026-10-03 | hand angle, home | Principal axis 67.950° against 67.933-67.936° implied by the device clock, 0.014-0.017° apart |
| 2026-10-03 | hand angle, lit lock screen | 68.083° against 68.092-68.095°, 0.009-0.012° apart |
| 2026-10-03 | hand angle, preview | 68.432° against 68.435-68.438°, 0.003-0.006° apart |
| 2026-10-03 | equator | (0, 0) put the horizon on a straight line through the hub, day above and twilight then night below |
| 2026-10-03 | Sydney | (-33.86785, 151.20732) put night at the dial centre, a twilight annulus around it, and day outside |
| 2026-10-03 | poles | (90, 0) and (-90, 0) each gave concentric day, twilight, and night annuli, day outermost and night innermost; the two renders were pixel-identical apart from the hand |
| 2026-10-03 | hide/show | 0 producer frames while another app was focused; 6 in 6 s within a second of returning home |
| 2026-10-03 | surface recreation | `wm size 1080x2000` and then `wm size reset`: the dial re-centred on the smaller surface, cadence 6 in 6 s, and no renderer warnings |
| 2026-10-03 | reboot | New process, the wallpaper binding and both stored choices survived, 24 ticks and the zodiac ring drew, and cadence held at 6 in 6 s |
| 2026-10-03 | renderer log | `adb logcat --pid=<pid>` was empty over a 90 s steady 1 Hz soak, and no "skipping frame" warning appeared at any check above |

The equator and Sydney nesting reproduces the [southern plate report](2026-10-03-feat-4-southern-plate.md)'s
rows taken before the caching commit, supporting unchanged geometry at those sites. The poles'
concentric annuli resemble its “south pole” observation, but that report's recorded `(0, -90)` input
is inconsistent with the label and remains unverified. This pass's explicit `(90, 0)` and `(-90, 0)`
rows stand as separate polar observations on the caching build. The two poles agree because the
southern plate is the radial
inversion of the northern one through the equator circle, so both hemispheres give the same ring
structure. The [2026-09-29 feasibility rows](2026-09-28-feat-2-device-feasibility.md)
record 60 tick strokes (12 long, 48 short) for the earlier three-hand
dial; this dial draws 24 hour ticks, one per Roman numeral. After the pass the saved site was
restored to the device's current location and the wallpaper was left applied.

The follow-up commit that bounds the tick-loop failure log changes no rendering or scheduling
behaviour, so the checks above were re-run against its APK (SHA-256
3c3a5010f7b9c95ffe67173aaa6ef68e017f6d53dd105ada639ef89418a8ff7d). The home screen still drew its 24
hour ticks at 1.000 Hz (6 producer frames in the 6 s trace window, deltas 0.998-1.002 s) with an empty
renderer log. The throttling itself is not device-testable, because it needs a fault that repeats on
every tick and a healthy device does not produce one; `RepeatedFailureLogTest` and
`WallpaperFrameTest.repeatedDrawFailureLogsOnce` are its evidence, the same limitation as the
resilience claim below.

The review-response commit that keeps the Sun-layer paths out of the cache while that layer is
disabled changes no rendered output, and was checked on its own APK (SHA-256
bbf298cf187c6153b6eac04ed48fbcd724c694fafcc91a5e523567fc9f821ca5). With the Sun on, the home screen
drew 24 hour ticks at 1.000 Hz (6 producer frames in the 6 s trace window, deltas 0.999-1.001 s) with
the day, twilight, and night fills present and an empty renderer log. With the Sun off, the twilight
and day fills counted zero pixels while the tropic and equator grid circles stayed drawn, and
re-enabling the Sun restored both fills and both boundary strokes on the next frame.

**Unresolved limitation.** The resilience claim itself is not device-testable: the contained failure
is an injected first-frame `drawFrame()` exception, which cannot be produced on a healthy device
without a debug hook this build does not carry. `WallpaperFrameTest` and `WallpaperFoundationTest`
remain its evidence, and this pass verifies only that the containment path leaves the rendered output
and the 1 Hz tick loop unchanged. The cadence check still shows only that a frame is produced once
per second; battery and frame-cost qualification remain #6.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.

The renderer-log observations above are withdrawn as clean-log evidence: a completed empty scan is
inconclusive, and these runs were not repeated with the hardened collection checks. Original
APK/source attribution is retained.
