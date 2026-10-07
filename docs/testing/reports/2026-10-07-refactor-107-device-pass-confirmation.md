# Device-pass confirmation after the smoke confirmation consolidation (#107)

## Overview

Re-run of both final harnesses for [#107](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/107) after the round-5 review routed the smoke time-travel confirmation through the shared retry-based helper (commit `b40b0b42bdb09139b9896a6e283fd3580db3159c`). This report keeps the recorded physical-device pass current with the harness revision; the main pass remains [2026-10-07-refactor-107-final-device-pass.md](2026-10-07-refactor-107-final-device-pass.md).

Harness revision under test: `b40b0b42bdb09139b9896a6e283fd3580db3159c`. `scripts/device_layer.py` and `scripts/device_qualification.py` are byte-identical between the first pass (`48751a8d104edd471c1a720ef757acfb643f7d9e`) and this revision (`git diff 48751a8..b40b0b4 -- scripts/device_layer.py scripts/device_qualification.py` is empty); the change under this re-run is the smoke harness's use of `send_debug_clock_broadcast()`.

Test APK: the unchanged local debug `app-debug.apk` built from `058567a25ee740b8fb2bbcd0517ab64cf3aac84e` (APK SHA-256 `e252d65fbba61b37c13282d001d68a01dfa5f950e848e9db3cf36264286ac5b1`), pulled back from the device and re-hashed immediately before this run. `git diff 058567a..b40b0b4 -- app/` is empty; no install or reinstall was performed.

Target platform: physical device running Android 16 (API 36). Hardware serial number, OEM, model name, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy). Run date: 2026-10-07.

---

## 1. Automated Smoke Verification (`device_smoke.py`)

Run command:
```sh
python3 scripts/device_smoke.py
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | virtual time travel (+30m) | Hand advanced 7.519° against 7.500° expected, residual +0.019°; broadcast confirmed by the service log |
| 2026-10-07 | surface recreation | effective 1080x2408, `wm size 1080x2000` (active as requested) then `wm size reset` (verified); hand drawn afterwards |
| 2026-10-07 | renderer log | Inconclusive: no matching warning records; rendering was not verified |

Result: Smoke test passed with clean exit code 0, now through the shared confirmation helper, including the restored virtual clock, display size, and screen state.

---

## 2. Multi-Phase Device Qualification Verification (`device_qualification.py`)

Run command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 6144
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | visible dial baseline | Dial rendered (Dark palette), hand at 172.444° |
| 2026-10-07 | screen-off / wake navigation | device reported screen off for 4s; hand visible within 1s of wake at 172.465°; rendering while asleep was not measured |
| 2026-10-07 | preview navigation | returned to home and detected the active wallpaper hand at 172.490°; preview-engine cleanup was not inspected |
| 2026-10-07 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 172.504° |
| 2026-10-07 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 172.518°; saved preference values were not inspected |
| 2026-10-07 | time travel (+30m, +12h) | +30m moved hand 7.594° (residual +0.094°); +12h moved 179.790° (residual -0.210°) |
| 2026-10-07 | total PSS sample | 30273 -> 32516 kB over 10s (growth +2243 kB; budget 6144 kB); not battery or CPU evidence |
| 2026-10-07 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |

Result: All 7 configured qualification phases passed with clean exit code 0. Full teardown in the `finally` block verified restored display size, night mode, virtual clock, and screen state. This harness and the shared layer are unchanged from the first pass; the re-run confirms they still pass alongside the smoke change.

---

## 3. Not covered

Identical to the first pass: date rollover, battery and CPU consumption (the PSS sample is not battery or CPU evidence), the lit lock-screen scenario, reboot persistence, and rendering while the screen is off. This re-run adds no new scenarios.
