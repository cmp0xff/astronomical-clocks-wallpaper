# Battery budget B2, B3 and B6 on the device (#126)

Test build: local debug `app-debug.apk` built from this branch's tree with
`./gradlew qualityGate :app:assembleDebug`, APK SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`. The `app/src/main` tree it
compiles is byte-identical to the tree at `5efae15` (`git diff 5efae15 76a1b8c -- app/src/main` is
empty), so that revision names the application sources under test regardless of where the branch's
own base later moves; the harness revision that produced these records is
`76a1b8ceec51c402c6a4082f015264fc41a9dd35`, which is what each record carries. The artifact was
installed in place with `adb install -r`.

Physical device running Android 16 (API 36). Vendor, model, serial, and firmware build are withheld
per the device-privacy policy. The saved site carried through the pass is the manual published
city-centre reference `50.08, 14.42`, `Europe/Prague`.

## What was measured and why

Three criteria from [`docs/battery-budget.md`](../../battery-budget.md) whose evidence does not depend
on the discharge channel:

- **B2** — the partial wake locks and pending alarms the app holds. The budget is zero of each, and
  the anchor is Android vitals, which reports partial wake lock use as excessive at 2 or more hours
  in a 24-hour period; a wallpaper has no background work to justify any.
- **B3** — the hidden-state CPU over a ten-minute screen-off window. The budget is 0.1% of one core.
- **B6** — the total-PSS growth over a thirty-minute visible window. The budget is the 6144 kB the
  existing qualification harness runs at.

## Method

Each phase is one bounded `scripts/battery_protocol.py` invocation that writes a single JSON record
and prints its own evidence. Wake locks are counted from `dumpsys power` lines that name
`PARTIAL_WAKE_LOCK` and the app's own UID, read from `/proc/<pid>` rather than assumed; alarms are
counted from `dumpsys alarm` lines naming the package. A UID that cannot be resolved is an error,
not a zero.

The B3 window sleeps the display, then samples `dumpsys power`'s display state every 30 seconds for
the whole ten minutes and *requires* every sample to read off, so the window is a screen-off window
by measurement rather than by an endpoint check. CPU comes from `/proc/<pid>/stat` deltas, counted
from the closing parenthesis of the comm field.

Brightness, auto-brightness, the screen-off timeout and airplane mode are captured before a phase
and restored in a `finally`; the restore is read back and any mismatch is recorded and makes the
command exit non-zero. Every record here carries an empty `restore_problems` list, and the device's
`screen_off_timeout` and `airplane_mode_on` were read back after the pass.

## Observed results

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-12 | B2, wake locks and alarms | `dumpsys power` filtered by `uid=10026` (the wallpaper process's own UID, read from `/proc/20859`) matched 0 `PARTIAL_WAKE_LOCK` lines; `dumpsys alarm` matched 0 `Alarm{` lines naming the package. Both against a budget of zero |
| 2026-10-12 | B2, corroboration | `dumpsys batterystats` for the package reported `Total cpu time: u=1m 30s 768ms s=1m 1s 655ms`, `Fg Service for: 7m 2s 103ms`, `Total running: 13m 45s 367ms`. The issue classes this as a software model and corroborating evidence only, and it is recorded as such |
| 2026-10-12 | B3, hidden CPU | 604.17 s of screen-off window with the CPU counters read at both ends: **0.0397% of one core**, against the 0.1% budget. The display state was sampled 20 times inside the window and read off for all 20 |
| 2026-10-12 | B6, memory growth | 30.0 min of visible window: total PSS 50037 kB → 51670 kB, growth **+1633 kB** against the 6144 kB budget |
| 2026-10-12 | restore | Every record carries `restore_problems: []`. The device's `screen_off_timeout` was read back at its pre-pass 300000 ms and `airplane_mode_on` at 0 after the pass, and the battery simulation was cleared by the phases' own `finally` |

## What this does not establish

The B3 row is one ten-minute window on one device, taken with the app's own home-screen engine
hidden by the display going off. It is not a duty cycle, and it does not cover a hidden engine on a
*lit* display, nor preview instances. The frames-rendered half of the hidden-state question is not
measured here at all: it is #125 criterion 1 and is recorded in
[its own report](https://github.com/godaniya/astronomical-clocks-wallpaper/blob/d29e1003c7dcd859f800ae20ad084ab444fbf5e6/docs/testing/reports/2026-10-12-test-125-hidden-frames-and-reboot.md).

The B6 row is a two-sample bracket. It bounds the total-PSS growth across the window; it is not an
allocation profile, and a leak that starts and finishes inside the window, or one whose growth is
still within the budget at thirty minutes, would not be seen. The PSS budget itself is the existing
harness's, not one this protocol derived.

The B2 row is a single-instant census and not a duration. A wake lock acquired and released between
the two `dumpsys` calls would not appear, so the row is "none is held", which is the budget's
wording, rather than "none was ever held". Android vitals' own 2-hour-in-24 measurement is the
metric that bounds the latter, and this pass does not reproduce it.

This report deliberately makes no claim about B1, B4 or B5: B1 is #125's, and B4 and B5 are in the
drain report, which also records the channel that could not be measured on this device.
