# Battery budget B4 and B5: the A/B/A/B drain comparison (#126)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The `app/src/main` tree it
compiles is byte-identical to the tree at `5efae15`, so that revision names the application sources
under test. The harness revision the records carry is
`a9472458328ba4cd73d60b421531e40fca3b426f`; the three earlier records in the same state file carry
`76a1b8ceec51c402c6a4082f015264fc41a9dd35`, and the two differ only in documentation and in the
harness's own report aggregation, never in how a window is measured. The artifact was installed in
place with `adb install -r`.

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are withheld
per the device-privacy policy. The saved site carried through the pass is the manual published
city-centre reference `50.08, 14.42`, `Europe/Prague`.

## What was measured and why

#126 criterion 3 asks for "a recorded A/B/A/B run against stock and against a black static wallpaper,
with the result stated against B4 and B5, including a failed result if it fails". This is that run
against the stock baseline, and it reports **two** results the issue asks to be stated plainly: B4
cannot be measured on this device at all, and B5 fails.

## Method

`/tmp`-side orchestration is not part of the deliverable, so it is described rather than cited: the
run alternates a 15-minute visible home-screen window on the live wallpaper (arm A) with a
15-minute window on the OEM `ImageWallpaper` (arm B), four times, which is the minimum the issue
mandates. The swap is performed outside the harness — `am force-stop` for arm B and the live
picker for arm A — and confirmed by the harness before each window: it reads the bound component
and refuses the window when the device does not match the arm it is about to be recorded against.
A refused window writes no record, so a mis-swapped wallpaper cannot enter this table.

Each window pins brightness and auto-brightness, raises the screen-off timeout, holds the display
awake, enables airplane mode and simulates discharge with `cmd battery unplug`, restoring all of it
in a `finally` with every value read back. All eight records carry an empty `restore_problems` list,
and the run exited zero.

## Observed results

| Date | Window | Observed |
| --- | --- | --- |
| 2026-10-12 | pair 1, arm A (live) | 905 s; charge counter 4500000 → 4500000 uAh; discharge 0 uAh; CPU 3.2753% of one core |
| 2026-10-12 | pair 1, arm B (stock) | 906 s; discharge 0 uAh; no process to read CPU from, as expected for a static wallpaper |
| 2026-10-12 | pair 2, arm A | 904 s; discharge 0 uAh; CPU 3.4593% |
| 2026-10-12 | pair 2, arm B | 906 s; discharge 0 uAh |
| 2026-10-12 | pair 3, arm A | 904 s; discharge 0 uAh; CPU 3.5344% |
| 2026-10-12 | pair 3, arm B | 905 s; discharge 0 uAh |
| 2026-10-12 | pair 4, arm A | 904 s; discharge 0 uAh; CPU 3.5519% |
| 2026-10-12 | pair 4, arm B | 905 s; discharge 0 uAh |
| 2026-10-12 | **B4, relative increase against stock** | **NOT MEASURED.** Every window on both arms recorded 0 uAh, so the baseline mean discharge rate is not positive and no relative increase exists to compute. The hardware charge counter reports exactly `4500000` uAh in all eight windows: `cmd battery unplug` makes the device *report* itself as running on battery while it stays on USB, but the gauge itself does not move. This is a dead channel, not a device that draws nothing, and the B4 threshold cannot be evaluated against it |
| 2026-10-12 | **B5, visible CPU** | **FAIL.** The mean over the four live windows is **3.4552% of one core** against the committed 3% budget. The worst window was 3.5519%. A failed result is what the issue asks to be recorded when the budget is not met, and this is one |
| 2026-10-12 | arm verification | Each of the eight records names the component it was measured against — the live service for arm A, the static `ImageWallpaper` for arm B — so no window was recorded against the wrong arm |
| 2026-10-12 | B4/B5 pairing | The four pairs are an exact A/B alternation; the report refuses to pair a sequence that is not, so these pairs are the ones the statistics use |
| 2026-10-12 | **the black static baseline** | **NOT RUN.** Setting a static wallpaper writes the device's home wallpaper image, and on this production build with no root that file cannot be read, backed up, or restored — `adb root` is refused (`adbd cannot run as root in production builds`) and `/data/system/users/0` is not listable from `shell`. Running it would therefore leave the device's wallpaper permanently changed, which the device policy does not allow, and it would also destroy the pristine stock baseline this table is measured against |
| 2026-10-12 | device state restored | All eight records carry `restore_problems: []`; the device's `airplane_mode_on`, `screen_off_timeout`, `screen_brightness`, `screen_brightness_mode` and `stay_on_while_plugged_in` were read back at their pre-run values, and the live wallpaper was rebound on the home screen |

## What this does not establish

**The B5 failure is a real result, not a measurement artefact, but it is a result about this device
and this build.** The live windows' CPU readings drift upward across the run — 3.2753%, 3.4593%,
3.5344%, 3.5519% — monotonically, and the cause is not established here; the pilot's four
consecutive windows did not show that drift, so it is not simply time-in-run. Whatever the cause,
every window is above the budget on its own, not only their mean, so the verdict does not rest on
the drift.

Four alternations is the minimum the budget mandates and the weakest pairing the t interval is
computed for. A longer run would narrow the interval; it would not move the mean below the budget
by itself.

**No claim is made about drain.** B4's absence is not evidence that the wallpaper is power-hungry
or that it is frugal: no discharge signal exists on this device to say either way. The budget's
"visible discharge ≤ 5% relative increase" remains unevaluated, and the README and CONTRIBUTING
"battery efficiency" claim rests on B2, B3, B5 and B6 alone until a device can be run on its own
battery with telemetry that survives the unplug.

**The black-static baseline is unrun**, for the device reason in the table. On this particular
device the omission costs less than it would elsewhere: the OEM stock wallpaper is itself a dark
starfield whose median luminance sits at the black baseline's, so the comparison the black baseline
exists to make — dark dial against bright image — would not have been the comparison measured here
anyway. That is a property of this device, not a substitute for the run.

The rows bound one device, one build, one screen state, and one site. Nothing here transfers to
another device without re-running the pilot's noise floor, which the budget requires anyway.

**Superseded in part.** The B4 row above is superseded by [the 2026-10-13 pass with the device
genuinely discharging](2026-10-13-test-126-drain-comparison-on-battery.md), which measured the same
comparison where this one could not: B4 is no longer "not measured", it is a measured failure. The
measurements in this report stand as recorded and are not amended; the verdict that changed is B4's.
