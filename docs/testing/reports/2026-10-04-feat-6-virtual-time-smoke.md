# Virtual time travel and smoke harness verification (#6)

## Smoke run (2026-10-04)

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

The harness is now `scripts/device_smoke_test.py`. The historical warning count above is withdrawn
as clean-log evidence: a completed empty scan is inconclusive, and these runs were not repeated
with the hardened collection and restoration checks. Original APK/source attribution is retained.
