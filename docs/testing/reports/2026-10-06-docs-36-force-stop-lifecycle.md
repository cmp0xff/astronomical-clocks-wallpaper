# Force-stop vs process-recreation lifecycle verification (#36)

Investigation of wallpaper provider rebind behavior after process termination, addressing #36 and
providing foundational evidence for physical-device lifecycle qualification (#6).

Test build: local debug `app-debug.apk` from `docs/36-force-stop-lifecycle`, whose commits change
only this document, so the application sources under test are those of `main` at
`ab883937c5b8a201e2ab5697a34ce641692af0a7` (the branch's base; APK SHA-256
`a6379b408df1a834d2b1135a9aeaafeaa9bc280a64ef8b8b654044526d3097c4`, reproduced by rebuilding that
revision from a clean tree), installed in place with `adb install -r` over the previous debug build.

Same physical device. Android version: 16 (API 36). Device locale `de-DE`, device timezone
`Europe/Prague`. Firmware build: withheld (embeds the model identifier).

## Framework mechanism and architectural resolution

Android distinguishes force-stop, which marks a package stopped, from non-stopping process death;
the framework may further handle exits differently based on their reported reason:

1. **Non-stopping process termination (on-device `SIGKILL` simulation):**
   This check sent `SIGKILL` with `run-as <pkg> kill -9 <pid>` while leaving the package
   `stopped=false`; it did not create memory pressure or test `lmkd` victim selection. With the wallpaper
   still selected, `WallpaperManagerService` receives `onServiceDisconnected` and attempts to rebind the
   provider. On this device, that binding restarted `AstronomicalClocksWallpaperService`; it received
   `onBind`, attached its engine, reconstructed surface state from persisted preferences, and resumed 1 Hz
   frame ticking. This demonstrates the observed non-stopping process-death path, not an actual low-memory
   kill under memory pressure.

2. **Force-stop (`am force-stop` or Settings -> Apps -> Force stop):**
   `ActivityManagerService.forceStopPackage()` marks the package stopped (`stopped=true`). Android does not
   start a stopped package for background work; a user-initiated app launch clears that state. On this device,
   force-stop removed the live wallpaper binding and the system displayed its static wallpaper
   (`ImageWallpaper`). Returning to the home screen did not rebind the provider.

3. **Resolution:**
   This is an **accepted platform limitation** inherent to Android's stopped-package security model. Live
   wallpaper re-application without user interaction requires `android.permission.SET_WALLPAPER_COMPONENT`, a
   `signature|privileged` system permission unavailable to ordinary third-party applications. An app has no
   supported way to clear its own force-stop state or restart itself in the background.
   The application already satisfies the safety requirements: the saved observing site and all dial layer
   settings survive force-stop intact in private storage, launching `SettingsActivity` immediately clears the
   stopped state (`stopped=false`), and tapping **Open wallpaper preview** allows the user to re-apply the
   wallpaper in a single guided flow.

## Physical-device observations

The baseline started with the neutral Prague site `50.08, 14.42` (`Europe/Prague`, `MANUAL`), dial layers
enabled (Moon, Zodiac ring, Sun), and the live wallpaper active on home and lock screens (`mWakefulness=Dozing`).

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-06 | non-stopping `SIGKILL` simulation (not an LMK run) | Process `21302` was killed via `run-as ... kill -9 21302` without stopping the package (`stopped=false`). `dumpsys wallpaper` maintained `AstronomicalClocksWallpaperService`; the framework rebound it and the process returned with PID `21794` (~200 ms latency). Logcat recorded `onBind`, engine attach, surface creation, and `Wallpaper has updated the surface`, resuming frame rendering. `observing_location.xml` and `dial_settings.xml` were intact |
| 2026-10-06 | force-stop | `am force-stop` killed PID `21794` and set `stopped=true`. `dumpsys wallpaper` immediately dropped the provider and bound `ComponentInfo{com.android.systemui/com.android.systemui.wallpapers.ImageWallpaper}` across all display contexts. Returning to the home screen spawned no process (`pidof` empty); the framework stayed on `ImageWallpaper` |
| 2026-10-06 | relaunch and state resilience | Launching `SettingsActivity` cleared `stopped=true` to `stopped=false`. `shared_prefs/observing_location.xml` was byte-identical to baseline (`50.08, 14.42`, `MANUAL`, `Europe/Prague`), and dial layers remained intact |
| 2026-10-06 | preview re-application | Tapping **Open wallpaper preview** (`open_preview`) opened `LiveWallpaperChange`; selecting **Start- und Sperrbildschirm** (Home and lock screens) rebound `AstronomicalClocksWallpaperService` with PID `21987`. Returning home resumed the active dial from wall time |

**Limitations.** The physical-device check used `run-as ... kill -9` to exercise non-stopping process
termination; it did not create memory pressure or verify an actual kernel OOM / `lmkd` kill. The result
documents the observed rebind after `SIGKILL` with `stopped=false`, not equivalence with every low-memory
kill scenario. Android 16's [`WallpaperManagerService`](https://github.com/aosp-mirror/platform_frameworks_base/blob/android16-release/services/core/java/com/android/server/wallpaper/WallpaperManagerService.java#L1227-L1262)
handles exits reported as `REASON_LOW_MEMORY` separately: it delays rebind attempts and can revert to the
built-in wallpaper after repeated low-memory exits. The `SIGKILL` run does not exercise that branch, so
verification under actual memory pressure remains outstanding. Reboot and battery drain are not
re-evidenced here.

Device settings changed and restored: an accidental-touch protection setting was temporarily changed to
allow preview UI navigation, then restored. The device was left with
`AstronomicalClocksWallpaperService` bound to the home and lock screens (PID `21987`), saved site Prague
`50.08, 14.42` intact, and the screen dozing.
