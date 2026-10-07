# Final harness physical-device pass (#107)

## Overview

Final-harness physical-device verification of the consolidated host-side ADB testing layer ([#107](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/107)). The shared layer changes how and in what order the device is restored and adds the confirmed wake/sleep gates on both harnesses' setup steps; this standalone report records the pass that #107's verification requires for the final revision.

Harness revision under test: `48751a8d104edd471c1a720ef757acfb643f7d9e` (both harnesses and the shared `scripts/device_layer.py` at that revision). The first hardware attempts of the final revision failed and produced two harness fixes: the display timeout could switch the screen off mid-run (black frames and deferred broadcasts), and the log confirmation was slow and single-shot (about 17-20s per filtered count against 2.5s unfiltered, and it could miss a deferred delivery's write). The fixes are commits `482b381` and `48751a8`; the run below is the clean re-run at `48751a8`. A same-day re-run after the round-5 smoke-confirmation change is recorded in [2026-10-07-refactor-107-device-pass-confirmation.md](2026-10-07-refactor-107-device-pass-confirmation.md).

Test APK: local debug `app-debug.apk` built from `058567a25ee740b8fb2bbcd0517ab64cf3aac84e` (APK SHA-256 `e252d65fbba61b37c13282d001d68a01dfa5f950e848e9db3cf36264286ac5b1`), pulled back from the device and re-hashed immediately before the pass. `git diff 058567a..48751a8 -- app/` is empty, so the app under test is byte-identical to the build recorded in the [round-3 report](2026-10-07-refactor-107-device-layer.md); no install or reinstall was performed.

Target platform: physical device running Android 16 (API 36). Hardware serial number, OEM, model name, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy). Run date: 2026-10-07.

---

## 1. Automated Smoke Verification (`device_smoke.py`)

Run command:
```sh
python3 scripts/device_smoke.py
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | virtual time travel (+30m) | Hand advanced 7.554° against 7.500° expected, residual +0.054°; broadcast took 151ms |
| 2026-10-07 | surface recreation | effective 1080x2408, `wm size 1080x2000` (active as requested) then `wm size reset` (verified); hand drawn afterwards |
| 2026-10-07 | renderer log | Inconclusive: no matching warning records; rendering was not verified |

Result: Smoke test passed with clean exit code 0, including the restored virtual clock, display size, and screen state.

---

## 2. Multi-Phase Device Qualification Verification (`device_qualification.py`)

Run command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 6144
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | visible dial baseline | Dial rendered (Dark palette), hand at 169.243° |
| 2026-10-07 | screen-off / wake navigation | device reported screen off for 4s; hand visible within 1s of wake at 169.269°; rendering while asleep was not measured |
| 2026-10-07 | preview navigation | returned to home and detected the active wallpaper hand at 169.294°; preview-engine cleanup was not inspected |
| 2026-10-07 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 169.299° |
| 2026-10-07 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 169.327°; saved preference values were not inspected |
| 2026-10-07 | time travel (+30m, +12h) | +30m moved hand 7.557° (residual +0.057°); +12h moved 179.832° (residual -0.168°) |
| 2026-10-07 | total PSS sample | 30129 -> 32382 kB over 10s (growth +2253 kB; budget 6144 kB); not battery or CPU evidence |
| 2026-10-07 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |

Result: All 7 configured qualification phases passed with clean exit code 0. Full teardown in the `finally` block verified restored display size, night mode, virtual clock, and screen state.

---

## 3. Scope exercised and not covered

The `AdbDevice` command routing, the confirmed wake/sleep gates, and both harnesses' restore policies ran end-to-end on hardware in this pass; the application sources are unchanged from the recorded round-3 build.

Not covered by this pass:

- Date rollover (the debug virtual clock changes offsets, not the civil date).
- Battery and CPU consumption (the PSS sample is not battery or CPU evidence).
- The lit lock-screen scenario and reboot persistence, which need the full manual pass.
- Rendering while the screen is off (only the screen-off state and the wake behavior were observed).
