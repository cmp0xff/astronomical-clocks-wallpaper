# Orloj display controls on the wallpaper surface (#5)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew clean :app:assembleDebug`, APK SHA-256
`1176e96612b3ab608107652bd49ca1bc814de8dec8ff29219d9922889c61c300`. The `app/src/main` and
`app/src/test` trees it compiles were byte-identical to `16a2353` when the artifact was built and
exercised, so that revision identifies the application and test sources under test. The commits after
`16a2353` otherwise touch `scripts/`, the guides and this report. One later editorial correction
changes a single assertion in `WallpaperLayoutDiagnosticTest` and nothing in `app/src/main`, so the
APK and its hash still identify the artifact while the test tree now differs in that one file.
The artifact was installed in place with `adb install -r` and the existing home-screen wallpaper
binding survived, rendering under a new PID.

The hash comes from a clean build, reproduced twice. Rebuilding the same sources incrementally after
an unrelated source change and its revert produced a different artifact, so the recorded hash is only
meaningful for a clean tree and a clean build.

Physical device running Android 16 (API 36), 1080x2408 at 450 dpi. Vendor, model, serial, firmware
build, and precise location are withheld per the device-privacy policy; the saved site is the manual
Prague reference `50.08, 14.42`, `Europe/Prague`. Firmware build: withheld (embeds the model
identifier).

Two runs are recorded here. The first, on an earlier artifact, found and then re-verified a placement
defect; both the defective and the fixed build are kept because the defect is the reason that fix
exists and the pre-fix numbers are what a later reader has to reproduce to see it. The second is the
combined acceptance pass on the artifact above, which walks every reachable acceptance criterion in
#5. Sections are labelled so the two are not confused.

## What was measured and why

The controls under test percent-scale one dial and place it, from `DialDisplaySettings`, within a
usable rectangle that the engine derives per frame from its Canvas, its display resources, the
offsets the launcher reports, and the system insets. The host suite pins that arithmetic on synthetic
rectangles; this pass asks whether the rectangle the framework really hands the engine produces the
composition a user sees, which is not something a host test can establish.

Two independent measurements were taken for every state. The first is the debug-only `DialLayout`
report, requested with the `DEBUG_SET_TIME` diagnostics broadcast and read back from logcat, which
carries the engine's own centre, radius, brightness and palette together with the display metrics it
resolved; `scripts/device_layer.py` rejects a report unless exactly one visible engine answers and
the reported centre and its stroked extent (`radius * STROKED_EXTENT`) fit the captured frame. The
second is a pixel measurement straight off `screencap`: the bounding box of the dark palette's gold
`#D8B66A`, which is drawn as a stroked circle
at the dial's outer radius. Agreement between a number the engine computes and a number read off the
framebuffer is what separates a diagnostic that lies from one that works. In the first run the two
disagreed by 664 px; in the second they agree to about a pixel.

Where a state had to be set to an exact value, it was reached through the control itself - the slider
was dragged to the end of its track - and the on-screen label plus `shared_prefs/dial_display.xml`
were then read back, so a mis-landed tap shows up as a disagreement rather than as a silent
substitution. Sites other than the Prague reference were written into `shared_prefs/observing_location.xml`
under a live process and read back by a fresh process, which is the technique the #35 pass documents.

## First run: the defect this pass found

Measured on the pre-fix artifact, APK SHA-256
`a12e7a98e65b613562606eaa5aa815e1a83db38ec2c76c0afd153c2f736e1440`, revision `896b9e6`. The
`resolve` inputs below come from a temporary instrumented build that logged them and was then
reverted; it was never installed as the reported artifact.

| Check | Observed |
| --- | --- |
| home engine, defaults | reported `cx=208.0 cy=1217.0 radius=178.88 brightness=100`; measured gold rim centred (872, 1216), half-width 178 |
| `resolve` inputs, home engine | `surface=1080x2408 display=1080x2408 offsets=-664,0 insets=0,68,0,42` -> `(664.0, 68.0, 1080.0, 2366.0)` |
| `resolve` inputs, preview engine | `surface=1080x2408 display=1080x2408 offsets=0,0 insets=0,68,0,42` -> `(0.0, 68.0, 1080.0, 2366.0)` |
| engine start, first two frames | frame 1 `cx=540.0 radius=464.4`, frame 2 `cx=208.0 radius=178.88` 0.3 s later, reproduced on every restart and after a launcher restart |
| horizontal Position 0 / 99 | reported `cx=179.4` / `236.0`; the drawn rim's left edge sat at screen x 722 against a predicted 721, confirming the drawn centre was 664 px right of the reported one |
| Size 50 / 100 / 114 | reported `radius=89.4` / `178.88` / `203.9` |
| `scripts/device_smoke.py` | FAIL `hand not found; wallpaper must be visible and unobstructed`, FAIL `hand not drawn after surface recreation`, while the wallpaper was visible and correctly drawn |
| hand detector, both centres | reported centre (208, 1217) -> no hand found; drawn centre (872, 1217) -> hand at 151.63 deg |
| surface covered | the whole screen reads the wallpaper background `#101923` and dims with it, so the Canvas is the screen and the reported centre cannot be a screen coordinate |

`WallpaperViewport.resolve` treated the launcher's reported `xPixelOffset` as the window's left edge
and then clamped the window's *end* to the surface, so a surface no larger than the display - which is
what this device allocates - collapsed its usable rectangle to whatever slack remained beyond the
reported scroll. The launcher reports `xPixelOffset = -664` for a surface exactly as large as the
display, so the rectangle became 416 px wide starting at surface x 664, and the default dial moved to
the right edge at 38.5% of its intended radius. The same offset was then added back into the
`DialLayout` centre, so the diagnostic reported a point the renderer never drew to and the device
harnesses could not find the dial.

The fix clamps the window's *start* to the slack the surface actually has beyond it, which leaves a
screen-sized surface at 0, and drops the offset from the reported centre. That second step is right
for this device, whose launcher never scrolls, but wrong in general; the second run replaced it with
the general rule and is described below.

## Second run: the diagnostic coordinate space

`44a8f61` removed the `+ offsetX` term on the reasoning that a surface no larger than the display is
not scrolled, so the drawn centre is the screen centre. That holds here and fails wherever a launcher
does scroll: the framework shows the window `[origin, origin + min(surface, display)]` at screen 0, so
the screen centre is the drawn centre minus the *clamped window origin*, which `resolve` already
computes and then discards. Copilot's review of `37b8cf4` raised this as "Report dial centre in screen
coordinates for panned surfaces", and it is the reason `docs/device-testing.md`'s promise of a
screen-space centre and `scripts/device_layer.py`'s frame check could not both be honoured on a panned
surface.

`WallpaperViewport` now retains that origin as `windowOriginX`/`windowOriginY` from the resolve that
computed it, and `diagnoseLayout` subtracts it. The render path is unchanged; the framework does the
scrolling. `WallpaperLayoutDiagnosticTest` builds a 2000x1000 surface on a 1080x1000 display, scrolls
it by -500, and asserts both the exact screen-space centre and the property the device layer enforces,
that `cx +/- radius * STROKED_EXTENT` stays inside the captured frame; the assertion fails against a
deliberately un-subtracted centre on both configured SDK levels. The device pass below cannot exercise
this path - this launcher reports an offset on a surface no larger than the display, so the origin is
0 - and the rule is host-tested only.

## Observed results, combined acceptance pass

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-08 | build, verify, install | `./gradlew qualityGate :app:assembleDebug` passed at 593 tests per debug/release variant with detekt, ktlint and Android Lint clean; `scripts/verify-apk.sh`, codespell 2.4.3, Ruff 0.16.10 check and format, ty 0.0.84, `python3 -I -m unittest discover -s scripts -p 'test_*.py'` (122 tests: 46 in `test_device_smoke.py`, 76 in `test_device_qualification.py`) and `git diff --check` passed; `adb install -r` replaced the previous build in place and the home-screen binding re-rendered without being reapplied |
| 2026-10-09 | home defaults | reported `cx=540.0 cy=1217.0 radius=464.4 brightness=100 dark=true`; measured gold rim bbox centre (539.5, 1216.5), half-width 464.5 |
| 2026-10-09 | Size 50%, low endpoint | label `Size: 50%`, `dial_display` `size=50`; reported `radius=232.2`, exactly half of 464.4; measured rim bbox centre (539.0, 1216.5), half-width 232.0 |
| 2026-10-09 | Size 115%, high endpoint | label `Size: 115%`, `dial_display` `size=115`; reported `radius=534.06 = 1080 x 0.43 x 1.15`, the arithmetic the contract states. The earlier run stopped at 114% because a tap one step short landed there; dragging the thumb to the end of the track reaches 115 exactly |
| 2026-10-09 | Horizontal 0% at Size 115% | reported `cx=535.61926`, which equals the 535.61926 px stroked extent at radius 534.06, so the dial's outer stroke is flush with the usable rectangle's left edge at screen x 0 |
| 2026-10-09 | Horizontal 100% at Size 115% | reported `cx=544.38074`, equal to `1080 - 535.61926`, flush with the right edge |
| 2026-10-09 | Vertical 0% at Size 115% | reported `cy=603.61926`, equal to the 68 px top inset plus the stroked extent; the topmost gold rim pixel in the capture is at y=68 |
| 2026-10-09 | Vertical 100% at Size 115% | reported `cy=1830.3807`, equal to `2366 - 535.61926`; the bottommost gold rim pixel is at y=2365 against the usable rectangle's bottom edge of 2366, a 1 px sampling boundary |
| 2026-10-09 | four corners at Size 115% | (0,0) `cx=535.61926 cy=603.61926`; (100,0) `544.38074, 603.61926`; (0,100) `535.61926, 1830.3807`; (100,100) `544.38074, 1830.3807`. At the last of these the measured rim bbox centre is (544.0, 1830.0) with half-width 535.0, so the drawn dial matches the reported one at the corner as well as at the centre |
| 2026-10-09 | Size 50% at a corner | `cx=847.1221 cy=300.878 radius=232.2`; the extent scales with the radius, so the smaller dial is still flush at both edges |
| 2026-10-09 | Reset display | returned `cx=540.0 cy=1217.0 radius=464.4 brightness=100` and `dial_display` to `size=100 horizontal=50 vertical=50 brightness=100`, from a state of Size 50 as well as from Size 115 |
| 2026-10-09 | Reset preserves site, layers and appearance | Reset was applied with the site set to Sydney, the Moon layer unchecked (`dial_settings` `moon=false`) and appearance `DARK`. Afterwards `observing_location.xml` still held `-33.8688, 151.2093, Australia/Sydney`, `dial_settings` still held `moon=false`, and `appearance_settings` still held `DARK`; only `dial_display` changed |
| 2026-10-09 | persistence, process recreation | at Size 115% and Brightness 80% the engine reported `cx=540.0 cy=1217.0 radius=534.06 brightness=80` before and after `run-as <pkg> kill <pid>` (pid 32573 -> 1843); the binding survived and the settings were re-read |
| 2026-10-09 | persistence, reboot | after `adb reboot` and boot completion the binding was still this service, the process was new (pid 5066), and the dial still reported `radius=534.06 brightness=80`; `dial_display`, `observing_location` (Sydney at that point), `appearance_settings` (`DARK`) and `dial_settings` (`moon=false`) all survived unchanged |
| 2026-10-09 | site, southern | site seeded to `-33.8688, 151.2093, Australia/Sydney` and read back from `observing_location.xml`. The dial drew the southern plate, with the night region on the opposite side from the northern render and the civil hand at the site's local time; the southern mirror, which the earlier passes recorded as host-tested only, is now exercised on hardware |
| 2026-10-09 | site, polar | site seeded to `-77.85, 166.67, Antarctica/McMurdo`, header read `-77.8500, 166.6700 (manual)` / `Timezone: Antarctica/McMurdo`, and the dial redrew with the hand at the site's local hour |
| 2026-10-09 | no-site | with `observing_location.xml` removed and the process restarted the file was not recreated, the header read `No observing location set.`, and the dial drew the bare 24-hour civil clock only - no zodiac ring, no sky, no Sun or Moon. The Prague reference was then restored |
| 2026-10-09 | site timezone independent of the phone | with the phone timezone moved to `Europe/London` while the site stayed `Europe/Prague`, the frozen instant `2026-10-25T00:30:00Z` drew the hand at 217.729 deg against the 217.5 deg that Prague's UTC+2 civil time of 02:30 predicts. Had the phone zone driven the dial it would have read 202.5 deg at 01:30 BST; London's fall-back at 01:00 UTC that day leaves the second frozen instant also at 01:30 local. The phone timezone was restored to `Europe/Prague` |
| 2026-10-09 | site zone DST transition | the two frozen instants `2026-10-25T00:30:00Z` and `2026-10-25T01:30:00Z` straddle Prague's CEST -> CET change and are both 02:30 local. Measured hand 217.729 deg and 217.701 deg, a step of -0.028 deg for a one-hour step of UTC: the civil clock follows the site zone's DST rule, not UTC |
| 2026-10-09 | preview engine | the live picker's preview reported `cx=540.0 cy=1217.0 radius=464.4 brightness=100` and drew the dial in the preview area; it resolves its own viewport with no reported offsets |
| 2026-10-09 | lit lock screen | the lock slot was bound to the platform `ImageWallpaper` before this pass. Applying the wallpaper from the picker with "Sperrbildschirm" rebound that slot to this service. No keyguard exists on this device (`locksettings get-disabled` is true, `isKeyguardShowing=false` across a screen-off/screen-on cycle and across the reboot), so no lock surface ever renders either wallpaper and the criterion could not be exercised. See #122; the slot was not restored |
| 2026-10-09 | insets | the usable rectangle's insets are 68 px at the top and 42 px at the bottom, read independently from the drawn rim's extreme pixels at the vertical endpoints as well as from the endpoint centres. The display cutout is a 66 px top inset, so the system bar, not the cutout, sets the top |
| 2026-10-09 | cropping and offsets | the launcher delivers `xPixelOffset = -664` on a surface exactly as large as the display, so the window has no slack, the clamped origin is 0, and the offset is inert: the default dial reports the exact screen centre while the offset is non-zero. A genuinely panned surface is not reachable on this launcher and is host-tested only |
| 2026-10-09 | orientation | `settings put system user_rotation 1` with `accelerometer_rotation 0` left the display at rotation 0 and 1080x2408 throughout: the home screen is portrait-locked, so the landscape path and the wider-than-display surface were not reproduced. Both settings were restored |
| 2026-10-09 | dimming, Brightness 80/90/100 | labels and `dial_display` read 80, 90 and 100. The wallpaper background `#101923` measured (12, 20, 28), (14, 22, 31) and (16, 25, 35), exactly x0.8 and x0.902; the gold rim measured (173, 146, 85), (195, 164, 96) and (216, 182, 106), the same factors. `settings get system screen_brightness` read 13 at all three steps with `screen_brightness_mode=1`, so the device brightness was untouched |
| 2026-10-09 | dimming preserves geometry | the measured rim bbox centre (539.5, 1216.5) and half-width 464.5 were identical at Brightness 80, 90 and 100, and the engine reported `radius=464.4` at each |
| 2026-10-09 | equinox rotation, default composition | two surfaces: the native 1080x2408 and, for resolution, the `wm size 2160x4816` override, both at the default composition. Four instants 5 h 59 min 1 s apart - 89.9999 deg of sidereal rotation, so a rigidly attached star must step exactly 18.000 deg after the 72 deg fold. On the 2x surface the measured tips were 55.621, 2.070, 18.385 and 38.012 deg against predictions 54.993, 0.993, 18.993 and 36.993 deg, residuals +0.628, +1.077, -0.608 and +1.019 deg, with steps +18.449, +16.314 and +19.627 deg. The predicted equinox landed on the drawn star in every capture, confirmed on magnified crops |
| 2026-10-09 | equinox rotation, Size 115% | the same four instants at the Size extreme, on the 2x surface: tips 56.544, 2.130, 18.652 and 38.689 deg, residuals +1.551, +1.137, -0.340 and +1.696 deg, steps +17.586, +16.522 and +20.037 deg. The star tracks the ring at the extreme composition as well as at the default |
| 2026-10-09 | equinox probe calibration | the probe disc radius was set to 10.0 px, a ratio of 0.74 to the star's 13.6 px outer radius at 2x and default size, so the disc stays inside the silhouette. A 22 px disc, matching the earlier report's stated radius, admitted the zodiac band's gold rims: it collected 123 px instead of 44 and scattered the same residuals to between -20 deg and +12 deg. The measured step is unchanged in sign and rough size but the residuals are this pass's own, not a reproduction of the earlier report's |
| 2026-10-09 | `scripts/device_smoke.py` | passed: baseline hand 175.483 deg, at +30 m 183.042 deg, advance 7.559 deg against 7.500 deg expected, residual +0.059 deg; surface recreation `wm size 1080x2000` then reset, hand drawn afterwards |
| 2026-10-09 | `scripts/device_smoke.py`, tightened parser | re-run after `21233a7` to cover the parser's new stroked-extent check, which postdates this pass: passed, advance 7.572 deg against 7.500 deg expected, residual +0.072 deg; surface recreation OK; the renderer log scan was inconclusive |
| 2026-10-09 | `scripts/device_qualification.py` | passed at `--max-pss-growth-kb 6144`: baseline hand 175.663 deg; screen-off observed with `mVisible=false` and the hand detected after wake at 175.719 deg; preview navigation returned at 175.766 deg; surface recreation at 175.793 deg; process rebind pid 19959 -> 21455; +30 m advance 7.558 deg (residual +0.058) and +12 h 179.822 deg (residual -0.178); total PSS 26697 -> 27689 kB over 10 s, growth +992 kB against the 6144 kB budget |
| 2026-10-09 | `scripts/device_qualification.py`, rebind retry | the process-rebind poll now catches a transient `ScreencapError` when the first capture after a fresh PID finds no usable layout and retries within its poll budget instead of aborting the run; an exhausted budget still fails. The change postdates this pass, which ran the pre-change harness and never hit the transient path, so every number above stands. Host-tested only; no device re-run |
| 2026-10-09 | renderer log scan | Inconclusive: the `AstronomicalClocksWallpaperService:W` and `DialRenderer:W` filter matched no record over either harness run, so it neither passed nor reported a warning |
| 2026-10-09 | device state restored | `wm size` and `wm density` read back as physical with no override, `persist.sys.timezone=Europe/Prague` with `auto_time_zone=1`, `screen_off_pocket=1`, `proximity_sensor=null`, `user_rotation=0`, `accelerometer_rotation=1`, `cmd uimode night auto`, `stay_on_while_plugged_in` deleted back to null, and the home binding still this service. The app's own state was left at the defaults the pass started from: `dial_display` at 100/50/50/100, `appearance_settings` `SYSTEM`, all three layers enabled, and the Prague site. The lock-slot binding is the one setting this pass changed and did not restore |

## What this does not establish

Landscape and any surface wider than the display remain unmeasured on hardware. The home screen of
this device is portrait-locked, so `user_rotation=1` left the display at 1080x2408 in rotation 0, and
its launcher hands over a surface exactly as large as the display while reporting
`xPixelOffset = -664`. Both the orientation path and a genuinely panned, cropped surface are therefore
host-tested only, including the screen-space `DialLayout` rule this pass fixed; #122 records them.

The lit lock screen was not exercised twice over: no keyguard exists on this device, so no lock
surface renders, and the lock slot now bound to this service was not returned to the platform
`ImageWallpaper` it held before. Nothing renders from either, but the binding is a device setting this
pass left changed and a later pass should restore it.

The equinox rows above are this pass's own instrument, not a reproduction of the earlier equinox
report's: the same estimator with a wider probe disc scatters by tens of degrees, and the narrowed
disc that is reported here has a resolution of roughly 2 deg on the step. The measurement bounds the
star's rotation to that resolution; it is not a sub-degree claim, and the absolute residuals are
consistent with the star being drawn at the predicted equinox rather than with any particular
rendering accuracy.

Only the dark palette was measured across the controls. The southern and polar captures were taken
with the Moon layer disabled and the Dark appearance chosen, and the appearance and layer defaults
were restored afterwards without re-measuring the light palette or the Sun, Moon and equinox markers
under the controls. `screencap` composites the launcher over the wallpaper, so the dimming was
measured on wallpaper-painted background rather than on launcher content, and the launcher's own edge
panel overlaps the right margin of some captures. The virtual clock was reset after the equinox and
DST measurements and confirmed back at system time.

Cadence, screen-off behaviour beyond what the qualification harness samples, low-memory recovery,
allocation and battery or CPU qualification were not established by this pass and remain owed to #6.
A host check establishes no device behavior on its own, and this report records no milestone
completion, release or tag.
