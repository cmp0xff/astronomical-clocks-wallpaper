# Appearance themes verification (#31)

Test build: local debug `app-debug.apk` from `feat/31-appearance` at 82817a7 (APK SHA-256
`38b7135b8c854350130175c686e4250a2eeb7a01e5d8dc45f27ed63f7c837144`), installed with `adb install -r`
over the existing binding. The revision and hash describe the branch state when this artifact was
assembled; this report itself left `app/src/` untouched, but later commits on the original `feat/31-appearance`
branch do change it, so those later commits are not covered by the results below.
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
casing described in [orloj.md](../../orloj.md#palette-contrast); in the device frames it shows as a pale
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
[Sun](2026-10-04-feat-27-sun-marker.md) and [Moon](2026-10-04-feat-28-moon-marker.md)
passes retain their own reboot/lock-screen evidence on their respective APKs, without qualifying
these builds. Always On Display remains out of scope per #2. The screenshot
pipeline returned the palette unchanged on these captures, so the probes match the palette literals
exactly, but a capture still cannot adjudicate glyph antialiasing: the contrast contract in
[orloj.md](../../orloj.md#palette-contrast) rests on the palette values, not on these frames.

## Appearance review fixes: device pass

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
(`CURRENT_COARSE`, personal coordinates withheld, `Europe/Prague`); the southern spot-check below moved it to
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
[Sun](2026-10-04-feat-27-sun-marker.md) and [Moon](2026-10-04-feat-28-moon-marker.md)
passes retain their own reboot/lock-screen evidence on their respective APKs, without qualifying
these builds. Always On Display remains out of scope per #2. The southern check is one
first-quarter capture, not the full two-hemisphere phase pass. The screenshot pipeline returned the
palette unchanged, so the probes match the palette literals exactly, but a capture still cannot
adjudicate glyph antialiasing; the marker-outline contrast contract in
[orloj.md](../../orloj.md#palette-contrast) rests on the palette values, not on these frames.

**Editorial note (2026-10-07).** This report was extracted from the original device guide.
Branch/revision references describe the original feature work. This cleanup corrects report dating,
cross-report navigation, and evidence scope where applicable; all historical observation rows,
run dates, measured values, and APK/source attribution are retained. No new measurements were made.
