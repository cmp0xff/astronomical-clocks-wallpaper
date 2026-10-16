# Battery-protocol harness re-verification on device, round 2 (#126, [#132](https://github.com/godaniya/astronomical-clocks-wallpaper/pull/132))

Harness revision under test: `b7a694c3e2d79b4dc30c721540358f67994e3880`
(`scripts/battery_protocol.py`, `scripts/battery_report.py` and `scripts/device_layer.py` at that
revision). Android 16 (API 36). The APK **as installed on the device**: SHA-256
`3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d`, read from the device with
`pm path` → `sha256sum` rather than from a host build artifact, so the digest names the build the
windows actually ran against. It is **byte-identical to the digest the
[first 2026-10-16 pass](2026-10-16-test-126-harness-reverification.md) recorded**, which is what makes
that pass's readings comparable: this pass installed nothing and built nothing, and no `app/` code
changed between the two revisions. Device vendor, model, serial and firmware build are withheld per
the [physical-device privacy policy](../../../CONTRIBUTING.md#physical-device-testing-and-privacy);
the harness's own salted `device_id` for this device is `2ec5a940ef5f`, carried by every record below.
Run date: 2026-10-16.

## Why this pass exists

The [first 2026-10-16 pass](2026-10-16-test-126-harness-reverification.md) verified the harness at
`c48698f`, and the review loop was closed there. It was not closed for the rest of the day: two more
rounds came back with findings, and the second of them raised two **device-facing** findings in this
PR's own `scripts/battery_protocol.py`:

- **A census line that did not have to end after its count.** `JOBSCHEDULER_CENSUS_PATTERN` was
  anchored at the start of the line only, so a malformed line such as `Registered 0 jobs: CORRUPT`
  matched and its leading zero was read as the census. A count read from a line the probe never
  actually read is, for a zero, a B2 pass.
- **A setting whose "unset" reading could not be told from an unreadable one.** `read_int_setting`
  answers `None` both for the device's unset text and for text it cannot parse, and the stay-awake
  capture stored that `None` as "the setting was never set". A settings service that did not answer
  therefore had the phase overwrite a value the device did hold, and the restore then *deleted* the
  key and reported a clean restore of a device it left changed.

Both fixes landed in `b7a694c`, which means the first pass no longer describes this head — the same
rule that made that pass necessary in the first place. **Every phase this pass exercises is a phase
whose parser or restore path changed after the last recorded device evidence.**

Both fixes are strict tightenings: they add refusals that a device answering exactly as this one does
can never reach. That is an argument, not a proof, and the cost of being wrong is that the deferred
drain pass refuses on a dump this device actually produces — which is precisely the case the parser
check below exists to rule out.

**Scope: the short harness pass only.** No pilot and no drain comparison. B4, B5 and B6 are not
claimed by this pass, for the reasons under [What this pass does and does not
establish](#what-this-pass-does-and-does-not-establish).

## Method

One bounded invocation per phase, each writing its own record to a fresh state directory and printing
its own evidence. The state file is working data and stays out of git; this report quotes the records
rather than attaching them.

```sh
STATE=build/reports/battery-pass-2026-10-16-round2   # created empty for this pass
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

The pass started with the device awake and the launcher in front, which is the state `visible` needs;
the `hidden` phase took the display down and confirmed it went dark before sampling, and the harness
left the display in the state it found it. No setting outside the harness's own pinned set was
changed: `screen_off_pocket` was read as `1` before the pass and `1` after it, and
`svc power stayon` was never issued.

### The arm the pass ran on

The home wallpaper slot is bound to this app's own service, so the pass ran the live arm. Read
directly before any window: `dumpsys wallpaper` reports
`ComponentInfo{io.github.godaniya.astronomicalclockswallpaper.debug/io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService}`,
and both the `visible` arm guard and `hidden`'s live-arm check admitted it. The harness never swaps
the wallpaper it measures, so no swap was needed. The device has no keyguard
(`locksettings get-disabled` is `true`), so no lock surface could shadow the home screen.

## The parser check against the device's real dumps

This is the part of the pass that verifies the two fixes, and it was run **before any timed window**.
Each dump was captured through `device_layer.run_adb` — the same call a phase makes, so the bytes are
identical to what a phase would parse — into a scratch directory, and the captured text was then fed
to the worktree's parsers from a throwaway script. **15 readings, none refused; no parser refused a
real dump.** No dump carried a single CRLF line ending.

| Parser | Dump | Captured size | Observed |
| --- | --- | --- | --- |
| `parse_battery_output` | `dumpsys battery` | 11 223 B | charge counter 3 600 000 uAh, level 80%, voltage 4038 mV |
| `require_clk_tck` | `getconf CLK_TCK` | — | `100` |
| `parse_wakelock_count` | `dumpsys power` | 129 656 B | census `Wake Locks: size=0`, block reconciles, **0** attributed to uid 10026 |
| `parse_alarm_count` | `dumpsys alarm` | 305 003 B | census `167 pending alarms:`, block reconciles to **167** entries, **0** for the app |
| **`parse_job_count`** | `dumpsys jobscheduler` | **2 742 495 B** | census `Registered 566 jobs:`, block reconciles to **566** entries, **0** for the app |
| **`read_unsettable_int_setting`** | `settings get` | — | brightness 128, brightness mode 0, timeout 300000, airplane mode 0, **`stay_on_while_plugged_in` → `None` (the device's `null`)** |
| `read_wallpaper_component` | `dumpsys wallpaper` | — | the live service, as above |
| `read_screen_on` | `dumpsys power` | — | `True` |
| `read_home_in_front` | `dumpsys window` | — | `True` |
| `parse_api_level` | `getprop ro.build.version.sdk` | 3 B | `36` |
| `device_identity` | `device_identity` | — | `2ec5a940ef5f` |
| `read_process_uid` | `stat -c %u /proc/4805` | — | `10026` |

**The two tightened readers are the rows in bold, and each is the case the fix could have broken.**

- The jobscheduler census line on this device is `Registered 566 jobs:` and it ends **immediately
  after the colon** — there is no trailing text of any kind, so the new `[ \t]*$` anchor accepts the
  real line. The parser's block reconciliation passed, which requires the lines below the census to
  hold exactly the declared 566; a whole-dump `JOB ` scan finds the same 566. The count moved again
  between moments (521 in the recorded pass, 572 in the first pass of today, 566 now), which is the
  reason the parser reconciles whatever the dump declares rather than holding a remembered figure.
- The five settings reads all returned something the reader admits: four decimal integers and the
  device's literal `null` for `stay_on_while_plugged_in`. That last one is the reading the fix exists
  to preserve — this device genuinely has the setting unset, so the harness must still capture it as
  "unset" rather than refuse the phase. The raw text of the readback was confirmed as exactly
  `null` + newline, with no other bytes.

The alarm census is the other row worth reading against the first pass: the device declared **167**
pending alarms this pass where it declared 161 this morning, and the parser's reconciliation passed
at 167 — which is the accounting the branch introduced, and why a whole-dump scan is not what decides
the count. That scan finds **168** `Alarm{` headers here, one more than the census, because this
device's `Past-due non-wakeup alarms: 1` section repeats the entry shape four hundred lines below the
pending-alarm census; the block boundary is what keeps it out.

## Observed results

### `resources` (B2) — seconds

| Date | Check | Observed | Budget | Verdict |
| --- | --- | --- | --- | --- |
| 2026-10-16 | wake locks, every type | 0 | 0 | PASS |
| 2026-10-16 | alarms | 0 | 0 | PASS |
| 2026-10-16 | jobs | 0 | 0 | PASS |
| 2026-10-16 | `batterystats` corroboration | `Total cpu time: u=1m 26s 609ms s=1m 15s 425ms, Fg Service for: 15m 11s 463ms, Total running: 15m 11s 463ms, Total cpu time: u=27s 973ms s=3s 500ms` | — | recorded, not gated |
| 2026-10-16 | wallpaper pid / uid | 4805 / 10026 | — | recorded |

The `resources` phase exited 0 in under a second, and its three counts come from the three censused
dumps whose reconciliation is tabulated above.

### `hidden --minutes 10` (B3)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-16 | measured span | 605.9 s (a 10-minute window) |
| 2026-10-16 | hidden CPU | **0.0314%** of one core against the 0.1% budget — PASS |
| 2026-10-16 | `screen_on_samples` | 20/20 `False` — the display was off for the whole window, after the pre-window readback confirmed it went dark |
| 2026-10-16 | `on_battery_samples` | 20/20 `True` |
| 2026-10-16 | `power_source` | `simulated` — the device was on USB, so the override was applied and the reset owed |
| 2026-10-16 | `clk_tck` | 100 |
| 2026-10-16 | `restore_problems` | `[]` |

### `visible --arm A --minutes 15` (one arm-A window, not a B4/B5 measurement)

| Date | Check | Observed |
| --- | --- | --- |
| 2026-10-16 | arm guard | admitted the live service; `wallpaper_component` in the record names it in full |
| 2026-10-16 | `median_luma` | 23.81 for the live arm, with the record's own standing note that the channel is recorded and not gated |
| 2026-10-16 | measured span | 910.8 s (a 15-minute window) |
| 2026-10-16 | CPU | 3.2630% of one core |
| 2026-10-16 | `screen_on_samples` | 30/30 `True` |
| 2026-10-16 | `on_battery_samples` | 30/30 `True` |
| 2026-10-16 | `home_in_front_samples` | 30/30 `True` — the through-the-window sampling of the surface in front, working on hardware |
| 2026-10-16 | charge counter | 3 600 000 → 3 600 000 uAh; discharge 0 uAh, rate 0 uA |
| 2026-10-16 | `power_source`, `clk_tck` | `simulated`, 100 |
| 2026-10-16 | `restore_problems` | `[]` |

**The zero discharge is the known frozen-gauge artefact, not a harness defect**, for the reason the
first pass gives: the device was on USB, so `power_mode_for` chose the simulated override, and
`cmd battery unplug` freezes the charge counter. Every guard the `visible` phase owns still ran and
passed — the arm guard admitted the component, and the display, the battery state and the surface in
front were sampled 30 times each and were correct every time. That is what this window was for; no B4
or B5 value is claimed from it.

## Restoration evidence

Every phase restored every setting it pins, and each restore verifies its own readback. The settings
were captured before the pass and read again after it; all three phases left them identical:

| Setting | Before the pass | After the pass (`resources`, `hidden`, `visible`) |
| --- | --- | --- |
| `system/screen_brightness` | 128 | 128 |
| `system/screen_brightness_mode` | 0 | 0 |
| `system/screen_off_timeout` | 300000 | 300000 |
| `global/airplane_mode_on` | 0 | 0 |
| **`global/stay_on_while_plugged_in`** | **unset (`null`)** | **unset (`null`)** |
| `system/screen_off_pocket` | 1 | 1 (never changed by the harness) |
| display state | awake, launcher in front | awake, launcher in front |
| `dumpsys battery` power sources | USB powered | USB powered |
| home wallpaper component | the live service | unchanged |

**The stay-awake row is the one this pass was for.** It is the setting the phase *deletes* rather than
writes, and it is unset on this device — so the pass exercises the exact path the second fix changed:
capture the device's `null` as "unset", pin the setting to `7` for the window, delete the key
afterwards, and verify the delete by reading `null` back. It read back as unset both times, and no
record carried a non-empty `restore_problems`. The two `simulated` records each owed a
`cmd battery reset`; the power sources read back to `USB powered: true` after both, which is the
pre-phase state, so the override was cleared. No `*_aborted` record was written.
`screen_off_pocket` is listed because the device-pass recipe calls for disabling it during long
unattended sequences and restoring it afterwards; it was **not** touched here, and it reads `1` before
and after, which is the evidence that it was not.

## The `report` command's refusal

`report` was run against the state file and refused it, which is the designed behaviour here rather
than a failure. It read all three records, named their shared identity (one revision, one API level,
one `device_id`, one `apk_sha256`), and printed:

```
B4 pairing: NOT MEASURED - the visible windows are not an A/B alternation: recorded arms ['A']
B5 visible CPU (mean): FAIL — 3.2630% against 3.0%
```

It exits 1. Both rows are the harness behaving correctly on a state file that is not an acceptance
run:

- The **B4 pairing** rule refuses a sequence that is not an A/B alternation rather than pairing
  windows that were not taken under the same conditions. One arm-A window is exactly that case, and
  the message names what is missing. The `report` realizes this refusal as a `NOT MEASURED` verdict
  row rather than as a top-level `ERROR:` line, which is the same rule with a different presentation.
- The **B5** row is the live-arm CPU channel, which the report evaluates from the live windows
  independently of the pairing, so it prints a value even here. **This pass does not claim B5 from
  it.** The reading is consistent with every recorded pass — 3.4552% on 2026-10-12, 3.7479% on
  2026-10-13, 4.3391% in the six-pair run, and 3.4348% in the first pass today — all above the 3%
  budget at their own revisions.

The black baseline is `NOT MEASURED (DEVICE-LIMITED)`, declared to the report rather than inferred
from its absence: setting a static wallpaper rewrites the home image and this build has no root, so
that baseline can be neither backed up nor restored. `B6 PSS growth` is `NOT MEASURED` because no
`memory` phase was run, and `B1 hidden frames` is `NOT MEASURED HERE` because the frame counter is
#125 criterion 1, which records its own producer-trace evidence.

## What this pass does and does not establish

**It establishes** that the harness at `b7a694c` runs against real hardware and real dumps, and
specifically that the two device-facing fixes in that revision do not refuse anything this device
produces. Every phase exited 0, no phase wrote an aborted record, the censused dumps reconcile under
the branch's accounting at every count the device printed (566 jobs, 167 alarms, 0 wake locks), the
newly anchored jobscheduler census pattern accepts the device's own census line, the new
unset-versus-unreadable distinction reads this device's genuine `null` as "unset" and its four integer
settings as integers, and every restore returned the device to the state it was found in with nothing
reported.

**It does not establish B4, B5 or B6.** A single arm-A window is not an A/B alternation, so the paired
comparison B4 is gated on was not run. The B5 number above is one window's CPU, offered as an
observation that agrees with the recorded passes, not as this pass's B5 verdict. No `memory` phase was
run, so B6 has no value at all. None of the three would be meaningful from this window in any case:
the charge counter was frozen at 3 600 000 uAh throughout, because the device was on USB under the
simulated override.

**It does not re-run the first pass's readings or supersede them.** The APK digest is byte-identical
and the two revisions differ only in `scripts/battery_protocol.py`, so this pass is a re-run of the
same short harness pass at a later revision, and its counts sit beside the first pass's rather than
replacing them: the censuses it observed moved for the ordinary reason (the alarm census 161 → 167,
the job census 572 → 566, the wake-lock census 1 → 0) and the parser is what reconciles each dump's
own declared figure against the block below it.

**The recorded drain evidence still stands at its own older harness revision.** The 2026-10-13 and
six-pair reports were produced by an earlier `battery_protocol.py`; nothing here re-runs them or
supersedes their numbers.

One device, one build, one screen state, one site. Nothing here transfers to another device, another
window length, the lock screen, or a hidden engine. The device was left awake, on the home screen,
with the wallpaper binding it started with and every setting it was found with.
