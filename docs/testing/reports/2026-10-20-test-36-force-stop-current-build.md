# Force-stop and non-stopping process-death pass on the current build (#36)

Current-build physical-device check of the two observable process-death paths in #36: an
explicit force-stop and a non-stopping process kill. It also records the recovery flow through
**Open wallpaper preview** and the low-memory branch this pass did not exercise.

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`. The branch carries documentation only, so the
application sources are those of `main` at `fb6bab0e329ed5128abc11ab0396f8a547959eca`. APK
SHA-256 `ae2afd88a839aa3f5ac535df11a961196e1ab5313dd3343c7a7ca68dc8ce8693`. The artifact was
installed in place with `adb install -r` over the previous debug build (versionCode 2,
versionName 0.2.0-debug).

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are
withheld per the device-privacy policy. Device locale `de-DE`; device timezone
`Europe/Berlin`. The pass used the published city-centre reference site Prague
`50.08, 14.42`, `Europe/Prague` (manual), so the site zone and the phone zone differ. The
pre-test saved site was a private current-coarse record; it was captured privately, restored
byte-identically, and is withheld. Dial layers Zodiac ring, Sun, and Moon were enabled;
appearance was System; display size, horizontal and vertical position, and brightness were
100%, 50%, 50%, and 100%.

## Observed results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-20 | build and verify | `./gradlew qualityGate :app:assembleDebug` passed (93 actionable tasks). `scripts/verify-apk.sh` accepted the package ID, SDK levels, debug flag, permission set, wallpaper declaration, and bundled Astronomy Engine license, and verified the Android Debug signer. |
| 2026-10-20 | install/update | `adb install -r` succeeded over the previous debug build. The package stayed `stopped=false`, and the package replace rebound the service: process pid `10570`, `dumpsys wallpaper` still listed `AstronomicalClocksWallpaperService`, and the engine reported `mVisible=true`. |
| 2026-10-20 | force-stop | `am force-stop` set `stopped=true` and removed the wallpaper process (`pidof` empty). `dumpsys wallpaper` reported `ImageWallpaper` as the default component and for every listed wallpaper component; `dumpsys activity service` found no service record for the wallpaper service. |
| 2026-10-20 | return home | `KEYCODE_HOME` spawned no process (`pidof` remained empty), `stopped=true` was unchanged, and `ImageWallpaper` stayed bound. |
| 2026-10-20 | Settings relaunch | Starting `SettingsActivity` cleared the stopped state (`stopped=false`) and started process `10787` for the activity. The wallpaper stayed on `ImageWallpaper`; no wallpaper engine was bound until the preview re-application below. |
| 2026-10-20 | state persistence | `dial_settings.xml`, `dial_display.xml`, `appearance_settings.xml`, and `location_permission.xml` were byte-identical to the pre-test captures. `observing_location.xml` still held the pass site until the restore step below. |
| 2026-10-20 | non-stopping process death | Two `run-as ... kill -9` restarts left `stopped=false` throughout: pid `10787` → `11632` (about 1.27 s) and `11632` → `11699` (about 1.28 s). After each restart `dumpsys wallpaper` retained `AstronomicalClocksWallpaperService`, and the engine returned with `mVisible=true` and a created surface. |
| 2026-10-20 | preview re-application | **Open wallpaper preview** opened `LiveWallpaperChange`; choosing **Start- und Sperrbildschirm** (home and lock screens) rebound `AstronomicalClocksWallpaperService` (pid `10787`, visible engine, created surface). Returning home kept the engine visible. |
| 2026-10-20 | dial rendering and advance | A home-screen capture showed the dial. `scripts/device_smoke.py` then measured the static instant at 154.975° and the +30-minute instant at 162.534°, a 7.559° advance against 7.500° expected (residual +0.059°), and exercised a temporary `wm size 1080x2000` surface recreation followed by a verified reset. The renderer warning scan was inconclusive because it found no matching records. |
| 2026-10-20 | restore | The original `observing_location.xml` and all other preference files were restored byte-identically. `wm size` was the physical `1080x2408`, the screen was left dozing with the keyguard not showing, and the wallpaper remained bound to `AstronomicalClocksWallpaperService` (pid `11699`, `stopped=false`). |

The force-stop row and the non-stopping row are different framework paths: the first marks the
package stopped and leaves the stock wallpaper in place, while the second keeps the package
unstopped and lets `WallpaperManagerService` rebind the provider. The current build reproduces
both.

## Limitations

- `REASON_LOW_MEMORY` was not tested. No public adb command produces it: `am kill` reports
  `REASON_USER_REQUESTED`, and a direct `SIGKILL` is not an lmkd kill. Real memory pressure is
  outside the shared-device procedure. The evidence gap is carried by
  [#160](https://github.com/godaniya/astronomical-clocks-wallpaper/issues/160) and is not a
  v0.3 gate.
- Force-stop behavior belongs to the platform and may differ on other OEM wallpaper managers.
  The observed recovery flow is the one this device exposes: launch the app, then confirm
  **Open wallpaper preview**.
- One physical device, one build, one locale, and one UI state were exercised. Reboot, a lit
  lock screen, preview-engine cleanup, and multiple displays were not re-run here.
- The non-stopping row is a `SIGKILL` with `stopped=false`, not an lmkd or low-memory kill.
- `dumpsys` text surfaces and their labels are implementation details of this Android build;
  the observations are attributed to this source revision, APK, and device session.
