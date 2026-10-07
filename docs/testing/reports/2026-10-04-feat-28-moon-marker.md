# Moon marker verification (#28)

Test build: local debug `app-debug.apk` from `feat/28-render-moon` at 25bda1a (APK SHA-256
`9af9b6e861f1b400a47f96900e524da259f56aaa64eeee31f8e343f59c6d0dad`), the branch tip when the artifact
was assembled, installed with `adb install -r` over the existing binding. At the time of the original
feature work, the only later commit on
`feat/28-render-moon` was this report, which leaves `app/src/` untouched and produces the identical APK.
`./gradlew qualityGate :app:assembleDebug` passed with 396 unit tests per build variant and no
detekt, ktlint, or Android Lint findings, and `scripts/verify-apk.sh` verified the application ID,
SDK levels, debug flag, permissions, wallpaper declaration, Astronomy Engine notice, and APK
Signature Scheme v2.

Same physical device as the earlier passes. Android version: 16 (API 36). Firmware build: withheld
(embeds the model identifier). Both sites were entered by hand, so neither is the device's own
position and no private location appears below: Prague `50.08, 14.42` (the site the earlier passes
used) and Sydney `-33.86785, 151.20732`.

**Method.** The Moon moves about 0.000152 degrees of ecliptic longitude per second, and the marker
rides the ecliptic ring, so neither the eye nor a frame diff can separate a marker that tracks the
sky from one that holds a position. Each frame is therefore measured, not judged: every capture is
bracketed by `adb shell date -u` before and after, and the quantities below are read off the frame
and compared with values computed from ERFA, never read back from the renderer.

- The dial centre and scale come from a least-squares fit to the outer gold rim, whose path radius
  is `OUTER_RADIUS = 1.37`. The fit lands on the rim stroke's outer edge, half a stroke beyond the
  path, so `RIM_WIDTH / 2` is subtracted. Every capture fitted to (539.5, 1203.5) with a rim radius
  of 465.56 px, 464.20 px after the 1.36 px stroke correction against 464.40 expected, on a
  1080x2408 canvas whose centre is (540, 1204); the sky radius follows as 338.84 px.
- The marker is the pair of `DialStyle.MOON_SHADOW` and `DialStyle.MOON_ILLUMINATED` pixels. Both
  values are unique in the palette and matched exactly here: this device's screenshot pipeline
  returned the palette unmodified, `#2C3E50` and `#E8EEF5` reading back as themselves. The disc
  centre is the centroid of the two regions together; the terminator is the midpoint between the
  nearest edges of the lit and unlit regions along the horizontal through that centre.
- The three comparison markers are located as gold components inside the sky disk. The Sun marker's
  radiant disc and the vernal-equinox star are told apart from the ring, its dividers, and its
  labels by their distance from the Sun's and the star's own expected places.

The independent expectation uses Greenwich apparent sidereal time from `erfa.gst06a` and the Moon's
geocentric ecliptic longitude and phase from the recipe recorded in
[`DialGeometryFixture.kt`](../../../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/DialGeometryFixture.kt):
`erfa.moon98` reduced to the true ecliptic and equinox of date. The marker's bearing is the ecliptic
longitude's hour angle — `H = local apparent sidereal angle − right ascension`, noon at the top of
the Roman scale — and its projected radius is
`tan(45° ± declination/2) / tan(45° + true obliquity/2)` with the northern form above the equator and
the southern form below. The phase is compared two ways: which limb is lit, and where the terminator
crosses the horizontal through the disc centre, at `sign(sin φ)·cos(φ)·r` for phase longitude φ,
mirrored for a southern site.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install | `qualityGate` 396 tests per variant, 0 failures, no static-analysis findings; `verify-apk.sh` passed; APK SHA-256 `9af9b6e8…6d0dad` installed in place; Settings showed a third, checked **Moon** checkbox beside **Zodiac ring** and **Sun** |
| 2026-10-04 | marker, Prague | At 10:50:59 UTC measured bearing 75.5330° against 75.5344° expected, residual **−0.001°**; projected radius 326.77 px against 326.57 px, residual **+0.21 px** |
| 2026-10-04 | distinct from the Sun marker and the 0° Aries star | In the same frame the Sun marker measured bearing 359.957°/radius 0.6074 and the star 190.400°/0.6568, against 359.984°/0.6074 and 190.313°/0.6564 expected. The Moon marker sat at 75.533°/0.9638: about 84° of bearing and 0.36 of radius from the Sun, and 115° and 0.31 from the star, so the three never share a probe |
| 2026-10-04 | marker, Sydney | At 10:52:48 UTC measured bearing 212.7960° against 212.7544° expected, residual **+0.042°**; projected radius 151.62 px against 151.49 px, residual **+0.14 px**. Both the bearing and the radius differ from Prague's at the same instant, as the southern plate requires |
| 2026-10-04 | phase fidelity and hemisphere mirror | Both captures caught a **waning crescent**, phase longitude 281.72° (Prague) and 281.74° (Sydney), 39.8% illuminated by the ephemeris. Prague lit the **left** limb with the terminator at −1.40 px against −1.65 px, residual **+0.25 px**, and a lit area fraction of 0.361; Sydney lit the **right** limb with the terminator at +1.64 px against +1.65 px, residual **−0.02 px**, and 0.363. Same phase, opposite limb |
| 2026-10-04 | marker follows the ephemeris | Two captures 31.42 min apart on the same site and layers: the bearing advanced 7.5493 deg against 7.5468 deg expected, residual **+0.0025 deg** (about 0.01 px at this radius), and the projected radius moved 326.77 -> 326.57 px, so the marker advanced along the ecliptic rather than holding a position |
| 2026-10-04 | missing site | After `pm clear` of the app data, Settings read "No observing location set." with the **Zodiac ring**, **Sun**, and **Moon** checkboxes all `enabled="false"` while still `checked="true"` from their defaults. Clearing also dropped the binding to the stock `ImageWallpaper`, as #47 and #27 record; after reapplying, the dial drew only the 24-hour scale, its hand, and the plate's plain fill: **0** shadow and **0** illuminated marker pixels, and **0** sky and **0** twilight fill pixels, against 34557 and 14677 in the Prague frame |
| 2026-10-04 | Moon toggle | Off took the marker to **0** shadow and **0** illuminated pixels while the frame's gold count was unchanged (5054 sampled pixels against 5055), so nothing else moved; on restored it on the next visible frame |
| 2026-10-04 | surface recreation | `wm size 1080x2000` then `wm size reset`: the dial re-centred to (539.5, 999.5) and back to (539.5, 1203.5), the marker holding 213.82°/151.67 px and 213.84°/151.65 px against 213.81°/151.68 px before, with `WallpaperService` logging `handleResized: which=7`, `Session.relayout`, and a 1080x2000 then 1080x2408 buffer |
| 2026-10-04 | screen off/on | The dial redrew with the marker present, bearing residual **+0.021°** and radius **+0.18 px**, and `logcat` held no renderer warning |
| 2026-10-04 | lit lock screen | With the device's lock screen temporarily enabled, the dial and marker drew behind the lock UI at the same normalized position, bearing residual **+0.038°**, radius **+0.15 px**, terminator **−0.02 px**; the palette read back unmodified, so on this device the lock overlay did not dim the wallpaper layer |
| 2026-10-04 | repaint cadence | 8 producer buffer acquisitions in the 6.999 s trace window, deltas 0.999-1.002 s, so 1.000 Hz |
| 2026-10-04 | saved-site timezone | With the site on `Europe/Prague`, `cmd alarm set-timezone Asia/Kolkata` moved the phone to +05:30 and the status bar to 16:29:43 IST; the civil hand held 14.957° against 14.929° for the site's 12:59:43 CEST, where following the phone zone would have put it at 67.429°, and the marker held its bearing (residual +0.034°) and radius (+0.13 px). The zone was restored to `Europe/Prague` |
| 2026-10-04 | renderer log | `adb logcat --pid=<pid>` filtered for renderer output was empty over a 31 s steady 1 Hz soak, and no "skipping frame" or "skipping render" line appeared in the full buffer |

The three marker rows are the same claim in two hemispheres and against two other drawn objects. The
bearing and the projected radius both match an expectation that never consults the renderer, in the
northern plate that `DialGeometryFixture.kt` also exercises and in the southern plate #27 additionally
requires; the residuals are of the same size as the measurement noise. The probe localizes an 8.13 px
disc to about 0.3 px, which is 0.05 deg of bearing at this radius, and the engine's modelled Delta-T
differs from the fixture's by about 6.5 s, a further 0.027 deg of rotation. Nothing here establishes an
accuracy better than about half a pixel, which is what the rows claim.

The phase rows do not depend on that scale. The terminator's horizontal position is an independent
geometric prediction from the phase longitude, and it landed within 0.25 px in Prague and 0.02 px in
Sydney; the lit-area fraction that the exact-colour probe reports (0.361 and 0.363 against 0.398
expected) under-counts both regions by their antialiased boundary, which is why the rows rest on the
terminator and the lit limb rather than on the fraction. Both captures happened to catch a waning
crescent, which is the case that tests the mirror least: a waning crescent lights the left limb in
Prague and the right one in Sydney, and the signs of the terminator offsets follow.

Three device settings were changed for this pass and restored afterwards: the lock screen, which the
device had disabled, was enabled for its row and set back to disabled; the phone timezone was moved to
`Asia/Kolkata` for its row and restored to `Europe/Prague`; and the saved site was cleared for the
missing-site row, which also dropped the wallpaper binding, after which the wallpaper was reapplied to
home and lock screens and the site re-entered by hand as manual Prague `50.08, 14.42` with both layers
at their defaults. The screen timeout and stay-awake were read back unchanged at 300 s and off. The
device was left with the wallpaper applied to both screens, the dial drawing, and the saved site on
Prague.

**What this pass did not run.** The reboot row is deliberately not run, per the reduction agreed for
this pass: a process restart is therefore not re-evidenced here, and #27's own reboot row remains the
most recent [process-recreation evidence](2026-10-04-feat-27-sun-marker.md) on this device.
Battery and frame cost remain #6; the cadence check shows only that a frame is produced once per second. The 0° Aries star's own position is
`ZodiacRenderer`'s claim and `DialRendererTest`'s to keep; this pass only checks that the marker is
distinguishable from it and from the Sun. Always On Display remains out of scope per #2. The screenshot
pipeline returned the palette unchanged on these captures, `#D8B66A` reading back as `#D8B66A` and
`#2C3E50` as itself, so the capture cannot adjudicate the exact gold but can and does match the two
Moon tones exactly.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.

The renderer-log observations above are withdrawn as clean-log evidence: a completed empty scan is
inconclusive, and these runs were not repeated with the hardened collection checks. Original
APK/source attribution is retained.
