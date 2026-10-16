# Battery-protocol harness re-verification on device, round 3 (#126, [#132](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/132))

Harness revision under test: `67bd82b3ed5b67642ade757df7c8b3c2c6fdcf51`
(`scripts/battery_protocol.py`, `scripts/battery_report.py` and `scripts/device_layer.py` at that
revision). Android 16 (API 36). The APK **as installed on the device**: SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`, read from the device with
`pm path` → `sha256sum` rather than from a host build artifact, so the digest names the build the
windows actually ran against. It is **byte-identical to the digest both earlier 2026-10-16 passes
recorded**, which is what makes this pass's readings comparable with theirs: nothing was installed and
nothing was built, and no `app/` code has changed. Device vendor, model, serial and firmware build are
withheld per the
[physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy); the
harness's own salted `device_id` for this device is `2ec5a940ef5f`, carried by every record below.
Run date: 2026-10-16.

## Why this pass exists

The review loop closed at `c48698f` and was re-opened five times; the head moved six commits after the
[first pass](2026-10-16-test-126-harness-reverification.md) and three after the
[second](2026-10-16-test-126-harness-reverification-round-2.md). Three of those commits changed
**device-facing** code, so neither earlier pass describes this head:

- **`f78dc17`** — the census block refuses a line that is neither an entry nor a continuation of one.
  A parser that refuses more can refuse a dump this device produces, which is the one failure mode
  worth spending a pass on: a refusal on the deferred drain pass costs the owner an hour.
- **`67bd82b`** — `ensure_visible_home` reads the lock state *before* it touches the device, so a
  locked one stops the phase instead of being unlocked by it. This runs at the start of every visible
  and memory window, so it is on the path of the one window this pass takes.
- **`67bd82b`** — the pilot stops as soon as an emitted window reports restore problems. The short pass
  runs no pilot, so this is not exercised here; it is a stop condition, and the pass records that it
  did not fire rather than that it works.

`9d1425e`, the other commit in that span, changes only `scripts/battery_report.py`'s identity rule and
touches no device-facing code; the report section below covers it.

**Scope: the short harness pass only.** No pilot and no drain comparison. B4, B5 and B6 are not
claimed by this pass, for the reasons under [What this pass does and does not
establish](#what-this-pass-does-and-does-not-establish).

## Method

One bounded invocation per phase, each writing its own record to a fresh state directory and printing
its own evidence. The state file is working data and stays out of git; this report quotes the records
rather than attaching them.

```sh
STATE=build/reports/battery-pass-2026-10-16-final   # created empty for this pass
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> resources
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> hidden  --minutes 10
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> visible --arm A --minutes 15
python3.11 scripts/battery_protocol.py --state "$STATE" --serial <SERIAL> report \
  --black-unavailable "setting a static wallpaper rewrites the home image and this build has no root"
```

No Gradle and no `ANDROID_HOME` are involved: the pass installs nothing and builds nothing. As the
first pass records, the harness **must not** be run with `-I` (`-I` implies `-P`, so `scripts/` is not
prepended to `sys.path` and the run dies at `import device_layer`), and `--state` and `--serial` are
top-level options that appear **before** the subcommand.

The pass started with the device awake and the launcher in front. No setting outside the harness's own
pinned set was changed: `screen_off_pocket` was read as `1` before the pass and `1` after it, and
`svc power stayon` was never issued. The device has no keyguard at all (`locksettings get-disabled` is
`true`), which is what `ensure_visible_home`'s new pre-check reads and what makes it pass where the old
post-check did.

### The arm the pass ran on

The home wallpaper slot is bound to this app's own service, so the pass ran the live arm. Read
directly before any window: `dumpsys wallpaper` reports
`ComponentInfo{io.github.godaniya.astronomicalclockswallpaper.debug/io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService}`,
and both the `visible` arm guard and `hidden`'s live-arm check admitted it. The harness never swaps
the wallpaper it measures, so no swap was needed.

## The parser check against the device's real dumps

Run **before any timed window**. Each dump was captured through `device_layer.run_adb` — the same call
a phase makes, so the bytes are identical to what a phase would parse — into a scratch directory, and
the captured text was then fed to the worktree's parsers from a throwaway script. **12 readings, none
refused; no parser refused a real dump.** No dump carried a single CRLF line ending.

| Parser | Dump | Captured size | Observed |
| --- | --- | --- | --- |
| `parse_battery_output` | `dumpsys battery` | 11 452 B | charge counter 3 600 000 uAh, level 80%, voltage 4037 mV |
| `require_clk_tck` | `getconf CLK_TCK` | — | `100` |
| `wallpaper_pid` | `pidof` | — | `4805` |
| **`parse_wakelock_count`** | `dumpsys power` | **131 759 B** | census `Wake Locks: size=0`, block reconciles, **0** attributed to uid 10026 |
| **`parse_alarm_count`** | `dumpsys alarm` | **318 173 B** | census `176 pending alarms:`, block reconciles to **176** entries, **0** for the app |
| **`parse_job_count`** | `dumpsys jobscheduler` | **2 630 172 B** | census `Registered 562 jobs:`, block reconciles to **562** entries, **0** for the app |
| **`read_keyguard_locked`** | `dumpsys window` | — | **`False`** — the reading the new pre-check refuses a run on when it is `True` or unreadable |
| `read_screen_on` | `dumpsys power` | — | `True` |
| `read_home_in_front` | `dumpsys window` | — | `True` |
| `read_wallpaper_component` | `dumpsys wallpaper` | — | the live service, as above |
| `parse_api_level` | `getprop ro.build.version.sdk` | 3 B | `36` |
| `device_identity` | `device_identity` | — | `2ec5a940ef5f` |

**The three census parsers are the rows in bold, because `f78dc17` made them refuse more.** They are
the ones whose new rule could reject a real dump, and none did: each block reconciles at exactly the
figure its census declares, over the continuation lines these dumps actually print. The block rule is
indentation-based — a continuation is deeper indented than the entry it continues — and the real dumps
hold 176 alarm entries and 562 job entries with their continuations below them, which is what the rule
was written from.

The census figures moved again between passes, as they have every time: **562** jobs where the earlier
passes of today read 566 and 572, **176** pending alarms where they read 167 and 161, and a wake-lock
census of `size=0` where the first pass of today read `size=1`. That is the ordinary reason the parser
reconciles whatever a dump declares against the block beneath it rather than holding a remembered
number.

## Observed results

### `resources` (B2) — seconds

| Date | Check | Observed | Budget | Verdict |
| --- | --- | --- | --- | --- |
| 2026-10-16 | wake locks, every type | 0 | 0 | PASS |
| 2026-10-16 | alarms | 0 | 0 | PASS |
| 2026-10-16 | jobs | 0 | 0 | PASS |
| 2026-10-16 | `batterystats` corroboration | `Total cpu time: u=1m 29s 837ms s=1m 11s 697ms, Fg Service for: 15m 10s 916ms, Total running: 15m 10s 916ms, Total cpu time: u=25s 916ms s=3s 953ms` | — | recorded, not gated |
| 2026-10-16 | wallpaper pid / uid | 4805 / 10026 | — | recorded |

The `resources` phase exited 0 in under a second, and its three counts come from the three censused
dumps reconciled in the table above.

### `hidden --minutes 10` (B3)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-16 | measured span | 606.1 s (a 10-minute window) |
| 2026-10-16 | hidden CPU | **0.0346%** of one core against the 0.1% budget — PASS |
| 2026-10-16 | `screen_on_samples` | 20/20 `False` — the display was off for the whole window, after the pre-window readback confirmed it went dark |
| 2026-10-16 | `on_battery_samples` | 20/20 `True` |
| 2026-10-16 | `power_source` | `simulated` — the device was on USB, so the override was applied and the reset owed |
| 2026-10-16 | `clk_tck` | 100 |
| 2026-10-16 | `restore_problems` | `[]` |

### `visible --arm A --minutes 15` (one arm-A window, not a B4/B5 measurement)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-16 | arm guard | admitted the live service; `wallpaper_component` in the record names it in full |
| 2026-10-16 | keyguard pre-check | read `False` and let the window proceed; the device has no keyguard |
| 2026-10-16 | `median_luma` | 23.81 for the live arm, with the record's own standing note that the channel is recorded and not gated |
| 2026-10-16 | measured span | 911.2 s (a 15-minute window) |
| 2026-10-16 | CPU | 3.2101% of one core |
| 2026-10-16 | `screen_on_samples` | 30/30 `True` |
| 2026-10-16 | `on_battery_samples` | 30/30 `True` |
| 2026-10-16 | `home_in_front_samples` | 30/30 `True` — the through-the-window sampling of the surface in front |
| 2026-10-16 | charge counter | 3 600 000 → 3 600 000 uAh; discharge 0 uAh, rate 0 uA |
| 2026-10-16 | `power_source`, `clk_tck` | `simulated`, 100 |
| 2026-10-16 | `restore_problems` | `[]` |

**The zero discharge is the known frozen-gauge artefact, not a harness defect**, for the reason both
earlier passes give: the device was on USB, so `power_mode_for` chose the simulated override, and
`cmd battery unplug` freezes the charge counter. Every guard the `visible` phase owns still ran and
passed. That is what this window was for; no B4 or B5 value is claimed from it.

**The pilot's new stop condition did not fire, because no pilot was run.** That is recorded as the
absence it is: the short pass takes one window, so `67bd82b`'s pilot change is covered by its host
tests and not by this pass.

## Restoration evidence

Every phase restored every setting it pins, and each restore verifies its own readback. The settings
were captured before the pass and read again after it; all three phases left them identical:

| Setting | Before the pass | After the pass (`resources`, `hidden`, `visible`) |
| --- | --- | --- |
| `system/screen_brightness` | 128 | 128 |
| `system/screen_brightness_mode` | 0 | 0 |
| `system/screen_off_timeout` | 300000 | 300000 |
| `global/airplane_mode_on` | 0 | 0 |
| `global/stay_on_while_plugged_in` | unset (`null`) | unset (`null`) |
| `system/screen_off_pocket` | 1 | 1 (never changed by the harness) |
| display state | awake, launcher in front | awake, launcher in front |
| `dumpsys battery` power sources | USB powered | USB powered |
| home wallpaper component | the live service | unchanged |

The stay-awake setting is the one a phase *deletes* rather than writes; it read back as unset both
times. The two `simulated` records each owed a `cmd battery reset`, and the power sources read back to
`USB powered: true` after both, which is the pre-phase state, so the override was cleared. No record
carried a non-empty `restore_problems` and no `*_aborted` record was written — which is also the
evidence that the pilot stop condition was not needed here, rather than that it works.

## The `report` command

`report` was run against the state file and exits 1, which is the designed behaviour here rather than a
failure. It read all three records, named their shared identity (one revision, one API level, one
`device_id`, one `apk_sha256`), and printed:

```
B4 pairing: NOT MEASURED - the visible windows are not an A/B alternation: recorded arms ['A']
B5 visible CPU (mean): FAIL — 3.2101% against 3.0%
```

Both rows are the harness behaving correctly on a state file that is not an acceptance run. The
**B4 pairing** rule refuses a sequence that is not an A/B alternation rather than pairing windows that
were not taken under the same conditions, and the **B5** row is the live-arm CPU channel, which the
report evaluates from the live windows independently of the pairing. **This pass does not claim B5
from it**: the reading agrees with every recorded pass — 3.4552% on 2026-10-12, 3.7479% on 2026-10-13,
4.3391% in the six-pair run, 3.4348% and 3.2630% in the two earlier passes of today — and all of them
sit above the 3% budget at their own revisions.

The black baseline is `NOT MEASURED (DEVICE-LIMITED)`, declared to the report rather than inferred
from its absence. `B6 PSS growth` is `NOT MEASURED` because no `memory` phase was run, and `B1 hidden
frames` is `NOT MEASURED HERE` because the frame counter is #125 criterion 1, which records its own
producer-trace evidence.

`9d1425e`'s identity-kind rule is on this path: `report` read the three records and printed every
identity line from them — `API levels: 36`, `device_id: 2ec5a940ef5f`, the APK digest,
`power_source: simulated`, `clk_tck: 100` — which is the harness's own state file passing a rule that
now refuses a scalar of the wrong kind.

## What this pass does and does not establish

**It establishes** that the harness at `67bd82b` runs against real hardware and real dumps, and that
its tightened parsers refuse nothing this device produces. Every phase exited 0, no phase wrote an
aborted record, the three censused dumps reconcile at every figure the device printed (562 jobs, 176
alarms, 0 wake locks), the block rule `f78dc17` added accepts every continuation line these dumps
carry, the keyguard pre-check reads the state it needs and passes, the identity-kind rule reads the
harness's own records unchanged, and every restore returned the device to the state it was found in
with nothing reported.

**It does not establish B4, B5 or B6.** A single arm-A window is not an A/B alternation, so the paired
comparison B4 is gated on was not run; the B5 number above is one window's CPU, offered as an
observation that agrees with the recorded passes, not as this pass's B5 verdict; and no `memory` phase
was run, so B6 has no value at all. None of the three would be meaningful from this window in any
case, because the charge counter was frozen at 3 600 000 uAh throughout under the simulated override.

**It does not exercise the pilot.** No pilot was run, so `67bd82b`'s stop condition on a window whose
restore failed is covered by its host tests alone.

**It does not supersede the earlier passes' readings.** The APK digest is byte-identical to both of
them, so this is the same short pass run at a later revision, and its counts sit beside theirs rather
than replacing them. The censuses moved for the ordinary reason recorded above.

One device, one build, one screen state, one site. Nothing here transfers to another device, another
window length, the lock screen, or a hidden engine. The device was left awake, on the home screen, with
the wallpaper binding it started with and every setting it was found with.
