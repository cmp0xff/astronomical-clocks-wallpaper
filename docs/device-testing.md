# Physical-device testing

Procedures and acceptance guidelines for verifying the live wallpaper on a physical Android device.
This page is deliberately device-agnostic about identity: the device is referred to only as
"the physical device", and its model, OEM, and serial number are omitted from public surfaces per
the project's device-privacy policy. Android version is non-identifying and is recorded in reports; the
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
afterwards. Run `python3 scripts/device_smoke.py [--serial <device>]` with the wallpaper
applied and unobstructed on home. Before mutation it requires readable display and screen state
and a confirmed unlocked keyguard. It confirms debug-clock resets and the +30m broadcast, checks
the expected 7.5° civil-hand advance, and verifies a temporary size override and its phase restore.
The `finally` cleanup restores the original override and screen state and rechecks the keyguard.
A failed restore remains a failure. A pre-existing virtual-clock offset cannot be read or restored;
the harness returns it to system time.

Renderer warnings fail the run. A logcat/ADB collection error is a failed check with a nonzero exit
and a failed report row; a successfully collected empty scan is inconclusive. Both outcomes retain
the other measured rows and cleanup result. An empty scan establishes no successful rendering.
Record device runs as standalone reports in `docs/testing/reports/` with source and APK attribution.
Run host checks with `python3 -I -m unittest discover -s scripts -p 'test_*.py'`.

This clock moves the civil hand and the astronomy together from one instant, so it shows that the
marker follows the ephemeris but does not replace the independent ERFA comparison in the Sun and Moon
passes. A smoke run only measures the hand; it does not exercise lifecycle, reboot, or the lock screen.

## Qualification harness

Run `python3 scripts/device_qualification.py --max-pss-growth-kb <agreed-limit> [--serial <device>]`
with the wallpaper applied and the keyguard already unlocked. Agree the PSS limit before the run.
It checks the baseline hand, screen-off/wake navigation (sampling the wallpaper engine's reported
visibility and CPU ticks via `/proc/<pid>/stat` while the display is off, and detecting the hand after wake), preview navigation, a temporary surface
resize, non-stopping SIGKILL/rebind, +30m/+12h hand advances, midnight date rollover, and a short total-PSS sample.
Each dependent phase requires its own confirmed clock prerequisite. Final cleanup restores and
verifies the original override, screen state, night mode, and unlocked keyguard; the virtual clock
is reset to system time. An unreadable renderer log fails; an empty successful scan is inconclusive.

SIGKILL does not exercise `lmkd` or prove low-memory recovery. Full lifecycle transitions,
screen-off background CPU inactivity, and midnight date rollover are qualified on physical hardware
in [`2026-10-09-test-6-final-qualification.md`](testing/reports/2026-10-09-test-6-final-qualification.md),
fulfilling and closing #6. Historical observations retain their original APK/source attribution in
the reports directory; future runs require new dated reports.

## Standard acceptance test matrix

The expected behavior describes the implementation contract; diagnostics describe how to investigate
it. Historical observations apply only to each report's recorded source revision, APK, sites, instants,
and measurement method. They do not qualify the current build. No device checks were run for this
documentation update. The pending column names gaps in the recorded evidence; a later device pass
must also re-check the applicable expectations against its own identified build.

| Category | Expected behavior | Diagnostics | Recorded historical evidence | Pending qualification |
| :--- | :--- | :--- | :--- | :--- |
| **Lifecycle & Surfaces** | Draw while visible; cancel ticks while hidden or without a surface; resume after surface recreation. Persist settings across process recreation. Force-stop requires manual re-application. | `dumpsys wallpaper`, `pidof`, visibility/surface callbacks, producer trace while hidden | [Feasibility](testing/reports/2026-09-28-feat-2-device-feasibility.md) records initial transitions; [dial caching](testing/reports/2026-10-03-perf-39-dial-caching.md) records 0 producer frames while another app was focused, surface recreation, and reboot; [force-stop investigation](testing/reports/2026-10-06-docs-36-force-stop-lifecycle.md) records rebind after non-stopping `SIGKILL` and manual recovery; [final qualification](testing/reports/2026-10-09-test-6-final-qualification.md) records complete multi-phase qualification on hardware, qualifying and closing #6. | Actual low-memory recovery under memory pressure is unrun; `SIGKILL` does not establish it. Standard lifecycle, home/preview navigation, surface recreation, and process rebind are qualified in #6. |
| **Cadence & Ticking** | Schedule visible ticks at second boundaries. Assess presentation timing, thread activity, and allocation behavior separately. | Producer buffer trace; presentation/frame timing trace; thread-lifetime trace; allocation profiler. Renderer logs diagnose reported failures only. | [Orloj foundation](testing/reports/2026-10-03-feat-4-orloj-foundation.md) and [dial caching](testing/reports/2026-10-03-perf-39-dial-caching.md) record short producer windows near 1 Hz and log observations; [final qualification](testing/reports/2026-10-09-test-6-final-qualification.md) records screen-off CPU dormancy via `/proc/<pid>/stat` (0.0% CPU) and clean wake resumption. | Dropped-frame checks, thread-churn checks, and allocation profiling are unrun. Standard tick scheduling and background dormancy are qualified in #6. |
| **Virtual Time Travel** | Debug offsets and fixed instants drive the civil hand and astronomy from one instant; a +30-minute virtual offset advances the hand 7.5° when the site UTC offset is unchanged, without changing the device clock or timezone. | `scripts/device_smoke.py`, `DEBUG_SET_TIME`, measured hand angle | [Virtual-time smoke](testing/reports/2026-10-04-feat-6-virtual-time-smoke.md) records +7.526° against +7.500°; [final qualification](testing/reports/2026-10-09-test-6-final-qualification.md) records +30m, +12h, and 20s midnight date rollover (residual −0.005°) across 180° with clean exit 0. | Fixed-instant marker checks and timezone-transition checks have dedicated coverage in #27, #28, and #21. Standard virtual time travel and midnight rollover are qualified in #6. |
| **Location & Permissions** | Explicit location actions request coarse fixes; denial leaves manual entry usable. Non-promptable denial offers recovery. Persist sites; `load()` is a pure read with maintenance at startup. | Permission flags, acquisition logs, `observing_location.xml`, file mtime, restart/recreation | [Location slice](testing/reports/2026-09-28-feat-3-location-slice.md) records manual/acquired paths with revision-specific limits; [permission recovery](testing/reports/2026-10-05-fix-3-permission-recovery.md) records denial/retry/recreation; [pure-read storage](testing/reports/2026-10-05-refactor-35-pure-location-store.md) records four seeded startup states and unchanged valid records. | Interrupted and unrelated permission callbacks remain host-tested only. Storage pass used process killing rather than reboot; broader startup states and current-build device re-checks are unrun. |
| **Site Timezone & Selection** | The selected site's geographic timezone drives civil time independently of the phone timezone. Unchanged Save preserves source/zone; picker selects and filters zones. | Phone timezone changes, preference snapshots, civil-hand probe, picker UI | [Site timezone](testing/reports/2026-09-30-feat-24-site-timezone.md) records distinct historical stages; [unchanged Save](testing/reports/2026-10-03-fix-42-unchanged-save.md) records retained `CURRENT_COARSE`/`Europe/Prague`; [geographic timezone/picker](testing/reports/2026-10-04-feat-21-geographic-timezone.md) records dial-hour and picker checks on its builds. | Unchanged-Save device pass did not establish retention of a site zone different from the phone zone; that distinction has host coverage. Current-build rollover, DST, picker, and persistence qualification is unrun. |
| **Dial Astronomy & Geometry** | The 24-hour civil hand follows the observing site's civil timezone (phone zone if no site). The Sun marker approximates local apparent solar time; Sun/Moon positions use the same instant and hemisphere projection. Moon limb/terminator reflect phase and hemisphere. | Screencap rim fit and marker/hand probes against independent ERFA expectations; projection/phase fixtures | [Sun marker](testing/reports/2026-10-04-feat-27-sun-marker.md) bounds its recorded captures to about 1 px; [Moon marker](testing/reports/2026-10-04-feat-28-moon-marker.md) bounds its captures to about half a pixel, with terminator residuals +0.25 px and −0.02 px. [Southern plate](testing/reports/2026-10-03-feat-4-southern-plate.md), [dial caching](testing/reports/2026-10-03-perf-39-dial-caching.md), and [zodiac compartments](testing/reports/2026-10-03-feat-43-zodiac-compartments.md) record specific site/projection checks. These are measurement limits, not general accuracy guarantees. | Current-build geometry and phase qualification is unrun. Southern plate's historical `(0, -90)` “south pole” row is inconsistent with latitude/longitude order and remains unverified; the separate caching report records explicit `(90, 0)` and `(-90, 0)` checks. |
| **Themes & Contrast** | Light/Dark choices use their palettes; System follows night mode and repaints when visible with a surface. Markers remain legible across plate regions. | `cmd uimode night yes/no`, screencap palette/crop probes, palette contrast calculations | [Appearance](testing/reports/2026-10-04-feat-31-appearance.md) records palettes, visible repaints, ring-disabled marker crops, phase spot-checks, and reachability on two identified APKs. | Destroyed-surface configuration changes are host-tested only. Reboot and lit lock screen were not re-run in the appearance passes; current-build theme/contrast qualification is unrun. Captures cannot adjudicate glyph antialiasing. |

## Historical verification reports

Discover dated reports in [`docs/testing/reports/`](testing/reports/). Each report records its
original source/APK attribution and distinguishes observations from unrun acceptance checks.
The filename date is the first recorded verification date. Branch names, revisions, and statements
about follow-up commits describe the original feature work, not this documentation branch or the
current build. Historical reports may receive documented editorial/privacy corrections while
preserving measurements and attribution; new measurements belong in new reports.
The matrix above links existing baseline evidence; new reports are linked from their PR and
found through the directory listing rather than appended to this living guide.

## Display controls acceptance (#5)

This pass has been run. Its combined acceptance results are recorded in
[`2026-10-08-feat-5-display-controls.md`](testing/reports/2026-10-08-feat-5-display-controls.md),
with its source revision, APK SHA-256 and device-privacy limitations; coverage it
could not exercise - the lit lock screen, landscape, and a genuinely panned or
cropped surface - is tracked in #122. The steps below remain the procedure a later
pass repeats on its own build: record that pass's results in a new dated report
under `docs/testing/reports/`, with source revision, Android/API and APK SHA-256,
and preserve device/location privacy. Do not close #5/v0.2 or create a
release/tag from host results alone.

1. In preview, home and lit lock, exercise Size 50/100/115%, both positions at
   0/50/100%, and Brightness 80/100%. Check all four corners and recover using
   **Reset display**. Check complete rim/strokes and proportional ticks/labels/
   Sun/Moon/equinox marker in dark and light appearance.
2. Confirm wallpaper-wide dimming, unchanged Settings and system screen brightness,
   readable information and unchanged geometry. Verify Reset preserves site,
   timezone, layer grouping and appearance; controls remain enabled without a site.
3. Confirm changes on the next visible tick with independent preview/active engines.
   Change orientation and recreate surfaces, including reported asymmetric insets,
   scrolling/cropped launcher surfaces and offset changes. Check safe bounds and
   logged skips if usable geometry is unavailable.
4. Verify persistence after activity/process recreation and reboot. Exercise a remote
   site, site-zone DST transition, phone-zone changes, site changes and no-site civil
   clock fallback. Use virtual time for equinox rotation and moving sky checks.
5. Run the existing smoke/qualification harnesses on the candidate. Device captures
   now request fresh debug `DialLayout` reports without changing virtual time;
   screen-space centre, radius and brightness drive the rim/hand probes. Multiple
   visible reports, missing/unreadable diagnostics, dimension mismatches or geometry
   outside the capture fail collection rather than falling back to centered guesses.

Debug layout reports appear on layout changes and virtual-clock triggers. An explicit
request is `adb shell am broadcast -a io.github.godaniya.astronomicalclockswallpaper.DEBUG_SET_TIME --es diagnostics <unique-token>`.
Only visible engines with surfaces respond. Reports contain no site coordinates;
release builds register no debug receiver. Unreported launcher crops or OEM wallpaper
dimming need visual/device evidence and must be recorded as limitations; the
launcher-crop case the #5 pass could not reproduce is tracked in #122.

Lifecycle, accuracy, and battery behavior are qualified in [`2026-10-09-test-6-final-qualification.md`](testing/reports/2026-10-09-test-6-final-qualification.md), fulfilling Issue #6 for Milestone v0.3. Signed personal release packaging remains under Milestone v0.3.
