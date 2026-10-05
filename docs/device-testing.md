# Physical-device testing

Procedures and acceptance results for verifying the live wallpaper on a physical Android device.
This page is deliberately device-agnostic about identity: the device is referred to only as
"the physical device", and its model, OEM, and serial number are omitted from public surfaces per
the project's device-privacy policy. Android version is non-identifying and is recorded below; the
firmware build string embeds the model identifier, so it is withheld.

## Connecting

Prefer wireless debugging (Android 11+, API 30+) so no USB cable or vendor ID is needed:

```sh
adb pair <host>:<port> <pairing-code>   # one-time pairing from Developer options
adb connect <host>:<port>
adb devices                             # confirm the device is listed as "device"
```

A USB connection works too; the steps below are transport-independent.

## Install and launch

The application ID changed from `io.github.cmp0xff.astronomicalclockswallpaper` to
`io.github.godaniya.astronomicalclockswallpaper` in #47, so Android installs the new build as a
separate app. Remove any pre-migration debug build first; otherwise both stay installed and the
old wallpaper binding and saved data remain on the device. Saved site and dial settings do not
carry over to the new package.

```sh
adb uninstall io.github.cmp0xff.astronomicalclockswallpaper.debug   # only if a pre-migration build is installed
adb install -r app-debug.apk   # replaces a previous build of the same application ID in place
adb shell am start -n \
  io.github.godaniya.astronomicalclockswallpaper.debug/io.github.godaniya.astronomicalclockswallpaper.SettingsActivity
```

In **Astro Clocks**, tap **Open wallpaper preview**, then apply it to the home and lock screens.

## Inspecting state

```sh
adb shell dumpsys wallpaper                            # active component and visibility
adb shell pidof io.github.godaniya.astronomicalclockswallpaper.debug
adb logcat --pid=<pid> -v time                         # follow the running wallpaper process
adb shell screenrecord /sdcard/clock.mp4               # record; press Ctrl-C to stop
adb pull /sdcard/clock.mp4
```

## Lifecycle transitions

Each #2 transition is mapped to the `ClockEngine` callback it should trigger, so an observation can
be checked against the expected handler.

| Transition | Expected `ClockEngine` behavior |
| --- | --- |
| Preview open | `onCreateEngine`, then `onVisibilityChanged(true)` draws and schedules the next tick |
| Preview close | `onVisibilityChanged(false)` cancels the pending tick |
| Lock / unlock (lit lock screen) | `onVisibilityChanged(false)` / `(true)` across the surface switch |
| Screen off | `onVisibilityChanged(false)` cancels the pending tick |
| Screen on | `onVisibilityChanged(true)` redraws and reschedules from wall time |
| Surface recreation (no visibility change) | `onSurfaceDestroyed` cancels the pending tick; `onSurfaceChanged` redraws and reschedules while visible |
| Reboot | Process and engine recreated; clock resumes from the device wall time |

## Observed results

Test build: local debug `app-debug.apk` from `feat/2-device-feasibility` (SHA-256
881cb234654bd4967e3c9ca4c9a84003723ec8ac94f5ce2c80f2d133d58b5eef; logging-only change,
rendering unchanged).

Rendering was later extracted into `DialRenderer` on `feat/19-rendered-clock` with no intended
visual change. The reviewed renderer preserved the original `#D8B66A` dial colour. That build's host
verification on 2026-09-29 ran `DialRendererTest` on SDK 26 and 36: four cardinal-time checks
anchored orientation, and all three hands were independently checked at 12:20:43, 04:42:03, and
08:03:23. Literal expected angles, isolated 3x3 presence windows, and single-pixel overdraw probes
accounted for pixel rounding and antialiasing; the test documented the distances from other hands
and ticks. It also checked the tick bands, non-square canvas, palette, and renderer reuse on a
200x200 bitmap.

Regression checks first confirmed that the original-colour assertion rejected `#D8B26A`. Temporarily
setting each hand's length to zero, rotating it by 90 degrees, or doubling its length (one change at
a time) failed all three dispersed-time checks on both SDKs for every hand. All nine mutations were
restored before final verification.

That three-hand, 60-tick renderer was superseded by the Orloj foundation, which draws a single
24-hour hand on a 24-numeral scale; `DialRendererTest` now covers that renderer, and the checks above
stand only as the record for the `feat/19-rendered-clock` build. See the Orloj foundation section
below.

`AstronomicalClocksWallpaperServiceTest` covers resuming ticks after surface recreation. `WallpaperFrameTest`
injects null/throwing acquisition, drawing failures, and posting failures through the engine's real
frame operation. It verifies exception logging, one posting attempt for each acquired canvas, and a
subsequent successful scheduled frame. Unrelated drawing exceptions still propagate after posting.
These are host checks only; they do not verify a physical wallpaper surface.

`./gradlew qualityGate :app:assembleDebug` passed with 78 tests per build variant (SDK 26/36 combined),
including strict detekt and Android Lint. `scripts/verify-apk.sh` verified the local debug APK, SHA-256
851bdd8639d0f571569024bfbb5f27861f31ba94aa1c09634e54e1f3f5c205a7, the same artifact that was
installed on the physical device below.

Device verification on 2026-09-29 installed that APK and confirmed core rendering and ticking on the
physical device. Geometry was measured from `adb shell screencap` frames against the angles `ClockState`
produces: 60 tick strokes (12 long, 48 short) and three hands whose reach was 0.504, 0.754, and 0.851 x
the dial radius, matching the 0.50, 0.75, and 0.85 the renderer specifies for the hour, minute, and
second hand. At a capture near device time 17:01:49 the hour hand measured 151.03 degrees and the
minute hand 10.53, within 0.4 degrees of the 150.9 and 10.8 that wall time implies; the second hand
read 288.13 degrees, or 48 s. A lock-screen capture measured the same structure, 12 long and 48 short
ticks with hands reaching 0.505, 0.754, and 0.850 x radius, all three within 0.32 degrees of what
17:04:44 implies. Across 15 frames spanning 9.3 s the second hand's angle stayed within 0.15 degrees of
a whole 6 degree multiple, the discrete once-per-second step the engine schedules rather than a smooth
sweep. `adb logcat` for the wallpaper process reported no skipping-frame or renderer warnings while the
dial was visible.

The device's screenshot pipeline applies a colour transform (`#111923` reads back as `#131922`, and
`#D8B66A` as `#D1BC7E`), so pixel checks against a capture must allow for that offset. The shift is
far larger than the 4/255 green difference between `#D8B66A` and the rejected `#D8B26A`, so captures
cannot adjudicate that palette detail; `DialRendererTest` remains the authority for it.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

The `2026-09-28` observations below are from the `feat/2-device-feasibility` build. The `2026-09-29`
observations are from `feat/19-rendered-clock` at `c3bd25b`, which carries the `DialRenderer`
extraction.

| Date | Surface | Transition | Observed |
| --- | --- | --- | --- |
| 2026-09-28 | preview | open | Clock advanced |
| 2026-09-28 | preview | close (apply) | Applied to home and lock screens via **Open wallpaper preview** → **Set wallpaper** |
| 2026-09-28 | home | apply | Clock advanced |
| 2026-09-28 | home | repeated lock/unlock | Clock advanced after each of three cycles; process survived |
| 2026-09-28 | home | screen off/on | Clock advanced |
| 2026-09-28 | lit lock | lock/unlock | Clock advanced |
| 2026-09-28 | lit lock | screen off/on | Clock advanced |
| 2026-09-28 | home + lit lock | reboot | Clock recovered; wallpaper persisted and service restarted |
| 2026-09-29 | preview | open | Dial rendered: dark slate background, gold dial, 60 ticks, 3 hands |
| 2026-09-29 | preview | close (apply) | Applied to home and lock screens via **Open wallpaper preview** → **Set wallpaper** |
| 2026-09-29 | home | apply | Measured 12 long + 48 short ticks and 3 hands at 0.504/0.754/0.851 x radius; hands matched the device clock |
| 2026-09-29 | home | ticking | Second hand held whole 6 degree steps, 6 degrees per second |
| 2026-09-29 | lit lock | apply | Same dial on the lock screen: 12 long + 48 short ticks, hands at 0.505/0.754/0.850 x radius |

Unresolved limitations: neither device-tested build had an observed failure. Reboot and
surface-recreation behavior is documented above but was not re-run for `feat/19-rendered-clock`; only
the `2026-09-28` rows cover those transitions. Always On Display is out of scope per #2.

## Location slice (#3)

Test build: local debug `app-debug.apk` from `feat/3-current-location` at a845a5d, the rebase of the
review fixes onto `main` at 7dbff58 (APK SHA-256
cf285da99ffc8b7cc3cc667b51fcf3042d01fd836751d17f16dd2391ed323310).

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-28 | manual coordinates + relaunch | Saved `45.5, -120.25` as MANUAL; persisted through force-stop and relaunch |
| 2026-09-28 | use current location (grant) | Approximate-location prompt; stored a real network fix as CURRENT_COARSE; no failure logs |
| 2026-09-28 | refresh | Re-fetched cleanly; current value retained |
| 2026-09-28 | deny permission | Permission-denied toast; prior selection preserved |
| 2026-09-28 | location off → refresh | "Could not get the current location" toast; logged "network location provider disabled"; prior selection preserved |
| 2026-09-28 | corrupt stored prefs | Discarded invalid values; no crash; fell back to "No observing location set." |
| 2026-09-29 | manual coordinates + relaunch | Entered `45.5, -120.25`, saved with **Save coordinates** ("Location saved." toast); the display read `45.5, -120.25 (manual)` and held it through a force-stop and relaunch |
| 2026-09-29 | use current location (grant) | The prompt asked only for the approximate location; granting stored a real network fix as CURRENT_COARSE, the display switched to `(current)`, and no failure was logged |
| 2026-09-29 | refresh | A fresh provider registration completed after one update; the current value was retained |

The `2026-09-28` rows apply to cf30cf5 only, and their failure paths (denial, disabled location,
corrupt preferences) have not been re-run since; those remain historical evidence alongside the
automated failure tests. The three happy paths above were re-verified on the rebased build on
`2026-09-29`. The rebase changed no application source, so the re-run and the API 26/36 suite
together cover the PR #23 review fixes (manual-save cancellation, permission-flow recreation, cache
age, provider failure recovery, wrongly typed preferences, localized coordinate entry, and settings
scrolling) on the post-merge codebase.

### Coordinate locale fix (2026-09-29)

Test build: local debug `app-debug.apk` from `feat/3-current-location` with the coordinate locale fix
on top of 5db6f02 (APK SHA-256
34c600ef0c162f8a32bd1c93852e2091ca3de92cae6505839243476bfa0a57a1). Same physical device, Android 16
(API 36), the device's own `de-DE` locale, and no locale override.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-29 | manual entry, dot | Typed `1.2` / `2.3`; **Save coordinates** showed the "Location saved." toast, the display read `1.2, 2.3 (manual)`, and `observing_location.xml` held latitude 1.2 / longitude 2.3 as MANUAL |
| 2026-09-29 | manual entry, relaunch | Force-stop and relaunch restored `1.2, 2.3 (manual)` |
| 2026-09-29 | on-screen comma key | With the latitude field focused, Gboard's German numeric keypad accepted `,`, `0`, `5`, `.` in that order, leaving `,05.` in the field |
| 2026-09-29 | keyboard filter, before and after | The same `,`, `0`, `.` taps on a pre-fix build of 5db6f02 (APK SHA-256 f6e8a9e8759d5f321b71127ee3d59f2e4b2aae3e91eadabb105b6717b5bcabb1) left `0.` in the field: the layout filter admitted the dot and dropped the comma |
| 2026-09-29 | manual entry, comma | Entered `1,2` / `2,3` with `adb shell input text` (the virtual keymap's comma) and saved as `1.2, 2.3 (manual)` |

Coordinate entry uses `.` as its decimal separator in every locale and accepts the locale's separator
as an alias. `parseCoordinate` normalizes `.` to the locale decimal separator before parsing with the
locale-aware `NumberFormat`, and `CoordinateKeyListener` derives each field's accepted characters
from the same `textLocale`, so the character filter can no longer drop a separator the parser
accepts. That listener replaced the layout's `numberDecimal` filter, which under `de-DE` admitted `.`
but dropped the comma, leaving no fractional coordinate enterable on the deployment device.
`SettingsActivityLocaleTest` and `CoordinateKeyListenerTest` pin the behavior: `de-rDE` accepts `45,5`
and `8.5`, `ar-rEG` accepts `٤٥٫٥` and `45.5`, and `en-US` still filters `,` out of the field as a
grouping separator.

The `2026-09-29` manual-entry row in the table above the fix was driven with the app's locale
overridden to `en-US`, because `adb` synthetic input could not produce the comma the German keyboard
would; that override is no longer needed for manual entry, and the row stays as evidence for the
build it tested.

Unresolved limitations: the display now rounds coordinates to four decimals, as recorded under
"Coordinate display precision (2026-10-01)" below. The entry fields are seeded from the saved site
(#53), so an unchanged Save needs no retyping and keeps the precision below the fourth decimal; that
seeding landed with its interactive device checks unrun, folded into #42. The offline city chooser
remains outstanding under #3.

## Saved-site timezone verification (#24)

Automated coverage uses fixed instants and explicit zones for site time, Prague's spring and
autumn DST boundaries, preference updates across multiple engines, hidden/destroyed engines,
and recreation. Settings tests change the phone zone while an acquisition is in flight to
verify capture at save time, and acquire on top of an already-saved site to verify that a refresh
retains its zone rather than re-capturing the phone's. These Robolectric checks do not establish
physical-device behavior.

Test build: local debug `app-debug.apk` from `feat/24-site-timezone` at d21c6a7 (APK SHA-256
d5e0235d5d772044324b02505e8d19486d13912f55b8c77852b21781d7dd8335).

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-30 | save + display | Entered the neutral coordinates `35.68, 139.69`, which are not the device's location, and tapped **Save coordinates**; the display read `35.68, 139.69 (manual)` and `Timezone: Europe/Prague`, and the stored record held `"source":"MANUAL"` with `zoneId` `Europe/Prague` |
| 2026-09-30 | phone-zone change | Turned automatic zone selection off and moved the phone to `Asia/Kolkata` (UTC+05:30); `getprop persist.sys.timezone` and `date` both then showed +05:30, which is 3.5 h from the captured +02:00, and this was confirmed before any later observation |
| 2026-09-30 | core retention | Force-stop and relaunch while the phone was on `Asia/Kolkata` left the display and the record reading `Europe/Prague`. On the home screen at 20:38:36 UTC the dial's hands measured 320.5° and 234.0°; the saved site's civil time, 22:38:37 CEST, is 319.3° and 231.7°, while the status bar read 02:08 IST |
| 2026-09-30 | hide/show | Home, then system Settings, then Home: the dial redrew at the saved site's civil time (hands 324.0° and 275.0° at 20:45:26 UTC against 322.7° and 272.6° expected) and the provider pid was unchanged |
| 2026-09-30 | surface recreation | `wm size 1080x1921` then `wm size reset`: the dial recentred to (539,960) at radius 325 and back to (540,1170) at radius 324, the hands tracked the saved site throughout, and logcat showed `onSurfaceChanged`, `onSurfaceRedrawNeeded`, and a drawn window about 15 ms later in each direction, with no skipped or lost frames |
| 2026-09-30 | process restart | `am force-stop` emptied `pidof` and the provider did **not** rebind on returning home: the system fell back to its stock `ImageWallpaper` and the dial was gone until the wallpaper was applied again by hand, after which the saved site was still intact |
| 2026-09-30 | refresh inversion | With the phone restored to `Europe/Prague` and the saved site still on `Asia/Kolkata`, **Refresh location** replaced the manual record with a live coarse fix and re-captured the phone zone: the display and the record both flipped to `(current)` with `zoneId` `Asia/Kolkata` |
| 2026-09-30 | saved site, both directions | With the phone on `Europe/Prague` and the saved site on `Asia/Kolkata`, the dial showed the site's civil time (hands 70.0° and 109.5° at 20:47:57 UTC against 69.0° and 107.7° expected) while the status bar read 22:47 CEST, the reverse of the core retention row |

Applying the wallpaper by hand after a force-stop restores the saved site unchanged, but the system
does not rebind a force-stopped provider on its own, so a killed process leaves the stock wallpaper
in place. The `refresh inversion` row above records that build re-capturing the phone's current zone
and overwriting the saved one; that is the defect fixed by the build in **Refresh zone retention
(2026-09-30)** below, and the row stays as the d21c6a7 build's historical record.

A current-location acquisition with no site saved yet still captures the phone zone; establishing
each site's geographic timezone remains #24, with the offline city chooser in #21.

### Refresh zone retention (2026-09-30)

Test build: local debug `app-debug.apk` from `fix/24-refresh-zone-retention` at 12e4184, the fix
that keeps a saved site's zone when its coordinates are refreshed (APK SHA-256
eb78aa7167ead68e737ae11409432f5e9c533e0158fcf3fddcf59555abe61f6f).

Same physical device. Android version: 16 (API 36). Firmware build: withheld (embeds the model
identifier).

| Date | Check | Observed |
| --- | --- | --- |
| 2026-09-30 | save + baseline | Entered the neutral coordinates `35.68, 139.69` and tapped **Save coordinates** while the phone was on `Europe/Prague`; the display read `35.68, 139.69 (manual)` and `Timezone: Europe/Prague`, and the stored record held `"source":"MANUAL"` with `zoneId` `Europe/Prague` |
| 2026-09-30 | phone-zone change | Turned automatic zone selection off and moved the phone to `Asia/Kolkata` with `cmd alarm set-timezone`; `getprop persist.sys.timezone` read `Asia/Kolkata` and `date` read `Thu Oct 1 02:45:55 IST 2026`, which is 3.5 h from the baseline's +02:00, and this was confirmed before interpreting the refresh |
| 2026-09-30 | refresh retention | Tapped **Refresh location** with the phone still on `Asia/Kolkata`: the display and the record both flipped to `(current)`, so `source` became `CURRENT_COARSE` and the neutral coordinates were replaced by the live coarse fix, while the display's `Timezone:` line and the stored `zoneId` both stayed `Europe/Prague`. The live coordinates are the device's own position and are deliberately not recorded |

The run-as record read matched the on-screen `Timezone:` line in every row. The phone zone was
restored to `Europe/Prague` with automatic time-zone selection re-enabled afterwards. The pre-fix
behaviour this replaces is the `refresh inversion` row above, from the d21c6a7 build.

### Coordinate display precision (2026-10-01)

Test build: local debug `app-debug.apk` from `fix/24-refresh-zone-retention` with the coordinate
display fix on top of 12e4184 (APK SHA-256
6f2a54f349ce63de7fc87a3c413a1cca6264944013bc467cc6c2b9bbf457a409).

Same physical device. Android version: 16 (API 36). Firmware build: withheld (embeds the model
identifier).

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-01 | install + display | After reinstalling the APK, the record left by the run above — a `(current)` coarse fix — also rendered with four decimals on each side, with its `Timezone: Europe/Prague` line intact. The live coordinates are the device's own position and are deliberately not recorded |
| 2026-10-01 | high-precision entry | Entered `-33.86785` / `151.20732`, which are not the device's location, and tapped **Save coordinates**: the display read `-33.8678, 151.2073 (manual)`, while the stored record held `"latitude":-33.86785,"longitude":151.20732` unchanged, so only the display rounds. The fourth decimal is 8 rather than 9 because the stored `Double` is a hair below -33.86785 |
| 2026-10-01 | neutral entry | Entered the neutral `35.68` / `139.69` and tapped **Save coordinates**: the display read `35.6800, 139.6900 (manual)` with `Timezone: Europe/Prague`, and the stored record held `"source":"MANUAL"` with `zoneId` `Europe/Prague` |

The run-as record read matched the on-screen `Timezone:` line in every row. The device ends on the
neutral record with the phone zone: **Refresh location** was tried twice at the end of the session
to restore the device's own site, and both attempts timed out after 10 s —
`LocationProvider: network location update timed out after 10000ms`, followed by the toast logged
from `fetchCurrentLocation`'s failure branch — leaving the display and the record unchanged.

The display interpolates four decimals — about 11 m — and `.` in every locale, which matches the
coordinate entry convention recorded above. `formatCoordinate` rounds to four decimals and then
formats with `Locale.ROOT`, so the readout has a uniform width and does not change with the phone's
locale; the `Double.toString()` it replaces printed every digit the `Double` carries. `LocationStore`
is untouched: `save()` still writes the full `Double`, so a stored coordinate keeps the precision it
was entered with. Reading the rounded readout and retyping it would lose precision, but the entry
fields are seeded from the saved site (#53), so editing one coordinate leaves the other at full
precision; the interactive device checks for that seeding are unrun, folded into #42.

## Orloj foundation verification (#4, #5)

Test build: local debug `app-debug.apk` from `feat/4-orloj-foundation` at 905ff8e (APK SHA-256
85191f20c2249d7430613b86bc5acde4547621cf48d82ad12d5add0eae4b2c08), the artifact installed on the
device below.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The single civil hand moves 0.004 degrees per second, so a stalled loop looks identical to a running
one and neither the eye nor a frame diff can tell them apart. Two instrumented checks replace
inspection. The hand angle is measured from `adb exec-out screencap` frames as the principal axis of
the `DialStyle.HAND` pixels (`#F4E5B8` reads back as about `#F1E5BD` through the screenshot
pipeline), and compared with the civil time the device's own clock implies. Repaint cadence is
measured with `atrace -a <pid> gfx view`, counting the wallpaper producer's
`lock`/`unlock`/`dequeueBuffer`/`queueBuffer` events; `dumpsys SurfaceFlinger --latency` is
unpopulated on this API level, so it could not be used.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | 24-hour scale, preview | XII top, XXIV bottom, VI left, XVIII right, with one hand; day and night shading and the zodiac ring drawn |
| 2026-10-03 | 24-hour scale, home | Same scale and hand; measured hand angle within 0.02 degrees of the civil time the device clock implies |
| 2026-10-03 | 24-hour scale, lit lock screen | Same scale and hand as home at the same instant; both lock-screen clocks agreed with the hand |
| 2026-10-03 | repaint cadence, preview | 6 producer buffer acquisitions in 6 s, deltas 0.999-1.000 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, home | 6 in 6 s, deltas 0.998-1.001 s, so 1.000 Hz |
| 2026-10-03 | repaint cadence, lit lock screen | 6 in 6 s, deltas 0.998-1.001 s, so 1.000 Hz |
| 2026-10-03 | lock screen stays live | Over 92 s the measured hand advanced 0.39 degrees against 0.38 implied by wall time, so the lock surface redraws rather than holding a snapshot |
| 2026-10-03 | Zodiac ring toggle | Turning it off removed the twelve hand-coloured sign glyphs on the next visible frame and turning it on restored them; the stored preference followed each change |
| 2026-10-03 | Day and night toggle | Turning it off left the sky and twilight pixel counts at zero over a plain night plate; turning it on restored both |
| 2026-10-03 | hide/show | A non-default choice (zodiac ring off) survived leaving to another app and returning |
| 2026-10-03 | surface recreation | `wm size 1080x2000` and then `wm size reset`: the dial re-centred, the choice still applied, cadence 6 in 6 s, and no renderer warnings |
| 2026-10-03 | reboot | New process, same wallpaper binding, the stored non-default choice still applied, hand within 0.02 degrees, cadence 6 in 6 s |
| 2026-10-03 | saved-site timezone | With the site saved while the phone was on `Europe/Prague`, moving the phone to `Asia/Kolkata` left the hand tracking Prague civil time within 0.01 degrees and 52.49 degrees away from Kolkata |
| 2026-10-03 | representative sites | Prague, Sydney, the equator, and both poles entered manually; the horizon adapted as described below |
| 2026-10-03 | no saved site | After clearing app data, Settings read "No observing location set." and the wallpaper drew only the 24-hour scale and hand, following the phone's zone |

The representative sites were entered by hand, never from the device's own position. With the zodiac
ring off, the horizon took the shape the geometric altitude equation predicts, and the measured day,
twilight, and night regions matched `OrlojProjection.altitudeDeg` at every site. At this revision the
plate was always the Prague north-pole projection, whose dial centre is the south celestial pole at
altitude -latitude: Prague (50.08) put the centre below the horizon with the night region inside an
outer day crescent, and Sydney (-33.87) inverted that picture, the centre in daylight with night as
the outer crescent. The equator put the horizon on a straight line through the hub, and the poles put
it on concentric circles, with night inside day at the north pole and day inside night at the south
pole. The southern inversion is the construction replaced in the next section.

Clearing app data also drops the wallpaper binding, so the wallpaper had to be reapplied by hand
afterwards; the device was left with the Orloj wallpaper applied and a current-location site
restored. `am force-stop` was not used as the process-restart check, because it clears the binding
by platform design instead of restarting the service, and `am kill` does not select this process, so
the reboot row is the process-restart evidence.

The review fixes committed after this pass change documentation, one log string outside the render
path, and tests, so the rendering output observed above is unchanged and the observations stand for
the pushed revision.

No failure was observed, and no unresolved limitation remains from this pass. The cadence check
shows only that a frame is produced once per second; battery and frame-cost qualification remain #6.

## Southern plate and Sun layer (#4, #5)

Test build: local debug `app-debug.apk` from `feat/4-orloj-foundation` at 96f972a (APK SHA-256
a13cd926bab76cc72347dc5b09bfb6c489b5b4395ccdf02fbf8ffdac0a388eec), the artifact installed on the
device below.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The sites were entered by hand through Settings. Sydney, the south pole, and the equator were read
from the home screen; the Sun-layer checks used the system live-wallpaper preview, which shows the
dial unobstructed. The screenshot pipeline applies a colour transform (the hand reads back about
`#F1E5BD`, not `#F4E5B8`), so these checks are structural rather than exact-colour, except the hand
angle, which is a principal-axis measurement of the hand pixels.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | southern nesting | Sydney (-33.87, 151.21) put night at the dial centre, a twilight annulus around it, and day outside; the zodiac ring stayed tangent to both tropics |
| 2026-10-03 | south pole | (0, -90) gave concentric night, twilight, and day annuli, night innermost |
| 2026-10-03 | equator | (0, 0) put the horizon on a straight line through the hub, day above and twilight then night below |
| 2026-10-03 | Sun layer off | With the zodiac ring off, turning **Sun** off removed the sky and twilight fills and the horizon and night contour strokes, leaving only the tropics, the equator, and the outer rim |
| 2026-10-03 | Sun layer on | Restoring it redrew the fills and both contour strokes on the next visible frame |
| 2026-10-03 | repaint cadence, preview | 8 producer frames in 7.0 s, deltas 0.983-1.016 s, so 1.001 Hz |
| 2026-10-03 | hand angle, preview | Measured hand angle 32.022 degrees against 32.029 degrees implied by the device clock, 0.007 degrees apart |

The equator plate was observed both on the home screen and in the preview; the southern plates on the
home screen. After the pass the saved site was restored to the device's current location
(50.1081, 14.4695) and the wallpaper was left applied. No failure was observed. The cadence check
shows only that a frame is produced once per second; battery and frame-cost qualification remain #6.

## Dial caching and render-failure containment (#39)

Test build: local debug `app-debug.apk` from `perf/39-dial-caching-resilience` at 8935527 (APK
SHA-256 8dd818135b89cd8810c78347fe3a61d51aa72585bbebcafcfaeb41bfa58da213), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The caching commit (5272a51) and its review fixes (8935527) change the steady-state render path from
re-sampling plate contours to redrawing pre-built `Path` objects, and route the first visible frame
through the same `runTick()` error containment as the posted ticks. This pass re-runs the cadence,
rendering, nesting, and lifecycle checks of the two preceding sections against that build, so a
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

The equator and Sydney nesting reproduces the preceding section's rows, and the poles' concentric
annuli match its south-pole row; that record was taken before the caching commit, so the cached paths
did not alter the plate geometry. The two poles agree because the southern plate is the radial
inversion of the northern one through the equator circle, so both hemispheres give the same ring
structure. The 2026-09-29 rows record 60 tick strokes (12 long, 48 short) for the earlier three-hand
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

## Unchanged Save provenance and timezone retention (#42)

The changes in `fix/42-unchanged-save-provenance` prevent an untouched **Save coordinates** action
from silently converting a `CURRENT_COARSE` site to `MANUAL` and overwriting its preserved geographic
timezone with the phone's system default timezone. At the tested revision recorded below,
`LocationStore.load(repair = false)` provided a pure read so that checking coordinates never
triggered a repair write. That historical API was replaced in #35: current callers use `load()`,
which never writes preferences. `AstronomicalClocksApplication.onCreate()` owns
`migrateAndRepair()` once per process startup, migrating valid legacy records and repairing
missing or invalid timezones in otherwise usable supported records. Malformed and unsupported
records remain untouched.

Host verification covers these paths across API 26 and 36 via Robolectric:
- `SettingsActivityTimezoneTest`: verifies that after acquiring a location on a site with a distinct
  geographic timezone, tapping Save coordinates untouched preserves `CURRENT_COARSE` and its timezone;
  verifies that cold-opening on a saved `CURRENT_COARSE` site and tapping Save coordinates without
  edits preserves `CURRENT_COARSE` and its timezone; and verifies that edited coordinates still switch
  to `MANUAL` and capture the device timezone.
- `SettingsActivityAcquisitionTest`: verifies refresh-then-save preserves `CURRENT_COARSE` and cancels
  in-flight location acquisition.
- `SettingsActivityTest`: verifies that an unchanged Save produces zero SharedPreferences writes after
  startup repairs an invalid timezone, shows the distinct "Coordinates unchanged; nothing to save."
  feedback, and verifies that a stored `-0.0` coordinate does not take the edited branch.
- `LocationStoreTest`: at the tested revision, verified that `load(repair = false)` did not persist
  repairs. Current pure-read and explicit maintenance coverage lives in `LocationStoreMigrationTest`
  and `LocationStoreCorruptionTest`; `AstronomicalClocksApplicationTest` covers startup ownership.

**Physical-device status.** On 2026-10-03, debug APK
`aba41ceec2fd69cddd899cb10e0fc8f48477e3c5b90b97b3c25f4ff7c2095c8c`
(source revision `02f5acb03788de650ebae7b30f1b7795319b46be`) was installed and exercised on a
connected Android 16 physical device. An existing `CURRENT_COARSE` site retained `(current)` and
`Europe/Prague` after an untouched Save and force-stop/relaunch; the Save visibly showed
"Coordinates unchanged; nothing to save." No new device location was requested. Earlier public
Prague-coordinate checks also covered manual Save, edited manual Save, and relaunch persistence.

## Zodiac compartments and vernal-equinox star (#43)

Test build: local debug `app-debug.apk` from `feat/43-zodiac-compartments` at 2f4660f (APK
SHA-256 460349fbd0c91c78fe83407cfd3c13fc956a3ae17da0a840f57c8fee717ccaaf), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The star is drawn at `eclipticPoint(0.0)`, which is `equatorRadius` from the dial centre, and it
shares that point with the divider between the ARI and PIS compartments. It is 0.04 sky radii across
against a 0.0075 divider stroke, so on this surface the star is about 13.6 px wide where a divider is
2.5 px, but both are `DialStyle.GOLD` and neither a screenshot nor the eye separates them by colour.
The star is therefore measured as the extra gold at one boundary relative to another: a 7 px disc is
centred on the sidereal-implied vernal equinox, which carries the star, and on the autumn equinox
180 degrees away, which carries only a divider. Both are on the equator circle, and the band's gold
rim starts 12.7 px from the ring centreline, so the rim is outside the disc. The expected bearing is
the local sidereal angle, Greenwich apparent sidereal time plus east longitude, computed from the
device clock and the saved site rather than read back from the renderer.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place and the existing wallpaper binding re-rendered without being reapplied |
| 2026-10-03 | compartments, Prague | Twelve sign labels (ARI through PIS) around the ring, each centred in its own compartment, with a gold radial divider on every boundary between them |
| 2026-10-03 | star against a divider, Prague | 72 gold px in the 7 px disc at the vernal equinox against 33 px at the autumn equinox, 180 degrees away on the same circle, so the star is drawn rather than only the divider |
| 2026-10-03 | star bearing, Prague | Gold centroid bearing 340.514 deg against 340.549 deg implied by local sidereal time, 0.035 deg apart |
| 2026-10-03 | compartments, Sydney | Twelve labels each centred in its compartment with a divider on every boundary, at the placement the southern projection gives for -33.86785, 151.20732 |
| 2026-10-03 | star against a divider, Sydney | 71 gold px at the vernal equinox against 34 px at the autumn equinox, so the southern projection draws the star on the same terms |
| 2026-10-03 | star bearing, Sydney | Gold centroid bearing 117.979 deg against 117.994 deg implied by local sidereal time, 0.015 deg apart |
| 2026-10-03 | star follows sidereal time, Sydney | Re-captured 26 min later at the same site: the measured bearing moved from 117.979 to 124.456 deg while local sidereal time advanced from 117.994 to 124.416 deg, and that capture again read 69 px against 35 px at the autumn equinox |

Both sites were entered through the settings app's coordinate fields and Save button, not from the
device's own position, and the wallpaper binding stayed applied throughout: the app was never
force-stopped, so the captures above are of the same running process that the install replaced. The
device was left on the saved Prague coordinates after the pass; manual entry changes the site's
provenance from current location to manual, which only the display label reflects, and Use current
location restores it. This pass re-checked the ring and the star only: the cadence, surface
recreation, lock-screen, and location rows recorded for the preceding revisions were not re-run
against this build. The two compartment rows describe the dividers as they were drawn before the
reorientation recorded in the next subsection.

### Reoriented zodiac dividers (#43)

Test build: local debug `app-debug.apk` from `feat/43-zodiac-compartments` at d231ce8 (APK
SHA-256 2ab46dcaf5dfe8ddb9d5ce29c2a640fc252002b8c4f37c9aaa5cd21a60ecd922), the artifact installed on
the device below, built from a clean tree at that revision.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

The dividers now run along rays from the dial centre rather than along the zodiac ring's own
radius, so a check that assumed the ring radius would no longer describe them. Frames come from
`adb exec-out screencap`. The dial centre is located from the gold hand hub and the scale from the
outer gold rim, both read off the frame rather than assumed. The projected boundary points, the
offset ring centre, and the local sidereal angle all come from Greenwich apparent sidereal time
plus the site's east longitude, computed from the device clock and the saved site, not read back
from the renderer: the 0 degree Aries star placed by that independent geometry lands within 0.3 px
of the drawn star at both sites, which validates the frame before any divider is fitted. Each
stroke's direction is then the principal axis of the `DialStyle.GOLD` pixels that lie inside the
night band within 5 px of its predicted ray; the two gold rim arcs sit 14 px out and are excluded,
because including them pulled the fitted line toward the ring's radius. The tilt is reported both
against the ring's own radius at the boundary and against the dial-centre ray, and the equinox
counts repeat the previous pass's 7 px disc.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-03 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place and the existing wallpaper binding re-rendered without being reapplied |
| 2026-10-03 | dividers point at the pivot, Prague | Twelve gold strokes, one on every boundary and spanning the band: each fitted line passes 0.1-4.1 px from the dial centre and lies 0.0-1.1 deg from the ray from the centre through its boundary point |
| 2026-10-03 | obliquity of the tilt, Prague | Departure from the ring's own radius is 0.6 deg at the 0 deg Cancer boundary and 0.5 deg at 0 deg Capricorn (coincidence, at the fit's resolution), 12.0-12.5 deg at the four boundaries at 60/120/240/300, 20.3-21.0 deg at 30/150/210/330, and 22.9 deg at 0 deg Aries and 24.5 deg at 0 deg Libra, the two equinox boundaries, against the 23.44 deg true obliquity |
| 2026-10-03 | clip to the night band, Prague | Along each stroke's ray the gold run starts and ends within 2 px of the ring's inner and outer rim circles and is continuous across the band; no stroke gold lies beyond them, the further gold found within 20 px of the band belonging to the equator and sky-boundary circles, recognised by its radius |
| 2026-10-03 | compartments stay closed, Prague | The interior of all twelve compartments, 5 deg of ecliptic longitude past each boundary on the ring centreline, reads `DialStyle.NIGHT` |
| 2026-10-03 | star against a divider, Prague | 68 gold px in the 7 px disc at the vernal equinox against 34 px at the autumn equinox 180 degrees away, so the star still adds gold at a boundary that carries a reoriented divider |
| 2026-10-03 | dividers point at the pivot, Sydney | At -33.86785, 151.20732 the same twelve strokes pass 0.1-2.5 px from the centre and 0.0-0.6 deg from their rays |
| 2026-10-03 | obliquity of the tilt, Sydney | 0.5 deg at 0 deg Cancer and 0.3 deg at 0 deg Capricorn, 12.2-12.5 deg at 60/120/240/300, 20.5-20.8 deg at 30/150/210/330, and 24.0 deg at both equinox boundaries, so the southern plate tilts its dividers on the same terms |
| 2026-10-03 | star against a divider, Sydney | 68 gold px at the vernal equinox against 37 px at the autumn equinox |

Both sites were again entered through the settings app's coordinate fields and Save, and the app was
not force-stopped: the install's process restart brought up the new build, and the same running
process produced every capture. The device was left on the saved Prague coordinates, byte-identical
to those recorded before the pass. Only the ring's dividers were re-measured; the sign labels, the
compartments, the star, and the cadence, lifecycle, and location rows from the earlier passes were
not re-run against this build.

## Sun marker (#27)

Test build: local debug `app-debug.apk` from `feat/27-render-sun` at 4ffa962, the review fix on top of
the rebase at 80e1a9f (APK SHA-256
1e165b93b8cb1b07b1744ddfc86d3f81cc5c358aebdc064bfd94a07facd46b47), the artifact installed on the
device below. Subsequent commits on this branch (c0cb0ba and later) are documentation and host-test
changes only, leaving `app/src/main/` untouched and producing the identical APK bytecode and SHA-256.
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
[`DialGeometryFixture.kt`](../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/DialGeometryFixture.kt).
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

## Zodiac hardening (#74)

Test build: local debug `app-debug.apk` from `refactor/74-zodiac-hardening` at faa4d04 (APK
SHA-256 61d95b92a8c440fe3f2bfdd2ccc1f18d1d23f4904d87ef8aea1076d93f6ea264), the artifact installed on
the device below, built from a clean tree at that revision.

The pass below predates this branch's rebase onto `main`, which brought in the Sun rendering (#27):
`faa4d04` and that APK SHA-256 name the pre-rebase build that was actually installed, and the rebase
re-created the commit as `eb8fe7a` without repeating the device checks.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, with no app-locale
override. Firmware build: withheld (embeds the model identifier).

This is the light follow-up spot-check to PR #81. The refactor (discriminant clamp, `try-finally`
canvas restore, and test hardening) changes no visual output, so the device check confirms the
structural output is unchanged from the device-verified #43 build rather than re-running the full
geometry pass. The site was entered through the settings app's coordinate fields as manual Prague
`50.08, 14.47`.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install | `./gradlew qualityGate :app:assembleDebug` and `scripts/verify-apk.sh` passed at the revision above; `adb install -r` replaced the previous build in place |
| 2026-10-04 | compartments, Prague | Twelve sign labels (ARI through PIS) around the ring, each centred in its own compartment, with a gold radial divider on every boundary between them |
| 2026-10-04 | star against a divider, Prague | The vernal-equinox boundary carried about 2.4x the gold of a plain divider in the night band, so the star is drawn rather than only the divider |
| 2026-10-04 | repaint cadence, preview | Producer buffer acquisitions 0.988-1.013 s apart, so 1.000 Hz |
| 2026-10-04 | renderer log | `adb logcat --pid=<pid>` empty over a 31 s steady 1 Hz soak, and no "skipping frame" warning appeared |

The device was left on the saved manual Prague coordinates after the pass.


## Moon marker (#28)

Test build: local debug `app-debug.apk` from `feat/28-render-moon` at 25bda1a (APK SHA-256
`9af9b6e861f1b400a47f96900e524da259f56aaa64eeee31f8e343f59c6d0dad`), the branch tip when the artifact
was assembled, installed with `adb install -r` over the existing binding. The only later commit on
this branch is this report, which leaves `app/src/` untouched and produces the identical APK.
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
[`DialGeometryFixture.kt`](../app/src/test/kotlin/io/github/godaniya/astronomicalclockswallpaper/DialGeometryFixture.kt):
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
most recent process-recreation evidence on this device. Battery and frame cost remain #6; the cadence
check shows only that a frame is produced once per second. The 0° Aries star's own position is
`ZodiacRenderer`'s claim and `DialRendererTest`'s to keep; this pass only checks that the marker is
distinguishable from it and from the Sun. Always On Display remains out of scope per #2. The screenshot
pipeline returned the palette unchanged on these captures, `#D8B66A` reading back as `#D8B66A` and
`#2C3E50` as itself, so the capture cannot adjudicate the exact gold but can and does match the two
Moon tones exactly.


## Geographic timezone and selector (#21, #24)

Automated coverage resolves each site's geographic zone offline, keeps a confirmed zone across
refreshes, recreation, and phone-timezone changes, and exercises the estimate confirmation and the
picker through Robolectric on API 26 and API 36. Those checks do not establish physical-device
behavior; the rows below do, on a reduced pass.

Test build: local debug `app-debug.apk` from `feat/21-manual-timezone` at 376c138 (APK SHA-256
4e76c6e42b8bc4f7757c72f493f1be61b0a76f9e42d283ceca8c5d503f254a52), installed in place with
`adb install -r` over the previous debug build.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install + version | `qualityGate` and `:app:assembleDebug` clean, `verify-apk.sh` passed; installed in place and Settings read `0.1.0-debug` (versionCode 1, minSdk 26, targetSdk 37). The phone was on `Europe/Prague` and the saved site read `50.0800, 14.4200 (manual)` with `Timezone: Europe/Prague` |
| 2026-10-04 | manual site, suggestion | Entered the published Tokyo coordinates `35.6762, 139.6503` by hand; the timezone button updated to **Timezone: Asia/Tokyo** while the phone zone stayed `Europe/Prague`, so the suggestion is geographic and not the phone's zone |
| 2026-10-04 | estimate confirmation | Tapping **Save coordinates** opened a dialog titled **Estimated timezone** reading "Timezone Asia/Tokyo was estimated from the nearest IANA reference point and can be wrong near a border", with **Choose…**, **Cancel**, and **Save**. With the dialog up, the `run-as` record still held the previous site, `{"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"Europe/Prague"}`: the estimate was offered, not written |
| 2026-10-04 | Choose… overrides the estimate | Tapping **Choose…** opened the picker over the dialog and picking `Pacific/Auckland` closed both and saved. The record then read `{"latitude":35.6762,"longitude":139.6503,"source":"MANUAL","zoneId":"Pacific/Auckland"}`, the screen showed `35.6762, 139.6503 (manual)` with `Timezone: Pacific/Auckland`, and a "Location saved." toast appeared, so the picker's choice — not the estimate that prompted it — is what persisted |
| 2026-10-04 | picker pre-selection | Reopening the picker with `Pacific/Auckland` saved opened the list scrolled to it with its radio filled, not at the alphabetically first entry; the entries shown ran `Pacific/Auckland`, `Pacific/Bougainville`, `Pacific/Chatham`, …, `Pacific/Honolulu`, with no `Africa/*` row visible |
| 2026-10-04 | dial civil hour follows the site | With the site saved on `Pacific/Auckland` and the phone moved to `Asia/Kolkata` by `cmd alarm set-timezone`, the home-screen capture at 1791120867 UTC measured the civil hand at **218.6111°**. The site's civil time then was 02:34:27 NZDT, whose expectation is **218.6125°**, a residual of **−0.0014°**; one second later the residual was **−0.0056°**. The phone's own zone would have put the same instant at 19:04:27 IST, an expectation of 106.1125°, which is **112.5°** away from what was drawn |
| 2026-10-04 | site survives a phone-zone change | With the phone on `Asia/Kolkata` (status bar 19:04 IST) the screen still read `35.6762, 139.6503 (manual)` and `Timezone: Pacific/Auckland`, and the record was byte-identical to the one written while the phone was on `Europe/Prague` |
| 2026-10-04 | force-stop and relaunch | `am force-stop` emptied `pidof`, and the record still held the Tokyo site on `Pacific/Auckland`; relaunching Settings showed the same coordinates and `Timezone: Pacific/Auckland` with no dialog. The force-stop dropped the wallpaper binding to the stock `ImageWallpaper`, as #42 and #28 also record, and the wallpaper was reapplied afterwards |
| 2026-10-04 | restore and the confirmation's Save path | Re-entering the original `50.08, 14.42` suggested `Europe/Prague`; the dialog was shown again and the record was still untouched while it was up, and accepting it with **Save** wrote `{"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"Europe/Prague"}`. That both exercises the dialog's Save button on the device and restores the site the pass found |

The dial row is the same claim as the `saved-site timezone` row in (#24) above, re-checked against the
build this branch produces. The probe finds the dial centre in the hub's inner disc, which is painted
at the dial origin, and measures the civil hand as the densest 1-degree cluster of hand-coloured
pixels, because the same colour also paints the twelve zodiac sign glyphs. One pixel of centre error
moves the measured bearing by about 0.16 deg at the hand's tip radius of 355.1 px, and the frame is
quantised to one second of civil time, 0.0042 deg, so the observed residual is inside the probe's own
resolution rather than a claim about the renderer's accuracy. The separation the row rests on is the
112.5 deg between the site's zone and the phone's, which is two orders of magnitude larger than that
resolution.

The confirmation row is what the reduced pass exists to establish: the `run-as` record read was taken
while the dialog was on screen in both cases, and it showed the previous site each time, so the rows
distinguish "offered" from "saved" rather than inferring the gate from the dialog's presence.

Four device settings were changed for this pass and restored afterwards: the phone timezone, moved to
`Asia/Kolkata` for its rows and set back to `Europe/Prague`; `stay_on_while_plugged_in`, set to USB
because the screen kept sleeping mid-sequence and restored to its previous value of 0;
`screen_off_pocket`, set to 0 and restored to 1; and `proximity_sensor`, set to 0 and restored to 1,
because an accidental-touch-protection overlay armed by the proximity sensor was intercepting the
injected input and had to be dismissed and disabled to drive the UI at all. The screen timeout was
read back unchanged at 300000 ms. The device was left with the wallpaper applied to the home screen,
the phone on `Europe/Prague`, and the saved site back on manual Prague `50.08, 14.42`.

**What this pass did not run.** The reboot row is deliberately not run, per the reduction agreed for
this pass. `am force-stop` is not accepted as process-recreation evidence (it is the row above, and it
clearly does not restart the service: it dropped the wallpaper binding instead), so this pass
establishes record persistence across a process restart but not service re-creation, and #27's own
reboot row remains the most recent process-recreation evidence on this device. The current-location
flow's estimate dialog was covered by Robolectric and by this pass's manual path, but no acquisition
was taken during the pass, because acquiring one would have written the device's own position, which
this project does not publish. Always On Display remains out of scope per #2, and battery and frame
cost remain #6. One cosmetic observation is outside this change: `android.R.string.cancel` renders in
the device's language ("Abbrechen") beside the app's own English strings in both dialogs, as the
picker already did before this branch.

### Searchable picker (2026-10-04)

Automated coverage types a query, narrows the adapter, and picks from the narrowed rows under
Robolectric. The rows below re-check that behavior on the physical device against the build this
branch now produces, with every tested site entered by hand from published coordinates.

Test build: local debug `app-debug.apk` from `feat/21-manual-timezone` at 43229f0 (APK SHA-256
5e64ade776b7f2df481a6a5b24a692d66762fa59215672b29ed3d34707a60612), installed in place with
`adb install -r` over the previous debug build.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install + version | `qualityGate` and `:app:assembleDebug` clean, `verify-apk.sh` passed; installed in place and Settings read `0.1.0-debug` (versionCode 1, minSdk 26, targetSdk 37). The phone was on `Europe/Prague` |
| 2026-10-04 | hand-entered site | Entered the published Prague coordinates `50.0800, 14.4200` by hand; the timezone button read **Timezone: Europe/Prague** from the geographic suggestion, and the saved record still held the site the pass found until **Save coordinates** was tapped |
| 2026-10-04 | picker opens on the saved zone | Tapping the timezone button opened **Select timezone** with an empty **Filter timezones** box and the list scrolled to `Europe/Prague` with its radio filled, not to the alphabetically first entry. On the build before the scroll fix the same dialog opened at `Africa/Abidjan` with `Europe/Prague` checked but hundreds of rows below the fold, so `list.setSelection` was added and this row is the check for it |
| 2026-10-04 | typing narrows the list | Typing `new york` in the filter narrowed the hundreds of rows to the single row `America/New_York`; the timezone button behind the dialog still read `Timezone: Europe/Prague`, so a query that only narrows commits nothing |
| 2026-10-04 | a filtered row stores and displays | Tapping the `America/New_York` row closed the dialog and the button read `Timezone: America/New_York`; **Save coordinates** showed a "Location saved." toast, the screen read `50.0800, 14.4200 (manual)` with `Timezone: America/New_York`, and the `run-as` record became `{"latitude":50.08,"longitude":14.42,"source":"MANUAL","zoneId":"America\/New_York"}` — the row the filter offered, and no other |
| 2026-10-04 | a matchless query | Reopening the picker and typing `zzzzzz` emptied the list, showed **No matching timezone**, and left the button reading `Timezone: America/New_York`; the `run-as` record read while the dialog was up was byte-identical to the saved one, so the query committed nothing |
| 2026-10-04 | restore | The record was restored to the one the pass found — a `current` record for the Prague area, coordinates withheld as an acquired fix — and Settings read that record's coordinates with `Timezone: Europe/Prague`. The phone's wallpaper was reapplied to the home screen afterwards (see below) |

The first picker row is the reason this subsection exists alongside the earlier section: the picker
offers hundreds of entries, so opening it on the alphabetically first row would hide the zone
actually in effect several hundred rows down. The scroll fix has no Robolectric assertion — with no
layout pass, `ListView.getSelectedItemPosition()` reports `-1` — so this device row, not the unit
suite, is what establishes it. The filter and the no-match row are what make a hundreds-long list
usable: a query that reaches no listed zone shows **No matching timezone** and cannot commit a zone
the list never offered.

The record was restored by writing it back after `am force-stop` rather than through the UI, because
storing a picked zone rewrites the record's `source` from `current` to `manual`, and the
originally-found `current` record cannot be re-created without acquiring a new fix, which this pass
does not do.

Two device settings were changed for this pass and restored: `screen_off_pocket`, set to 0 so the
screen could not sleep mid-pass and restored to 1; and `stay_on_while_plugged_in`, raised to USB (2)
by `svc power stayon usb` to keep the screen awake and reset to 0, this device's previously recorded
resting value, since the command was issued before the pass read it. `proximity_sensor` read back
unchanged at 1, and the screen timeout at 300000 ms. The phone timezone was never changed, and no
accidental-touch-protection overlay armed during the pass. Because `am force-stop` was used to
reload the restored record, the wallpaper binding dropped to the stock `ImageWallpaper` and was
reapplied to the home screen through the app's **Open wallpaper preview**; the lock screen was left
as found.

**What this subsection did not run.** Only the picker was exercised here; the estimate-confirmation,
dial-hour, and force-stop rows stay as recorded in the section above. The reboot row and
process-recreation evidence are unchanged. No current-location fix was acquired, because doing so
would write the device's own coordinates.

## Virtual time

Debug builds (the `debuggable` flag, so never release builds) accept a broadcast that moves the
wallpaper's clock without touching the phone's real time, so a drift check no longer needs a
30-minute wait. Every engine that is visible with a live surface redraws immediately; a destroyed
engine, a hidden engine, or one whose surface has been destroyed is skipped and catches up on its
next visibility or surface change (the debug trigger logs which guard failed at debug level).

```sh
ACTION=io.github.godaniya.astronomicalclockswallpaper.DEBUG_SET_TIME
adb shell am broadcast -a $ACTION --el offset_minutes 30        # also offset_millis/seconds/hours
adb shell am broadcast -a $ACTION --es instant 2026-06-21T00:00:00Z   # fix an exact instant
adb shell am broadcast -a $ACTION --ez reset true               # back to system time
```

Offsets are added to the real clock, so they keep running; an `instant` freezes it. Always reset
afterwards. `scripts/device-smoke-test.py` reads the screen's power state, wakes the screen, advances
30 minutes, measures the civil hand's advance (7.5° expected on the 24-hour dial), recreates the
surface, checks logcat, resets the clock, sleeps the screen again when it did not start awake, and
exits non-zero on failure. It needs the wallpaper applied and visible on the home screen.

This clock moves the civil hand and the astronomy together from one instant, so it shows that the
marker follows the ephemeris but does not replace the independent ERFA comparison in the Sun and Moon
passes. A smoke run only measures the hand; it does not exercise lifecycle, reboot, or the lock screen.

### Smoke run (2026-10-04)

Test build: local debug `app-debug.apk` from `feat/6-test-acceleration` at ac4e812 (APK SHA-256
8790dc4db3c057e02642fef2809e2d08fcce35321ab821a4fd40dd5598e27d65, reproducing the hash first recorded
at 1f084cb because no Kotlin changed), built from a clean tree at that revision and installed in
place with `adb install -r` over the previous debug build.

Android version: 16 (API 36)
Firmware build: withheld (embeds the model identifier)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | virtual time travel (+30m) | The civil hand advanced 7.526° against 7.500° expected (residual +0.026°); the broadcast took 109 ms |
| 2026-10-04 | surface recreation | From an effective 1080x2408 (no override), `wm size 1080x2000` then reset; the hand was drawn afterwards |
| 2026-10-04 | renderer log | 0 warnings or errors from `AstronomicalClocksWallpaperService` or `DialRenderer` |

The residual sits well inside the harness's ±0.5° tolerance. Five captures one second apart moved
the probe's estimate by 0.002° to 0.046° per second, against the 0.004° the running hand advances in
that second, so the probe's own estimate carries up to about 0.04° of jitter — the order of this
residual. It therefore bounds the probe's precision rather than the renderer's; the check asserts
that the hand advanced, not the probe's absolute accuracy. An earlier version of the probe read
6.56° and 9.11° because a naive mean over all cream-coloured pixels was biased; it now takes the
dominant wedge, whose dominant-bin selection is what leaves the small jitter above.

The restore and log-isolation behavior was exercised on the device, not only in a host stub. A
throwaway copy of the script with an injected mid-run failure left `wm size` at its physical
1080x2408 and reset the clock, and a stale `Invalid instant extra` error planted before a run was
excluded from that run's warning count while remaining in the buffer, so the filter isolates entries
without clearing any other session's evidence. The adb-bound, device-selection, display-restore,
resize-target, and screen-restore defenses were proven in a stubbed-adb harness: a mid-run failure
still issues both restores after the failure, a restore that itself times out is reported without
masking the original error, a two-device list exits non-zero having issued only the listing, an
unknown `--serial` and an `unauthorized` entry are refused, a pre-existing `1080x1200` `wm size`
override is restored verbatim with no reset issued, a device whose effective size is the default
1080x2000 is resized to 1080x1800 rather than issuing a no-op, an initial screen that is off issues
`KEYCODE_SLEEP` after the clock reset while one that is on issues none, and an initial screen state
that cannot be read issues no sleep at all and exits non-zero instead of reporting a clean pass.

One setting the harness still cannot restore. It has no read path for a pre-existing virtual-clock
offset — the debug broadcast only sets an offset or fixes an instant, it never reports the current
one — so the run resets the clock to system time rather than to whatever it found. The screen is now
restored: the harness reads `mWakefulness` before it wakes the screen and sleeps it again when it did
not start `Awake`, so this run began and ended with the screen Dozing. When `mWakefulness` cannot be
read at all it issues no sleep — it will not guess a state the run never found — and reports the
restore incomplete, so a screen it may have woken is never counted as restored. `KEYCODE_SLEEP` is a no-op
when the device is set to stay awake while plugged in, so that remains a best-effort limit;
`screen_off_pocket 0`, set before the run to avoid the accidental-touch overlay, was put back to 1
afterwards, and `stay_on_while_plugged_in` was left at 0. Everything else was restored: `wm size`
returned to the physical 1080x2408 with no override, the `…AstronomicalClocksWallpaperService`
binding survived, and the debug clock was reset. Reboot, lock screen, and marker-position checks were
not run.

## Appearance themes (#31)

Test build: local debug `app-debug.apk` from `feat/31-appearance` at 82817a7 (APK SHA-256
`38b7135b8c854350130175c686e4250a2eeb7a01e5d8dc45f27ed63f7c837144`), installed with `adb install -r`
over the existing binding. The revision and hash describe the branch state when this artifact was
assembled; this report itself left `app/src/` untouched, but later commits on this branch do change it,
so those later commits are not covered by the results below.
`./gradlew qualityGate :app:assembleDebug` passed with 526 unit tests per build variant and no detekt,
ktlint, or Android Lint findings, and `scripts/verify-apk.sh` verified the application ID, SDK levels,
debug flag, permissions, wallpaper declaration, Astronomy Engine notice, and APK Signature Scheme v2.

Same physical device as the earlier passes. Android version: 16 (API 36). Firmware build: withheld
(embeds the model identifier). The saved site remained the manual Prague entry `50.08, 14.42` from the
previous pass, with the Zodiac ring, Sun, and Moon layers at their defaults.

**Method.** The three appearance choices are driven through the Settings radio group, and the wallpaper
result is read from an `adb shell screencap` frame by exact palette match. This device's screenshot
pipeline returns the palette unmodified, as the Moon pass recorded, so each probe compares the rendered
colour to the `DialStyle` literal rather than to a transformed approximation. Night mode is driven with
`cmd uimode night yes|no|auto` rather than waiting for the schedule.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-04 | build + install | `qualityGate` 526 tests per variant, 0 failures, no static-analysis findings; `verify-apk.sh` passed; APK SHA-256 `38b7135b…c837144` installed in place |
| 2026-10-04 | Settings theme, explicit Light and Dark | The window background read `#F7F4EB` under Light and `#111923` under Dark, matching the two style resources |
| 2026-10-04 | Settings theme, System | Night mode forced on read `#111923`; forced off read `#F7F4EB`, so the `values-night` override resolves |
| 2026-10-04 | choice survives recreation | Light was selected, the activity recreated itself, and re-entering Settings still showed Light checked and the light window |
| 2026-10-04 | choice survives process recreation | Dark was selected and the app process was then killed as its own uid (`run-as … kill <pid>`; `am kill` skips a process the wallpaper keeps bound). The framework restarted it under a new pid (2463 → 5889) with the wallpaper binding intact, and Settings reopened showing Dark checked and the dark window `#111923`, with the wallpaper on the dark palette (`#101923`, 1842809 px) |
| 2026-10-04 | light wallpaper palette | One frame held 1841426 px of `#F7F4EB` plate and 312180 px of `#E8E2D2` (the civil scale and the zodiac band share the pale sand tone), with the sign names in `#4E341B` (2730 px) and the night sky region still `#2C3E50` (97675 px) |
| 2026-10-04 | dark wallpaper palette | One frame held 1842957 px of `#101923`, 130838 px of `#152433` (the night region and the zodiac band), and 20071 px of `#D8B66A`, with the sign names in `#F4E5B8` (2714 px) |
| 2026-10-04 | Moon reads on the light band | The unlit limb measured 98 px of `#5A6B7D` at (296.3, 1408.3), with the ivory lit limb and the gold rim bounding the disc against the pale band |
| 2026-10-04 | Moon reads on the dark band | The unlit limb measured 101 px of `#2C3E50` at (295.9, 1407.8) |
| 2026-10-04 | night-mode change while visible | `cmd uimode night yes` repainted the visible wallpaper to `#101923` (1842979 px) and `cmd uimode night no` back to `#F7F4EB` (1841454 px); the wallpaper process's logcat held no warning or error line |
| 2026-10-04 | sign names legible in light mode | A crop of the ring at the Aries–Pisces boundary shows `ARI` and `PIS` as bronze glyphs on the pale band, with the ring outline and the equinox star in the same bronze |
| 2026-10-04 | tick cadence, hand, surface recreation | `scripts/device-smoke-test.py`: the civil hand advanced 7.531° against 7.500° expected (residual +0.031°), the broadcast took 110 ms, `wm size 1080x2000` then reset redrew the dial, and the run counted 0 renderer warnings |

The light rows reflect the palette as reviewed: the zodiac band carries the civil scale's pale sand tone
rather than the dark palette's navy, which is what makes the bronze sign names readable on it, and the
night sky region stays the one deep navy area because there the dark tone is the night. The hand and the
two reference circles cross both the pale plate and that night region, so they carry the page-toned
casing described in [orloj.md](orloj.md#palette-contrast); in the device frames it shows as a pale
outline where they cross the navy and is absent where they cross the plate.

The night-mode row is the device-side counterpart of the surface guard commit 5d6c64f adds to
`ClockEngine.onConfigurationChanged`: a configuration change arriving while the engine is visible must
repaint, and it did, with no surface or renderer warning. The destroyed-surface case itself is covered
on the host by `AstronomicalClocksWallpaperServiceAppearanceTest.nightChangeWithoutSurface`, because the
gap between `onSurfaceDestroyed` and `onSurfaceChanged` is not reproducible from adb.

Device settings changed for this pass and restored afterwards: the screen was held awake
(`svc power stayon usb`) and pocket screen-off disabled (`screen_off_pocket 0`) to keep the
accidental-touch overlay away, both restored (`svc power stayon false`, `screen_off_pocket 1`); night
mode was moved to `yes` and `no` for their rows and returned to `auto`; the appearance choice was moved
through Light, Dark, and System, and through Dark again for the process-recreation row, and left on
System, its setting when the pass began. `wm size` returned
to the physical 1080x2408, `proximity_sensor` was read back at 1, the wallpaper binding survived, and
the device was left with the screen dozing.

**What this pass did not run.** Reboot and the lit lock screen are not re-evidenced here; the earlier
passes stand for those. Always On Display remains out of scope per #2. The screenshot
pipeline returned the palette unchanged on these captures, so the probes match the palette literals
exactly, but a capture still cannot adjudicate glyph antialiasing: the contrast contract in
[orloj.md](orloj.md#palette-contrast) rests on the palette values, not on these frames.

### Appearance review fixes: device pass

Test build: local debug `app-debug.apk` from `feat/31-appearance` at 33807f3 (APK SHA-256
`91cfdf52a4b9b921ca8fb684dbfdcb016c941373670e51e24ca5c7e9757bfbb2`), the branch tip when the artifact
was assembled, installed with `adb install -r` over the existing binding. The suppression-rationale
fix in that revision edits a comment in `app/src/`, which shifts the debug line tables, so the hash
differs from the earlier `38b7135b…` build even though the feature behaviour is unchanged.
`./gradlew qualityGate :app:assembleDebug` passed with 530 unit tests per build variant and no detekt,
ktlint, or Android Lint findings, and `scripts/verify-apk.sh` verified the application ID, SDK levels,
debug flag, permissions, wallpaper declaration, Astronomy Engine notice, and APK Signature Scheme v2.

Same physical device as the earlier passes. Android version: 16 (API 36). Firmware build: withheld
(embeds the model identifier). The device's found site was its coarse current location
(`CURRENT_COARSE`, ≈ 50.126, 14.472, `Europe/Prague`); the southern spot-check below moved it to
manual Sydney and it was restored to manual Prague `50.08, 14.42`, matching the earlier appearance
pass's site.

**Method.** Ring-disabled markers are read from `adb exec-out screencap` frames by exact palette match,
the same unmodified screenshot pipeline the earlier passes recorded. The Moon phase is driven with the
debug virtual clock (`DEBUG_SET_TIME --es instant`) to instants computed offline from mean-lunation
arithmetic (new 2026-10-11T09:13:52Z, first quarter 2026-10-18T18:24:52Z, full 2026-10-26T03:35:53Z),
and the lit fraction is the count of `MOON_ILLUMINATED` pixels over the lit plus `MOON_SHADOW` count in
the dark palette, where both are unique. Reachability is checked at `font_scale 2.0` and
`wm size 1080x1200`.

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-05 | build + install | `qualityGate` 530 tests per variant, 0 failures, no static-analysis findings; `verify-apk.sh` passed; APK SHA-256 `91cfdf52…7bfbb2` installed in place |
| 2026-10-05 | Settings theme, System/Light/Dark | Window background read `#F7F4EB` under Light and `#111923` under Dark; under System, `cmd uimode night yes` read `#111923` and `no` read `#F7F4EB` |
| 2026-10-05 | light wallpaper palette | One frame held 1844309 px of `#F7F4EB` plate, 311974 px of `#E8E2D2` (civil scale and zodiac band), 2644 px of `#4E341B` names, and 108458 px of `#2C3E50` night region |
| 2026-10-05 | dark wallpaper palette | One frame held 1846003 px of `#101923`, 141494 px of `#152433` (night and band), 20015 px of `#D8B66A`, and 2631 px of `#F4E5B8` names |
| 2026-10-05 | night-mode change while visible | `cmd uimode night yes` then `no` repainted the visible wallpaper dark then light, and the wallpaper-process logcat held no warning or error line |
| 2026-10-05 | ring-disabled markers, light | The Sun marker's `#6E4D25` disc carried its `hand` outline `#4E341B` over the twilight and sky region; the Moon (a thin near-new crescent, `#5A6B7D` shadow and `#F7F4EB` lit limb) carried its `hand` ink at the twilight-facing edge, and the `#F7F4EB` casing matched the plate tone, confirmed by crop |
| 2026-10-05 | ring-disabled markers, dark | The Sun marker's `#D8B66A` disc carried its `hand` outline `#F4E5B8` (small clusters at the disc edge); the thin-crescent Moon (`#2C3E50` shadow and `#E8EEF5` lit limb) showed its two-tone boundary antialiased into the night and twilight regions, legible by crop |
| 2026-10-05 | lunar phase, new / quarter / full | Virtual clock to the three instants measured lit fractions 0.000 (lit 0, shadow 161), 0.462 (lit 67, shadow 78), and 1.000 (lit 163, shadow 0), confirming each phase reached |
| 2026-10-05 | lunar phase, southern mirror | At the first-quarter instant with the site on Sydney `-33.87, 151.21`, the lit limb offset flipped sign (+3.6 px, right limb, Prague → −3.0 px, left limb, Sydney); lit fraction 0.551, the same ~half phase |
| 2026-10-05 | reachability, 2× font on a short screen | At `font_scale 2.0` and `wm size 1080x1200`, all three appearance radios sat within the viewport (y ≈ 514/649/784) and each tap applied System, Light, and Dark in turn |
| 2026-10-05 | tick cadence, hand, surface recreation | `scripts/device-smoke-test.py`: hand advanced 7.513° against 7.500° (residual +0.013°), broadcast took 114 ms, `wm size 1080x2000` then reset redrew the dial, 0 renderer warnings |

The lit-fraction rows under-count both Moon regions by their antialiased boundary, as the earlier Moon
pass records, so the phase claim rests on the three distinct fractions (0, ≈½, 1) and the southern
limb mirror rather than on exact values. The southern check is a single first-quarter spot-check, not
the full two-hemisphere pass the Moon record documents.

Device settings changed for this pass and restored: the screen was held awake (`svc power stayon usb`,
`stay_on_while_plugged_in` back to 0) and pocket screen-off disabled (`screen_off_pocket 0`, back to 1)
to keep the accidental-touch overlay away; `font_scale` moved to 2.0 for the reachability row and
restored to its found 1.1; `wm size` returned to the physical 1080x2408 with no override; night mode
moved through `yes`/`no` and back to `auto`; the appearance choice moved through Light, Dark, and
System and was left on System; the saved site moved to manual Sydney for the mirror row and was
restored to manual Prague `50.08, 14.42`. The wallpaper binding survived throughout, and the device was
left with the screen dozing.

**What this pass did not run.** Reboot and the lit lock screen are not re-evidenced here; the earlier
passes stand for those. Always On Display remains out of scope per #2. The southern check is one
first-quarter capture, not the full two-hemisphere phase pass. The screenshot pipeline returned the
palette unchanged, so the probes match the palette literals exactly, but a capture still cannot
adjudicate glyph antialiasing; the marker-outline contrast contract in
[orloj.md](orloj.md#palette-contrast) rests on the palette values, not on these frames.

## Pure-read location storage and startup migration (#35)

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
