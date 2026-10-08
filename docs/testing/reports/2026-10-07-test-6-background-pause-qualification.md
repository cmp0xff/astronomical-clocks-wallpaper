# Screen-off visibility and wake qualification (#6)

## Overview

Physical-device qualification run for [#6](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/6) observing reported screen-off visibility and wake behavior. The first acceptance criterion of #6 remains unverified:
> *"Instrument or otherwise demonstrate zero rendering while hidden and while the screen is off; hidden engine instances do not keep recurring calculation/render work alive."*

Prior qualification reports noted *"rendering while asleep was not measured"* during Phase 1. This run instruments Phase 1 in `scripts/device_qualification.py` via `read_wallpaper_visible()` to query `dumpsys activity service`, observing that Android's WallpaperManager reports the engine hidden (`mVisible=false`) during sleep, followed by confirmed screen wake and hand detection after wake. This visibility sample does not measure frames or recurring render/tick work.

Tested source and harness revision: `21976c3e42e9d1f082ab5b435992e6d4e3f53763`, as identified by the original PR #111 metadata (branch `test/6-background-pause-qualification`, based on `91f2348b64e56570c9c7f698a9c2bc52c3dbfecb`).

Test APK: local debug `app-debug.apk` at that tested source revision (APK SHA-256 `ac69905fe32b91c3c87bc04f892a0025cd86a389b366707aa5be5783b67a50a5`), verified with `scripts/verify-apk.sh` and confirmed on-device via `sha256sum`.

Target platform: physical device running Android 16 (API 36), locale `de-DE`, timezone `Europe/Prague`. Hardware serial number, OEM, model name, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy). Run date: 2026-10-07.

---

## Editorial correction (2026-10-08)

The original inference that `mVisible=false` proved halted rendering or ticks is withdrawn.
No producer trace, frame counter, or tick trace was collected, so zero rendering while
hidden or screen-off remains unverified. The original recorded output below is preserved
verbatim, including its unsupported "rendering halted" wording; that wording is not evidence
of halted rendering. Dates, measurements, exit status, and APK hash remain unchanged.
The recorded base SHA above is incorrect: the tested commit's actual parent is
`91f2348f07c997ec2eff02aeb763fc2be7b85fb0`, verified from its Git commit object.
The original output also claims a continuous 4s screen-off duration and hand detection
within 1s of wake. The harness sampled screen state after its sleep interval and detected
the hand after wake; it measured neither continuous screen-off duration nor wake latency.
This correction adds no new hardware verification.

## 1. Automated Smoke Verification (`device_smoke.py`)

Run command:
```sh
python3 scripts/device_smoke.py
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | virtual time travel (+30m) | Hand advanced 7.536° against 7.500° expected, residual +0.036°; broadcast confirmed by the service log |
| 2026-10-07 | surface recreation | effective 1080x2408, `wm size 1080x2000` (active as requested) then `wm size reset` (verified); hand drawn afterwards |
| 2026-10-07 | renderer log | Inconclusive: no matching warning records; rendering was not verified |

Result: Smoke test passed with clean exit code 0.

---

## 2. Multi-Phase Device Qualification Verification (`device_qualification.py`)

Run command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 6144
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | visible dial baseline | Dial rendered (Dark palette), hand at 178.127° |
| 2026-10-07 | screen-off / wake navigation | device reported screen off for 4s; wallpaper reported hidden (mVisible=false, rendering halted); hand visible within 1s of wake at 178.156° |
| 2026-10-07 | preview navigation | returned to home and detected the active wallpaper hand at 178.182°; preview-engine cleanup was not inspected |
| 2026-10-07 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 178.198° |
| 2026-10-07 | process rebind | new PID observed within 10 polls at 0.5s intervals; hand visible at 178.212°; saved preference values were not inspected |
| 2026-10-07 | time travel (+30m, +12h) | +30m moved hand 7.536° (residual +0.036°); +12h moved 179.762° (residual -0.238°) |
| 2026-10-07 | total PSS sample | 29319 -> 31589 kB over 10s (growth +2270 kB; budget 6144 kB); not battery or CPU evidence |
| 2026-10-07 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |

Result: All 7 configured qualification phases passed with clean exit code 0. Teardown in `finally` confirmed restored display size (1080x2408), night mode, virtual clock, and screen state.

---

## 3. Not covered

Zero hidden/screen-off rendering and recurring render/tick inactivity require producer/frame/tick evidence and remain unverified. Date rollover, battery and CPU consumption benchmarks (the PSS sample is not battery or CPU evidence), the lit lock-screen scenario, and cold reboot persistence remain unverified.
