# Shared ADB device layer refactoring verification (#107)

## Overview

Verification of the consolidated host-side ADB testing layer ([#107](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/107)), re-anchored on `upstream/main` (`40281d6b6d54bbc365f41e3bda2c6ce774fe88a2`), where the Python-policy prerequisites merged via #103 and #109. The refactoring extracts `scripts/device_layer.py` providing an object-oriented `AdbDevice` abstraction and pure dial geometry and frame decoders, renames `scripts/device_smoke_test.py` to `scripts/device_smoke.py`, and preserves the distinct restore policies between smoke and qualification testing.

Test build: local debug `app-debug.apk` built from this branch at revision `058567a25ee740b8fb2bbcd0517ab64cf3aac84e` (APK SHA-256 `e252d65fbba61b37c13282d001d68a01dfa5f950e848e9db3cf36264286ac5b1`), verified with `./scripts/verify-apk.sh` and installed over the existing wallpaper binding via `adb install -r`.

Target platform: physical device running Android 16 (API 36). Hardware serial number, OEM, model name, and firmware build identifier are withheld in accordance with the project's [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy).

**Review-round note (2026-10-07).** The `AdbDevice` routing, the remaining smoke-harness path corrections, and the confirmed-wake/sleep gates on both harnesses' setup steps were completed in the pull-request review round as a host-only refactor of `scripts/`: the recorded physical-device runs above predate it, the application sources and the APK recorded above are unchanged, and the refactor was covered by the updated host unit suite (107 tests: 41 smoke, 66 qualification), Ruff, and ty at that revision; the final harnesses' own physical-device pass is recorded in [2026-10-07-refactor-107-final-device-pass.md](2026-10-07-refactor-107-final-device-pass.md).

---

## 1. Automated Smoke Verification (`device_smoke.py`)

Run command:
```sh
python3 scripts/device_smoke.py
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | virtual time travel (+30m) | The civil hand advanced 7.709° against 7.500° expected (residual +0.209° <= ±0.5° tolerance); broadcast confirmed by service log |
| 2026-10-07 | surface recreation | Tested override via 1080x2000; override verified active, dial rendered at 135.290°, and display restored to physical size |
| 2026-10-07 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |
| 2026-10-07 | device state restore | Display size restored to physical size; virtual clock reset to system UTC; screen state restored |

Result: Smoke test passed with clean exit code 0.

---

## 2. Multi-Phase Device Qualification Verification (`device_qualification.py`)

Run command:
```sh
python3 scripts/device_qualification.py --max-pss-growth-kb 6144
```

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-07 | visible dial baseline | Dial rendered (Dark palette), hand at 127.581° |
| 2026-10-07 | screen-off / wake navigation | Device reported screen off for 4s; hand visible within 1s of wake at 127.610°; rendering while asleep was not measured |
| 2026-10-07 | preview navigation | Returned to home and detected the active wallpaper hand at 127.632°; preview-engine cleanup was not inspected |
| 2026-10-07 | surface recreation | Override to 1080x2000 and verified restore to physical size redrew dial; hand at 127.635° |
| 2026-10-07 | process rebind | New PID observed within 10 polls at 0.5s intervals; hand visible at 127.657°; saved preference values were not inspected |
| 2026-10-07 | time travel (+30m, +12h) | +30m moved hand 7.647° (residual +0.147°); +12h moved 180.261° (residual +0.261°) |
| 2026-10-07 | total PSS sample | 29256 -> 34106 kB over 10s (growth +4850 kB; budget 6144 kB); not battery or CPU evidence |
| 2026-10-07 | renderer log scan | Inconclusive: no matching warning records; rendering was not verified |

Result: All 7 configured qualification phases passed with clean exit code 0. Full teardown in the `finally` block verified restored display size, night mode, virtual clock, and screen state.

---

## 3. Host-Side Unit Tests & Quality Gates

1. **Python Unit Tests**:
   - `python3 -I -m unittest discover -s scripts -p 'test_*.py' -v`
   - Passed 99 unit tests (40 in `test_device_smoke.py`, 59 in `test_device_qualification.py`) at the time of this run; the review-round note above records the current suite.
2. **Static Analysis & Formatting**:
   - `uvx --from ruff==0.16.10 ruff check scripts` (zero errors or warnings).
   - `uvx --from ruff==0.16.10 ruff format --check scripts` (all scripts formatted).
   - `uvx --from ty==0.0.84 ty check scripts` (zero type errors or warnings).
3. **Android Quality Gate & Packaging**:
   - `ANDROID_HOME=$HOME/Library/Android/sdk ./gradlew qualityGate :app:assembleDebug` (BUILD SUCCESSFUL).
   - `./scripts/verify-apk.sh app/build/outputs/apk/debug/app-debug.apk` (PASSED).
