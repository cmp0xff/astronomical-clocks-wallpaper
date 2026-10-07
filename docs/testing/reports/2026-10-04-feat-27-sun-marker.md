# Sun marker verification (#27)

Test build: local debug `app-debug.apk` from `feat/27-render-sun` at 4ffa962, the review fix on top of
the rebase at 80e1a9f (APK SHA-256
1e165b93b8cb1b07b1744ddfc86d3f81cc5c358aebdc064bfd94a07facd46b47), the artifact installed on the
device below. Subsequent commits in the original `feat/27-render-sun` work (c0cb0ba and later)
are documentation and host-test changes only, leaving `app/src/main/` untouched and producing the identical APK bytecode and SHA-256.
At 4ffa962, `./gradlew qualityGate :app:assembleDebug` passed with 376 unit tests per build variant
(now 377 following subsequent host-test additions) and no detekt, ktlint, or Android Lint findings,
and `scripts/verify-apk.sh` verified the application ID, SDK levels, debug flag, permissions, wallpaper
declaration, Astronomy Engine notice, and APK Signature Scheme v2.

Same physical device as the earlier passes. Android version: 16 (API 36). Firmware build: withheld
(embeds the model identifier), per issue #20. The saved site was the device's own current-location
fix, so its coordinates are withheld as private data too; the checks below report residuals against
that site rather than repeating it. Sydney was entered by hand as `-33.86785, 151.20732`, which is not
the device's position.

**Method.** The civil hand turns 0.0041667 degrees per second and the Sun marker 0.0041781, so neither
the eye nor a frame diff can separate a marker that tracks the sky from one that holds a fixed
position. Each frame is therefore measured, not judged: every capture is bracketed by
`adb shell date -u` before and after, and three quantities are read off the frame and compared with
values computed from ERFA rather than read back from the renderer.

- The dial centre and scale come from a least-squares fit to the outer gold rim, whose radius is
  `OUTER_RADIUS = 1.37`; the sky radius is `min(w, h) * 0.43 / 1.37`. Every capture fitted to
  (539.5, 1203.5) with a rim radius of 465.1 px against 464.8 expected, on a 1080x2408 canvas whose
  centre is (540, 1204).
- The marker is the densest gold disc within the sky radius, excluding the hand hub. Gold is matched
  by hue shape after normalizing each pixel to its brightest channel, so the check does not depend on
  a literal ARGB value or on a screenshot colour transform.
- The civil hand's bearing is the far tip of the `DialStyle.HAND` pixels.

The independent expectation uses Greenwich apparent sidereal time from `erfa.gst06a`, the saved
site's east-positive longitude, and the Sun's geometric ecliptic longitude by the recipe recorded in
[`DialGeometryFixture.kt`](../../../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/DialGeometryFixture.kt).
The marker's bearing is the Sun's hour angle — `H = local apparent sidereal angle − right ascension`,
noon at the top of the Roman scale — and its projected radius is
`tan(45° ± declination/2) / tan(45° + true obliquity/2)`, with the northern form above the equator and
the southern form below. The two handles are largely independent: the radius moves with the
declination, and so with the Sun's longitude, not only with the hour angle.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install | `qualityGate` 376 tests per variant at build time (377 at PR tip), no static-analysis findings; `verify-apk.sh` passed; APK SHA-256 `1e165b93…d46b47` installed with `adb install -r` over the existing binding |
| 2026-10-04 | marker, Prague | Measured bearing 182.907° against 182.881° expected, residual **+0.026°**; projected radius 206.9 px against 206.9 px, residual **+0.01 px**; 0.09 px apart on the dial |
| 2026-10-04 | distinct from the 0° Aries star | With the zodiac ring on, the star's five-pointed glyph sits at radius 0.657 (the equator circle) and bearing 15.8°, and the marker's radiant disc at radius 0.608 and bearing 185.4°; the two never share a probe |
| 2026-10-04 | marker, Sydney | Measured bearing 322.821° against 322.796° expected, residual **+0.025°**; projected radius 239.1 px against 240.0 px, residual **−0.95 px**; 0.95 px apart, and a different bearing and radius from Prague at the same instant |
| 2026-10-04 | marker follows sidereal time | Two captures 82.09 min apart with the same layers: the bearing advanced 20.363° against 20.527° expected, residual **−0.164°** (about 0.6 px), and the radius moved 206.9 → 206.3 px, so the marker advanced along the ecliptic rather than holding a position |
| 2026-10-04 | missing site | After clearing app data, Settings read "No observing location set." with both layer checkboxes greyed out and disabled, and the preview drew only the 24-hour scale and hand. Clearing also dropped the binding to the stock `ImageWallpaper`, as #47 records; after reapplying, the dial had no marker and no gold anywhere inside the sky radius |
| 2026-10-04 | Sun toggle | Off removed the sky and twilight fills, both contour strokes, and the marker, leaving the plain grid; on restored all of them on the next visible frame |
| 2026-10-04 | Zodiac ring toggle | Off removed the band, its labels and dividers, and the equinox star, leaving the marker as the only gold inside the sky radius; on restored them |
| 2026-10-04 | surface recreation | `wm size 1080x2000` then `wm size reset`: the dial re-centred to (539.5, 999.5) and back to (539.5, 1203.5), the marker holding the same normalized bearing and radius in both, with `WallpaperService` logging `handleResized` and `Session.relayout` |
| 2026-10-04 | screen off/on | The dial redrew with the marker present at the same normalized position, and `logcat` for the process held no renderer warning |
| 2026-10-04 | lit lock screen | With the device's lock screen temporarily enabled, the dial and marker drew behind the lock UI at the same normalized position; the overlay dims the palette, so this check is structural |
| 2026-10-04 | repaint cadence | 8 producer buffer acquisitions in the 7.000 s trace window, deltas 0.998-1.002 s, so 1.000 Hz |
| 2026-10-04 | reboot | `adb reboot`: new process, the wallpaper binding and the restored site survived, and the marker matched its expectation to **0.43 px** |
| 2026-10-04 | saved-site timezone | With the site on `Europe/Prague`, `cmd alarm set-timezone Asia/Kolkata` moved the phone to +05:30 and the status bar to 05:55 IST; the civil hand stayed within 0.035° of Prague civil time and the marker's bearing held at 203.779° in both frames, its own sidereal advance of 0.034° being below the probe's resolution |

The Prague and Sydney rows are the same claim at two hemispheres: the marker's bearing and projected
radius both match an expectation that never consults the renderer, in the northern plate that
`DialGeometryFixture.kt` also exercises and in the southern plate #27 additionally requires. The
residuals are of the same size as the measurement noise. The probe localizes a 7 px disc to about
0.5 px, which is 0.14° of bearing at this radius, and the engine's modelled ΔT differs from the
fixture's by about 6.5 s, which is a further 0.027° of rotation; the Prague residual of +0.026° is
that second term almost exactly. Nothing here therefore establishes an accuracy better than about
1 px, which is what the rows claim.

Two device settings were changed for this pass and restored afterwards: the lock screen, which the
device had disabled, was enabled for its row and set back to disabled; and the screen timeout was
raised to 30 minutes with stay-awake on so the screen would not sleep mid-pass, then restored to
5 minutes with stay-awake off. The saved site was restored to the device's current-location record
after Sydney and after the missing-site row, and the device was left on it with the wallpaper applied
and both layers at their defaults.

**What this pass did not run.** Battery and frame cost remain #6; the cadence check shows only that a
frame is produced once per second. The 0° Aries star's own position is `ZodiacRenderer`'s claim and
`DialRendererTest`'s to keep; this pass only checks that the marker is distinguishable from it. The
equinox-instant and season tests are host tests against fixtures. Always On Display remains out of
scope per #2. The screenshot pipeline returned the palette unchanged on these captures — `#D8B66A`
read back as `#D8B66A` — unlike the transform earlier passes recorded; the marker probe normalizes
each pixel before matching, so its result does not depend on which of the two applies.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.
