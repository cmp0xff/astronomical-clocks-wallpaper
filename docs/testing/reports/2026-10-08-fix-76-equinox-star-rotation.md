# Equinox star rotation on the wallpaper surface (#76)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`ce91051cbec2c853a9d1b3d6e55a9db26f05019eaead4e908cde6a5876a971e1`. The `app/src/main` tree it
compiles is byte-identical to `905d6e611c1b6e66eedd44421c9545ff3f7c137f` (`git diff 905d6e6 HEAD --
app/src/main` is empty), so that revision identifies the application sources under test; the branch
head carrying this report identifies the tests, image exports, and documentation only. The artifact
was installed in place with `adb install -r` and did not disturb the existing home-screen wallpaper
binding.

That SHA-256 differs from the value recorded in the pull request body for the same application
sources. The earlier artifact was not available to compare, so the difference is not attributed
here; it cannot come from a production source change, because there is none.

Physical device running Android 16 (API 36). Vendor, model, serial, firmware build, and precise
location are withheld per the device-privacy policy; the saved site is the manual Prague reference
`50.08, 14.42`, `Europe/Prague`, which is a published city centre rather than a personal location.
Firmware build: withheld (embeds the model identifier).

## What was measured and why

On the historical instrument the 0° Aries star is fixed to the zodiac ring, so it turns as the ring
turns; #76 reports that this implementation drew it upright wherever the ring carried it. The fix is
a single `Canvas.rotate` of the star path by the equinox radius-vector angle, which the host tests
check on a 160 px render of a 0.08 sky-radius field of view, about six times the native surface's
linear scale. This pass asks the different question of whether the rotation survives on the real
surface, at the size the wallpaper actually draws.

The star is a five-pointed polygon, so its gold silhouette is five-fold symmetric and its
orientation is defined **modulo 72°**. The measurement is therefore the star's tip direction reduced
mod 72°, labelled `psi` below, recovered as `arg(z5)/5` from the fifth angular harmonic
`z5 = sum(w * (d/R)^2 * exp(5i*gamma))` of the gold mask inside a disc centred on the equinox, with
`gamma` the screen direction of each pixel from the disc centre. The radial weighting emphasises the
five outer tips over the inner valleys, and the fold-5 harmonic ignores the ring rims and the
equatorial graticule, which have lower angular order.

The 0° Aries divider is a gold bar running straight through the equinox, and a straight bar through
the disc centre cancels in the fifth harmonic only to the accuracy of the assumed centre. Measured
on the host renders at the 1x scale, leaving it in the probe disc gives tip residuals of −21.1°,
−20.3°, +10.0°, and +10.6° at S = 0°, 90°, 180°, and 270°, up to 21° of error and far larger than
the star's own contribution. Pixels within `DIVIDER_WIDTH/2` of the predicted divider bearing are
therefore excluded, which reduces those residuals to +3.1°, −1.1°, +0.0°, and −1.8°. Because the
star and the divider are both fixed to the ring, that exclusion removes a *constant* offset rather
than a varying one, and the per-capture steps below are unaffected by whatever residual bias
remains.

The frame is validated before it is measured. The predicted equinox pixel and the predicted tip
bearing come from independent geometry, not from the renderer: `R = tan(45° - eps/2)` with
`eps = 23.4372°`, `E = R*(sin S, -cos S)`, `S = GMST + 14.42°` from the Meeus/IAU 2006 Greenwich mean
sidereal angle at the frozen instant plus the site's east-positive longitude, and the predicted tip
direction `180° + eps + S`. The GMST
implementation agrees with the repository's ERFA/SOFA `gast06a` fixture column to within 0.0023° at
all four of the fixture instants, which is the mean-versus-apparent difference and far below the
measurement resolution. On every capture the predicted equinox then landed on the drawn star, and
the predicted and measured tip lines coincided to within a few degrees by eye, which is what
validates the frame before the numbers are read.

Two surface sizes were captured. The native 1080×2408 surface gives a sky radius of 338.98 px and a
star 13.6 px across, about 15 fully saturated gold pixels inside the probe disc. The display-size
override `wm size 2160x4816` doubles that to a 27 px star and about 100 gold pixels, and is the
measurement reported as the primary one; it was reset afterwards. The probe disc radius was 22 px at
2x and 12 px at 1x, each staying inside the 0.0375 sky radii at which the band's gold rim begins
(25.4 px and 12.7 px respectively).

The virtual clock was frozen with `DEBUG_SET_TIME --es instant` rather than an offset, so a capture
cannot drift, and each freeze was confirmed from the service log line
`Debug clock fixed to instant: <instant>`. The four instants are spaced 5 h 59 min 1 s apart, which
is 89.9999° of sidereal rotation, so the expected step is exactly 18.000° after the 72° fold. The
base instant is the June solstice, which puts the Sun's ecliptic longitude about 90° from the star
so that no Sun marker sits on the probe. The palette was pinned dark with `cmd uimode night yes`
(gold `#D8B66A`) and restored to `auto`.

## Observed results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-08 | build, verify, install | `./gradlew qualityGate :app:assembleDebug` passed at 575 tests per variant with detekt, ktlint, and Android Lint clean; `scripts/verify-apk.sh`, codespell 2.4.3, and `git diff --check` passed; `adb install -r` replaced the previous build in place, the service restarted under a new PID (13134 → 19466), and the home-screen binding re-rendered without being reapplied |
| 2026-10-08 | k=0, S=283.6266° | 2x surface: measured tip 54.015° against a predicted 55.064°, residual −1.049°, 101 gold px |
| 2026-10-08 | k=1, S=13.6265° | 2x surface: measured tip 1.654° against a predicted 1.064°, residual +0.590°, 101 gold px, step +19.639° |
| 2026-10-08 | k=2, S=103.6264° | 2x surface: measured tip 18.645° against a predicted 19.064°, residual −0.419°, 100 gold px, step +16.991° |
| 2026-10-08 | k=3, S=193.6263° | 2x surface: measured tip 35.653° against a predicted 37.063°, residual −1.411°, 101 gold px, step +17.008° |
| 2026-10-08 | four-step advance | Steps of +19.639°, +16.991°, and +17.008° against the 18.000° a rigidly attached star must show for a 90° sidereal step; the largest deviation is 1.639° |
| 2026-10-08 | repeated at native size | Same four instants on the unmodified 1080×2408 surface: 52.995°, 2.873°, 16.302°, 36.812°, residuals −2.069°, +1.810°, −2.761°, −0.251° and steps +21.878°, +13.429°, +20.510°; coarser, as the 15-16 gold px per capture imply, and consistent with the 2x row |
| 2026-10-08 | pre-fix baseline, computed | The unrotated star has its tip at screen angle −90° at every sidereal angle, so it must read a constant 54.000° at all four instants. No pre-fix APK was installed for this pass; the figure is what the previous code path draws, which is what makes the four captures a discriminating test rather than a single one — at k=0 the two hypotheses differ by only 1.06° |
| 2026-10-08 | device state restored | `wm size reset` (read back as physical 1080×2408 with no override), `cmd uimode night auto`, the debug clock reset to system UTC (confirmed from the service log), the screen returned to `mWakefulness=Dozing`, the keyguard left not showing, and `stay_on_while_plugged_in`, `screen_off_pocket`, and `proximity_sensor` unchanged at 0, 1, and 1 throughout |
| 2026-10-08 | renderer log scan | Inconclusive: the `AstronomicalClocksWallpaperService:W` and `DialRenderer:W` filter matched no record over the run, so it neither passed nor reported a warning |

The capturable evidence is the four magnified star crops with the predicted equinox and tip
overlaid: in each one the cross sits on the star's centre and the measured and predicted tip lines
coincide, and the star's arms point in visibly different directions across the four panels.

## What this does not establish

The measurement resolution is about ±1.5° at the 2x surface, so it bounds the star's rendered
orientation to a few degrees; it is not a claim of sub-degree rendering accuracy. The device site is
northern, so the southern-plate mirror of the rotation is host-tested only and is not re-measured
here. Orientation is defined only modulo 72° by the star's own symmetry, so the captures separate
the fixed and rigidly-rotating hypotheses through their absolute predictions, not through an
unfolded angle. Only the dark palette was exercised, the display-size override was in effect for the
primary rows, and `screencap` composites the launcher under the wallpaper, so the four probe
positions were inspected visually at both sizes to confirm no launcher content overlapped them.

This pass measured the star's orientation only. Cadence, screen-off behaviour, reboot, the lit lock
screen, saved-state restoration, and the battery and CPU protocol were not re-run against this
build, and remain owed to #6. A host check establishes no device behavior on its own.
