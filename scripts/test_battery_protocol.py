#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the battery protocol's parsing, statistics, arm guard, and report."""

import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import battery_protocol as subject
import battery_report as report
import device_layer

# A `dumpsys battery` dump in the shape the test device actually prints, decoys included. Three of
# them carry a value under a similar name — `Max charging voltage`, `Capacity level`, and the
# broadcast echoes of both `voltage` and `level` — which is what an unanchored pattern picks up.
REALISTIC_BATTERY_DUMP = """\
Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  Dock powered: false
  Max charging current: 0
 Time when the latest updated value of the Max charging current was sent via battery changed broadcast: 0
  Max charging voltage: 0
  Charge counter: 4500000
  status: 4
  health: 2
  present: true
  level: 95
  scale: 100
  voltage: 4120
 Time when the latest updated value of the voltage was sent via battery changed broadcast: 0
 The last voltage value sent via the battery changed broadcast: 4120
  temperature: 280
  technology: Li-ion
  Charging state: 0
  Charging policy: 0
  Capacity level: -1
"""

SERVICE_COMPONENT = (
    "ComponentInfo{io.github.godaniya.astronomicalclockswallpaper.debug/"
    "io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService}"
)
STOCK_COMPONENT = "ComponentInfo{com.android.systemui/com.android.systemui.wallpapers.ImageWallpaper}"
BLACK_FRAME = (4, 4, bytes(4 * 4 * 4))
WHITE_FRAME = (4, 4, bytes([0xFF] * (4 * 4 * 4)))

# A `dumpsys jobscheduler` excerpt in the shape the test device prints it: the census line, one job
# for the app under test, and jobs belonging to other packages that must not be counted.
JOBSCHEDULER_DUMP = """\
Battery level: 94
Registered 3 jobs:
  JOB #10123/42: abc1234 io.github.godaniya.astronomicalclockswallpaper.debug/\
io.github.godaniya.astronomicalclockswallpaper.AstronomicalClocksWallpaperService
  JOB #1000/29: 6a6e122 com.samsung.android.statsd/androidx.work.impl.background.systemjob.SystemJobService
  JOB #1000/808: 1137bb6 android/com.android.server.MountServiceIdler
"""


def _window(*, cpu: float | None) -> subject.Window:
    """Build one measurement window with a fixed duration and CPU delta."""
    start = subject.BatterySample(
        timestamp=0.0,
        charge_counter_uah=4_000_000,
        level_pct=90,
        voltage_mv=4000,
        process_cpu_seconds=10.0 if cpu is not None else None,
        total_pss_kb=45_000,
    )
    end = subject.BatterySample(
        timestamp=600.0,
        charge_counter_uah=3_980_000,
        level_pct=90,
        voltage_mv=3990,
        process_cpu_seconds=(10.0 + cpu * 6.0) if cpu is not None else None,
        total_pss_kb=45_500,
    )
    return subject.Window(start_sample=start, end_sample=end, duration_seconds=600.0)


# The four `powered:` fields `dumpsys battery` prints, all false: the device is on its own battery.
# Every source is required, so a phase's power read is stubbed with the whole reading rather than one
# line of it.
ON_BATTERY_POWER_LINES = (
    "AC powered: false",
    "USB powered: false",
    "Wireless powered: false",
    "Dock powered: false",
)


# A digit run longer than `int()` will convert — it raises above about 4300 digits rather than
# building the integer — which is what an unbounded `\d+` in a dump parser hands it.
OVERSIZED_DIGITS = "9" * 5000


# The same four fields with a charger on USB: the state that puts a window under the `cmd battery
# unplug` override rather than leaving it on the device's own gauge.
ON_CHARGER_POWER_LINES = (
    "AC powered: false",
    "USB powered: true",
    "Wireless powered: false",
    "Dock powered: false",
)


def recorder() -> subject.Recorder:
    """Return a recorder bound to a throwaway state directory, for the phases that persist to one."""
    return subject.Recorder(Path(tempfile.mkdtemp()))


class ParseBatteryOutputTest(unittest.TestCase):
    """Checks that every required battery field is read or the probe fails."""

    def test_reads_every_field_of_a_realistic_dump(self) -> None:
        self.assertEqual((4500000, 95, 4120), subject.parse_battery_output(REALISTIC_BATTERY_DUMP))

    def test_voltage_field_is_anchored_to_the_pack_voltage(self) -> None:
        stripped = REALISTIC_BATTERY_DUMP.replace("  voltage: 4120\n", "")
        with self.assertRaises(device_layer.ProbeError):
            subject.parse_battery_output(stripped)

    def test_missing_field_raises_rather_than_reading_zero(self) -> None:
        for field, output in (
            ("Charge counter", REALISTIC_BATTERY_DUMP.replace("  Charge counter: 4500000\n", "")),
            ("level", REALISTIC_BATTERY_DUMP.replace("  level: 95\n", "")),
            ("voltage", REALISTIC_BATTERY_DUMP.replace("  voltage: 4120\n", "")),
        ):
            with self.subTest(field=field), self.assertRaises(device_layer.ProbeError):
                subject.parse_battery_output(output)

    def test_unrelated_output_raises(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.parse_battery_output("Nothing here")

    def test_an_oversized_counter_is_a_named_refusal_not_a_traceback(self) -> None:
        # `int()` refuses a value of more than about 4300 digits by raising, and that `ValueError` is
        # not one of the refusals `main` catches. An unbounded `\d+` therefore turned a battery dump
        # that lost its mind into a traceback instead of the refusal this probe promises.
        dump = f"  Charge counter: {OVERSIZED_DIGITS}\n  level: 95\n  voltage: 4120\n"
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_battery_output(dump)
        self.assertIn("Charge counter", str(raised.exception))


class ProcStatTest(unittest.TestCase):
    """Checks the `/proc/<pid>/stat` CPU-tick parsing."""

    def test_sums_utime_and_stime(self) -> None:
        # utime=150 and stime=50 at indices 13 and 14, so 200 ticks at 100 Hz.
        line = "1234 (astro) S 1 1234 1234 0 -1 4194304 100 0 0 0 150 50 0 0 20 0 1 0 1000 0 0"
        cpu_seconds = subject.parse_proc_stat_cpu_seconds(line, 100)
        if cpu_seconds is None:
            self.fail("a well-formed stat line carried no CPU time")
        self.assertAlmostEqual(2.0, cpu_seconds)

    def test_the_tick_rate_scales_the_result(self) -> None:
        # The same 200 ticks are one second at 200 Hz, so a caller that assumed 100 would double it.
        line = "1234 (astro) S 1 1234 1234 0 -1 4194304 100 0 0 0 150 50 0 0 20 0 1 0 1000 0 0"
        self.assertAlmostEqual(1.0, subject.parse_proc_stat_cpu_seconds(line, 200) or 0.0)

    def test_a_non_positive_tick_rate_is_refused(self) -> None:
        line = "1234 (astro) S 1 1234 1234 0 -1 4194304 100 0 0 0 150 50 0 0 20 0 1 0 1000 0 0"
        with self.assertRaises(device_layer.ProbeError):
            subject.parse_proc_stat_cpu_seconds(line, 0)

    def test_a_comm_field_containing_a_space_still_parses(self) -> None:
        # The comm field is parenthesised and may contain spaces, so the fields must be counted from
        # the closing parenthesis rather than from a whitespace split.
        line = "1234 (my astro clock) S 1 1234 1234 0 -1 4194304 100 0 0 0 150 50 0 0 20 0 1 0 1000 0 0"
        cpu_seconds = subject.parse_proc_stat_cpu_seconds(line, 100)
        if cpu_seconds is None:
            self.fail("a stat line whose comm contains a space carried no CPU time")
        self.assertAlmostEqual(2.0, cpu_seconds)

    def test_negative_tick_counters_have_no_value(self) -> None:
        # A tick count cannot be negative, so a negative token is a read that failed rather than a
        # measurement. `int()` takes one, and the only check downstream refuses a counter that went
        # *backwards* — it does not refuse one that began below the origin, so a negative start would
        # make the window's delta larger than the CPU the process actually spent.
        parts = ["S"] + ["0"] * 10 + ["-100", "-100"]
        line = "1234 (astro) " + " ".join(parts)
        self.assertIsNone(subject.parse_proc_stat_cpu_seconds(line, 100))
        shifted = ["S"] + ["0"] * 10 + ["500", "-100"]
        self.assertIsNone(subject.parse_proc_stat_cpu_seconds("1234 (astro) " + " ".join(shifted), 100))

    def test_lines_without_a_comm_or_with_unparsable_fields_have_no_value(self) -> None:
        self.assertIsNone(subject.parse_proc_stat_cpu_seconds("short string", 100))
        self.assertIsNone(subject.parse_proc_stat_cpu_seconds("1234 (astro) S 1 2 3 notanumber 50 0 0 0 1 2", 100))


class ClkTckProbeTest(unittest.TestCase):
    """Finding 1: the kernel tick rate is probed per run rather than assumed to be 100."""

    def test_a_probed_rate_is_returned(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 250
        self.assertEqual(250, subject.require_clk_tck(device))

    def test_an_unavailable_rate_fails_loudly(self) -> None:
        for missing in (None, 0):
            device = MagicMock()
            device.read_process_cpu_clock_ticks.return_value = missing
            with self.subTest(missing=missing), self.assertRaises(device_layer.ProbeError):
                subject.require_clk_tck(device)

    def test_a_rate_that_cannot_be_converted_is_a_named_refusal_not_a_traceback(self) -> None:
        # The device-layer reader tests `getconf CLK_TCK`'s text with `str.isdigit()` and then hands
        # it to `int()`, so a digit run past `int()`'s limit raises `ValueError` from inside it. That
        # is not an exception `main` catches, so a corrupted response reached the operator as a
        # traceback rather than the `ERROR:` line this probe's other failures produce. The reader is
        # reached here rather than faked, so the test pins the conversion as well as the translation.
        device = device_layer.AdbDevice("device")
        with (
            patch.object(device_layer, "run_adb", return_value=b"9" * 5000),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.require_clk_tck(device)
        self.assertIn("not a tick rate", str(raised.exception))

    def test_a_window_records_the_probed_rate_and_power_source(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 250
        device.run_adb.return_value = b"36\n"
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True,), (True,), (False,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
        ):
            record = subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        self.assertEqual(250, record.values["clk_tck"])
        self.assertEqual(subject.POWER_SOURCE_DEVICE, record.values["power_source"])
        # The surface in front is sampled through the window and carried into the record, the way the
        # display and power-source readings are, so the evidence the refusal rests on is on disk.
        self.assertEqual((True,), record.values["home_in_front_samples"])

    def test_a_device_without_a_tick_rate_fails_before_the_window(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = None
        with (
            patch.object(subject, "run_window") as window,
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        window.assert_not_called()
        device.wake_screen.assert_not_called()


# A `dumpsys power` excerpt in the shape the device prints it: the wake-lock census, one lock held by
# the app under test, and one held by another UID that must not be counted.
POWER_DUMP = """\
POWER MANAGER (dumpsys power)

Power Manager Service:
  mWakefulness=Awake
  Wake Locks: size=2
    PARTIAL_WAKE_LOCK              'AudioMix' ACQ=-1s234ms (uid=1013, pid=1234, ws=WorkSource{1013})
    PARTIAL_WAKE_LOCK              'WallpaperLock' ACQ=-2s345ms (uid=10026, pid=20859, ws=WorkSource{10026})
  Suspend Blockers: size=1
Display Power: state=ON
"""

# A `dumpsys alarm` excerpt in the shape the test device actually prints it, taken from its own dump:
# the census is `<n> pending alarms: ` with no `Pending alarm batches:` line anywhere in it, and each
# entry is a `<TYPE> #<n>: Alarm{...}` line whose last token is the package. The third entry is a
# package whose name has this app's as a prefix, which is what an install of a `…debug2` build beside
# this one puts in the list: it must not be counted as this app's alarm.
ALARM_DUMP = """\
Current Alarm Manager state:
  Settings:
    version=2
    min_futurity=+5s0ms
  3 pending alarms:
    ELAPSED #1: Alarm{eea5bdc type 3 origWhen 87399351 whenElapsed 87399351 android}
    RTC_WAKEUP #0: Alarm{7d0e24a type 0 origWhen 1712345678 whenElapsed 1712345678 \
io.github.godaniya.astronomicalclockswallpaper.debug}
    RTC_WAKEUP #1: Alarm{1be34f2 type 0 origWhen 1712345679 whenElapsed 1712345679 \
io.github.godaniya.astronomicalclockswallpaper.debug2}
"""


# A `dumpsys power` whose only lock is of a type other than `PARTIAL_WAKE_LOCK`, which is the state
# the test device was actually in: `Wake Locks: size=1` holding a `DOZE_WAKE_LOCK`. The census counts
# that lock, so a probe that enumerates partial locks alone can never reconcile with it.
POWER_DUMP_DOZE_LOCK = """\
POWER MANAGER (dumpsys power)

Power Manager Service:
  mWakefulness=Dozing
  Wake Locks: size=1
    DOZE_WAKE_LOCK                    'dream:doze' ACQ=-2m22s355ms (uid=10026 pid=1538 displayId=0 lock=782c8f1)
  Suspend Blockers: size=5
    PowerManagerService.Booting: ref count=0 []
Display Power: state=OFF
"""

# A `dumpsys alarm` in the shape the test device prints: each entry is an `Alarm{...}` header followed
# by indented continuation lines, the block is closed by a later section at the census's own indent,
# and below that section there is a *second* censused list whose entries look exactly like the first
# list's. The past-due entry names this app; nothing pending does, so the probe must report zero.
ALARM_DUMP_WITH_SECTIONS = """\
Current Alarm Manager state:
  Settings:
    version=2
  2 pending alarms:
    ELAPSED #1: Alarm{eea5bdc type 3 origWhen 87399351 whenElapsed 87399351 android}
      tag=*alarm*:android.intent.action.TIME_SET
      type=ELAPSED origWhen=+1s351ms window=+22m30s0ms
      operation=PendingIntent{83f0995: PendingIntentRecord{aca9438 android broadcastIntent}}
    RTC_WAKEUP #2: Alarm{7d0e24a type 0 origWhen 1712345678 whenElapsed 1712345678 com.other.app}
      tag=*walarm*:com.other.app.action.TICK
  LazyAlarmStore stats:
    GET_COUNT: count=0
  Past-due non-wakeup alarms: 1
    ELAPSED #1: Alarm{ae0dbfa type 3 origWhen 151266000 whenElapsed 151266000 \
io.github.godaniya.astronomicalclockswallpaper.debug}
      tag=*alarm*:io.github.godaniya.astronomicalclockswallpaper.action.FIRED
"""

# The namespaced job-line shapes the test device prints and the plain `JOB #<uid>/<id>` pattern did
# not match. WorkManager keeps its jobs under `androidx.work.systemjobscheduler` and the sync adapters
# under `SyncManager`, so an app that scheduled a worker had that worker's line dropped from the count
# and "the app holds no jobs" could come from a dump that listed one.
JOBSCHEDULER_DUMP_NAMESPACED = """\
Battery level: 94
Registered 3 jobs:
  JOB #1000/29: 6a6e122 com.samsung.android.statsd/androidx.work.impl.background.systemjob.SystemJobService
  JOB androidx.work.systemjobscheduler:10123/42: abc1234 \
@androidx.work.systemjobscheduler@io.github.godaniya.astronomicalclockswallpaper.debug/\
androidx.work.impl.background.systemjob.SystemJobService
  JOB SyncManager:1000/4: 78b894a @SyncManager@com.samsung.android.memo/com.osp.app.signin/a@example.org:android
"""

# A jobscheduler dump whose second job line carries neither a component nor a hash. It matches the
# entry predicate, so it counts towards the census, and it cannot be attributed to any package: a
# probe that only counted what it could parse would report the app's own jobs from a dump in which a
# job it could not place had been silently dropped.
JOBSCHEDULER_DUMP_UNATTRIBUTABLE = """\
Battery level: 94
Registered 2 jobs:
  JOB #1000/29: 6a6e122 com.samsung.android.statsd/androidx.work.impl.background.systemjob.SystemJobService
  JOB #1000/31: 6a6e123
"""


class WallpaperPidProbeTest(unittest.TestCase):
    """A `pidof` answer that cannot be read is a named refusal, not a traceback and not a zero."""

    def test_a_real_pid_is_returned(self) -> None:
        device = device_layer.AdbDevice("device")
        with patch.object(device_layer, "run_adb", return_value=b"4805\n"):
            self.assertEqual(4805, subject.wallpaper_pid(device))

    def test_no_live_process_is_still_none(self) -> None:
        # `pidof` exits 1 and prints nothing when the package has no process, and the device-layer
        # reader answers `None` for that. It is not the same answer as a corrupt response, and the
        # wrapper must not turn it into one.
        device = device_layer.AdbDevice("device")
        with patch.object(device_layer, "run_adb", side_effect=subprocess.CalledProcessError(1, "pidof")):
            self.assertIsNone(subject.wallpaper_pid(device))

    def test_a_pid_that_cannot_be_converted_is_a_named_refusal_not_a_traceback(self) -> None:
        # The device-layer reader converts a digit-only token with `int()`, so a digit run past
        # `int()`'s limit raises `ValueError` from inside it — an exception `main` does not catch, so
        # a corrupted response reached the operator as a traceback. The reader is reached here rather
        # than faked, so the test pins the conversion as well as the translation.
        device = device_layer.AdbDevice("device")
        with (
            patch.object(device_layer, "run_adb", return_value=b"9" * 5000),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.wallpaper_pid(device)
        self.assertIn("not a process id", str(raised.exception))


class WakeLockAndAlarmProbeTest(unittest.TestCase):
    """Finding 1: an unparsable power or alarm dump is an error, not a zero-lock B2 pass."""

    def test_counts_only_the_locks_held_by_the_app_s_uid(self) -> None:
        self.assertEqual(1, subject.parse_wakelock_count(POWER_DUMP, 10026))
        self.assertEqual(1, subject.parse_wakelock_count(POWER_DUMP, 1013))

    def test_a_census_with_no_matching_lock_is_a_real_zero(self) -> None:
        self.assertEqual(0, subject.parse_wakelock_count(POWER_DUMP, 99999))

    def test_a_dump_without_the_census_is_an_error_not_a_zero(self) -> None:
        # What a truncated or reshaped `dumpsys power` leaves behind: output that names no census and
        # therefore says nothing about wake locks at all.
        censusless = (
            "",
            "Power Manager Service:\n  mWakefulness=Awake\n",
            POWER_DUMP.split("Wake Locks", maxsplit=1)[0],
        )
        for dump in censusless:
            with self.subTest(dump=dump[:30]), self.assertRaises(device_layer.ProbeError):
                subject.parse_wakelock_count(dump, 10026)

    def test_counts_only_the_alarms_that_name_the_package(self) -> None:
        # Three pending alarms, one of them for `…astronomicalclockswallpaper.debug2`: counting by
        # substring would report two alarms held by this app, a B2 failure it did not cause.
        self.assertEqual(1, subject.parse_alarm_count(ALARM_DUMP))
        other_package = ALARM_DUMP.replace(
            "io.github.godaniya.astronomicalclockswallpaper.debug2", "com.other.pkg2"
        ).replace("io.github.godaniya.astronomicalclockswallpaper.debug", "com.other.pkg")
        self.assertEqual(0, subject.parse_alarm_count(other_package))

    def test_an_alarm_dump_without_the_census_is_an_error_not_a_zero(self) -> None:
        censusless = (
            "",
            "Current Alarm Manager state:\n  Settings:\n",
            ALARM_DUMP.split("pending alarms", maxsplit=1)[0],
        )
        for dump in censusless:
            with self.subTest(dump=dump[:30]), self.assertRaises(device_layer.ProbeError):
                subject.parse_alarm_count(dump)

    def test_the_census_refusal_names_the_line_the_probe_requires(self) -> None:
        # The required census is `<n> pending alarms:`. The refusal used to name `Pending alarm
        # batches:`, a line the device never prints and this probe never looks for, which sent an
        # operator looking in their dump for the wrong thing.
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_alarm_count("Current Alarm Manager state:\n  Settings:\n")
        self.assertIn("`pending alarms:`", str(raised.exception))
        self.assertNotIn("Pending alarm batches", str(raised.exception))

    def test_an_unresolved_uid_is_refused_before_the_probe_runs(self) -> None:
        device = MagicMock()
        with patch.object(device, "run_adb") as probe, self.assertRaises(device_layer.ProbeError):
            subject.count_held_wakelocks(device, None)
        probe.assert_not_called()

    def test_a_wake_lock_of_another_type_is_counted_and_accounted_for(self) -> None:
        # The census counts every lock on the device, and this device's was `Wake Locks: size=1`
        # holding a `DOZE_WAKE_LOCK`. Enumerating `PARTIAL_WAKE_LOCK` alone leaves that census
        # unaccounted for and the lock itself invisible: the app's own non-partial wake lock would be
        # reported as no wake lock at all, which is a B2 pass it did not earn.
        self.assertEqual(1, subject.parse_wakelock_count(POWER_DUMP_DOZE_LOCK, 10026))
        self.assertEqual(0, subject.parse_wakelock_count(POWER_DUMP_DOZE_LOCK, 1013))

    def test_a_power_dump_truncated_after_its_census_is_refused(self) -> None:
        # The finding this closes: the census line survives truncation, so a parser that only asks
        # whether a census is present reads a cut-off dump as a legitimate zero-lock pass.
        lines = POWER_DUMP.splitlines()
        cut = next(index for index, line in enumerate(lines) if "Wake Locks: size=" in line) + 1
        census_only = "\n".join(lines[:cut]) + "\n"
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_wakelock_count(census_only, 10026)
        self.assertIn("declares 2", str(raised.exception))
        self.assertIn("lists 0", str(raised.exception))

    def test_a_census_that_outnumbers_the_locks_is_refused(self) -> None:
        # A dump that stopped part-way through the lock list. The count and the dump disagree and
        # there is no reading that reconciles them, so the window is refused rather than measured.
        census_only = POWER_DUMP.replace("size=2", "size=4")
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_wakelock_count(census_only, 10026)
        self.assertIn("truncated", str(raised.exception))

    def test_a_census_that_undercounts_the_locks_is_refused(self) -> None:
        # The other direction. A census of 1 over a block holding 2 locks means one of them is a shape
        # the census does not count, so the block cannot be read as this census's entries either.
        undercounted = POWER_DUMP.replace("size=2", "size=1")
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_wakelock_count(undercounted, 10026)
        self.assertIn("lists 2", str(raised.exception))

    def test_a_census_with_no_locks_at_all_is_still_a_real_zero(self) -> None:
        # The refusal above must not turn a device that genuinely holds nothing into an error. The
        # empty census is built by *removing* the lock lines, not by renaming their lock type: a
        # renamed line is one the entry pattern does not recognise, and an unrecognised line inside
        # the census block is now a refusal of its own rather than a line to skip past — which is
        # exactly what the test below pins.
        emptied = POWER_DUMP.replace("size=2", "size=0")
        empty = "\n".join(line for line in emptied.splitlines() if "_WAKE_LOCK" not in line) + "\n"
        self.assertEqual(0, subject.parse_wakelock_count(empty, 10026))

    def test_an_unrecognised_line_in_the_census_block_is_refused(self) -> None:
        # Reconciliation alone cannot see this: a block holding a malformed entry beside exactly the
        # declared number of recognised ones reconciles perfectly, and a block holding one under a
        # census of zero reconciled too — because non-entry lines were skipped. Both read as a count
        # the dump does not support, and the second is a B2 pass from a census that describes no
        # block. A line that is neither an entry nor deeper indented than the entry it continues is
        # refused.
        cases = (
            ("  0 pending alarms:\n    MALFORMED_ALARM_ENTRY\n", subject.parse_alarm_count),
            (" Wake Locks: size=0\n  MALFORMED_LOCK_LINE\n", lambda dump: subject.parse_wakelock_count(dump, 10026)),
        )
        for dump, parse in cases:
            with self.subTest(dump=dump[:24]), self.assertRaises(device_layer.ProbeError) as raised:
                parse(dump)
            self.assertIn("neither an entry shaped like", str(raised.exception))
        # And the same thing under a census its recognised entries *do* satisfy: the third entry is
        # no longer in the entry shape, the two that remain reconcile with the count, and the
        # unrecognised line still sits in the block. Reconciliation alone accepts this dump.
        mixed = ALARM_DUMP.replace("  3 pending alarms:", "  2 pending alarms:").replace(
            "RTC_WAKEUP #1: Alarm{", "RTC_WAKEUP #1 Alarm{"
        )
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_alarm_count(mixed)
        self.assertIn("neither an entry shaped like", str(raised.exception))

    def test_a_continuation_line_deeper_than_its_entry_is_still_read(self) -> None:
        # The device's own alarm dump prints six or more `key=value` lines under every `Alarm{...}`
        # header, and the jobscheduler dump more below each `JOB`. Those are continuations, told
        # apart from an unrecognised line by being deeper indented than the entry they belong to, and
        # they must not be refused. Run against the real dump as well, which is where the rule was
        # checked before it was written.
        package = device_layer.PACKAGE_NAME
        dump = (
            "  2 pending alarms:\n"
            f"    ELAPSED #1: Alarm{{a {package}}}\n"
            "      tag=*walarm*:one\n"
            "      type=ELAPSED repeatInterval=0\n"
            f"    ELAPSED #2: Alarm{{b {package}}}\n"
            "      tag=*walarm*:two\n"
        )
        self.assertEqual(2, subject.parse_alarm_count(dump))

    def test_an_alarm_in_a_later_section_is_not_counted_as_pending(self) -> None:
        # `ALARM_DUMP_WITH_SECTIONS` names this app in `Past-due non-wakeup alarms:` and in nothing
        # pending. Scanning the whole dump for `Alarm{...}` finds the fired alarm and reports it as a
        # pending one the app is holding, which is a B2 failure the app does not have.
        self.assertEqual(0, subject.parse_alarm_count(ALARM_DUMP_WITH_SECTIONS))

    def test_the_alarm_block_counts_multi_line_entries_and_stops_at_the_next_section(self) -> None:
        # Each pending entry is an `Alarm{...}` header plus continuation lines, and the section after
        # the block holds a second censused list of the same shape. The block has to end at the
        # section boundary and count headers, not lines.
        entries = subject.census_entry_block(
            ALARM_DUMP_WITH_SECTIONS,
            subject.ALARM_CENSUS_PATTERN,
            subject.ALARM_ENTRY_PATTERN,
            probe="alarm dump",
            census_line="`pending alarms:`",
            entry_shape="`<TYPE> #<n>: Alarm{…}`",
        )
        self.assertEqual(2, len(entries))

    def test_an_alarm_census_that_does_not_match_the_block_is_refused(self) -> None:
        # A dump truncated between the census and the entries is the same fabricated zero as the
        # wake-lock one: the census line is what survives.
        truncated = ALARM_DUMP_WITH_SECTIONS.replace("  2 pending alarms:", "  5 pending alarms:")
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_alarm_count(truncated)
        self.assertIn("declares 5", str(raised.exception))

    def test_an_oversized_census_is_refused_rather_than_traceback(self) -> None:
        # The census count reaches `int()` as well, so it needs the same bound the field parsers do.
        # Without it, a corrupted census line raises a `ValueError` where the probe promises a named
        # refusal — the same escape `main` does not catch.
        absurd = f"{OVERSIZED_DIGITS} pending alarms:\n    ELAPSED #1: Alarm{{x type 3 android}}\n"
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_alarm_count(absurd)
        self.assertIn("pending alarms", str(raised.exception))

    def test_a_census_matches_on_its_own_line_and_not_the_blank_line_above_it(self) -> None:
        # The census patterns' leading class is `[ \t]*` and not `\s*`, because `\s` matches a newline:
        # the match could begin on the blank line *above* the census, and `census_entry_block` reads the
        # block from the match's own line, so it would start a line early. This device's dump has a
        # blank line above its census, and with `\s*` the fired alarm below the block is pulled in — it
        # declares 2 entries and lists 3. Nothing pins the anchoring without this fixture.
        dump = ALARM_DUMP_WITH_SECTIONS.replace("  2 pending alarms:", "\n  2 pending alarms:")
        self.assertIn("\n\n  2 pending alarms:", dump)
        self.assertEqual(0, subject.parse_alarm_count(dump))


class LoadRecordsTest(unittest.TestCase):
    """Finding 3: a truncated state file is a named error, not a traceback."""

    def state(self, *lines: str) -> Path:
        """Write the given lines to a fresh state file and return its directory."""
        directory = Path(tempfile.mkdtemp())
        (directory / subject.RECORD_FILE_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return directory

    def test_valid_records_load(self) -> None:
        directory = self.state('{"phase": "hidden"}', '{"phase": "visible"}')
        self.assertEqual([{"phase": "hidden"}, {"phase": "visible"}], subject.load_records(directory))

    def test_a_truncated_line_is_a_named_error_not_a_traceback(self) -> None:
        directory = self.state('{"phase": "hidden"}', '{"phase": "visible"')
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.load_records(directory)
        detail = str(raised.exception)
        self.assertIn(f"{subject.RECORD_FILE_NAME}:2", detail)
        self.assertIn("not valid JSON", detail)

    def test_a_line_that_is_not_an_object_is_named_too(self) -> None:
        directory = self.state('{"phase": "hidden"}', "[1, 2, 3]")
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.load_records(directory)
        self.assertIn(f"{subject.RECORD_FILE_NAME}:2 is not a JSON object", str(raised.exception))

    def test_a_missing_state_file_names_the_path(self) -> None:
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.load_records(Path(tempfile.mkdtemp()))
        self.assertIn(subject.RECORD_FILE_NAME, str(raised.exception))

    def test_a_state_file_that_is_not_utf8_is_named_not_a_traceback(self) -> None:
        # An append interrupted mid-character leaves a partial UTF-8 sequence, and `read_text` raises
        # `UnicodeDecodeError` on it. That is the same corrupt state file the JSON branch names,
        # failing one step earlier — and it used to fail as a traceback rather than a named refusal.
        directory = Path(tempfile.mkdtemp())
        (directory / subject.RECORD_FILE_NAME).write_bytes(b'{"phase": "hidden", "note": "\xff\xfe"}')
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.load_records(directory)
        self.assertIn("not valid UTF-8", str(raised.exception))
        self.assertIn(subject.RECORD_FILE_NAME, str(raised.exception))

    def test_a_number_json_refuses_to_convert_is_named_too(self) -> None:
        # `json` raises a plain `ValueError`, not a `JSONDecodeError`, for an integer with more digits
        # than `int()` will convert. Both are a line that cannot be read, so both are named.
        directory = self.state('{"phase": "hidden", "level_pct": ' + "9" * 5000 + "}")
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.load_records(directory)
        self.assertIn(f"{subject.RECORD_FILE_NAME}:1", str(raised.exception))
        self.assertIn("not valid JSON", str(raised.exception))


class JobProbeTest(unittest.TestCase):
    """Finding 2: B2 counts the app's JobScheduler jobs, and an unreadable probe is not a zero."""

    def test_counts_only_the_package_s_own_jobs(self) -> None:
        self.assertEqual(1, subject.parse_job_count(JOBSCHEDULER_DUMP))

    def test_a_census_with_no_matching_job_is_a_real_zero(self) -> None:
        dump = JOBSCHEDULER_DUMP.replace("io.github.godaniya.astronomicalclockswallpaper.debug", "com.example.other")
        self.assertEqual(0, subject.parse_job_count(dump))

    def test_a_dump_without_the_census_is_an_error_not_a_zero(self) -> None:
        for dump in ("Settings:\n  min_ready_cpu_only_jobs_count=3\n", "", "no jobs here\n"):
            with self.subTest(dump=dump[:20]), self.assertRaises(device_layer.ProbeError):
                subject.parse_job_count(dump)

    def test_a_namespaced_job_is_attributed_to_its_package(self) -> None:
        # `androidx.work.systemjobscheduler:<uid>/<id>` and `SyncManager:<uid>/<id>` are where an
        # app's WorkManager workers and sync adapters actually live. The old pattern matched only
        # `JOB #<uid>/<id>`, so a scheduled worker was invisible and the app read as holding no jobs.
        self.assertEqual(1, subject.parse_job_count(JOBSCHEDULER_DUMP_NAMESPACED))

    def test_every_job_line_is_accounted_for_by_the_census(self) -> None:
        entries = subject.census_entry_block(
            JOBSCHEDULER_DUMP_NAMESPACED,
            subject.JOBSCHEDULER_CENSUS_PATTERN,
            subject.JOB_ENTRY_PATTERN,
            probe="jobscheduler dump",
            census_line="`Registered <n> jobs:`",
            entry_shape="`JOB [<namespace>:]#<uid>/<id>: <hash> <component>`",
        )
        self.assertEqual(3, len(entries))
        self.assertEqual(3, len([line for line in entries if subject.JOB_LINE_PATTERN.match(line)]))

    def test_a_job_line_that_cannot_be_attributed_is_refused(self) -> None:
        # It matches the entry predicate, so it counts towards the census; if it were dropped from
        # the per-package count instead, the app's own jobs would be reported from a dump in which a
        # job nobody could place had gone missing.
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_job_count(JOBSCHEDULER_DUMP_UNATTRIBUTABLE)
        self.assertIn("cannot be attributed", str(raised.exception))

    def test_a_job_census_that_does_not_match_the_list_is_refused(self) -> None:
        truncated = JOBSCHEDULER_DUMP_NAMESPACED.replace("Registered 3 jobs:", "Registered 9 jobs:")
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_job_count(truncated)
        self.assertIn("declares 9", str(raised.exception))

    def test_a_census_line_with_a_suffix_is_not_the_census(self) -> None:
        # The census is required *because* it says this is a jobscheduler dump and what its count is.
        # Anchored at the start only, the pattern matched a malformed line and read it as written: a
        # dump holding nothing but `Registered 0 jobs: CORRUPT` reported the app as holding no jobs —
        # a B2 pass from a line the probe never read — and a suffixed count above zero was read as a
        # trusted number the same way. The power and alarm census patterns already required the line
        # to end after the count, and this one now does too.
        suffixed = subject.JOBSCHEDULER_CENSUS_PATTERN.search("Registered 0 jobs: CORRUPT\n") is None
        self.assertTrue(suffixed, "the census pattern still matches a line with a suffix")
        dumps = (
            "Registered 0 jobs: CORRUPT\n",
            JOBSCHEDULER_DUMP.replace("Registered 3 jobs:", "Registered 3 jobs: CORRUPT"),
        )
        for dump in dumps:
            with self.subTest(dump=dump[:40]), self.assertRaises(device_layer.ProbeError) as raised:
                subject.parse_job_count(dump)
            self.assertIn("carried no", str(raised.exception))


class PowerSourceProbeTest(unittest.TestCase):
    """Part 2: the device's own battery is used directly and only a charger is simulated away."""

    def test_all_false_means_the_device_is_on_battery(self) -> None:
        lines = ["AC powered: false", "USB powered: false", "Wireless powered: false", "Dock powered: false"]
        self.assertTrue(subject.power_lines_show_battery(lines))

    def test_any_true_means_a_charger_is_connected(self) -> None:
        lines = ["AC powered: false", "USB powered: true", "Wireless powered: false", "Dock powered: false"]
        self.assertFalse(subject.power_lines_show_battery(lines))

    def test_no_powered_line_is_an_error_not_a_guess(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.power_lines_show_battery(["Settings:"])

    def test_a_reading_missing_a_source_is_incomplete_not_on_battery(self) -> None:
        # A dump that lost three of its four `powered:` fields must not be read as "the one that
        # remains is false, so this device is discharging": the reading is incomplete, and the arm the
        # window is recorded against depends on it.
        complete = ["AC powered: false", "USB powered: false", "Wireless powered: false", "Dock powered: false"]
        for dropped in range(len(complete)):
            partial = [line for index, line in enumerate(complete) if index != dropped]
            with self.subTest(dropped=complete[dropped]), self.assertRaises(device_layer.ProbeError):
                subject.power_lines_show_battery(partial)

    def test_a_truncated_battery_dump_is_refused_before_the_mode_is_chosen(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"  AC powered: false\n  USB powered: false\n"
        with patch.object(subject, "simulate_unplug") as unplug, self.assertRaises(device_layer.ProbeError):
            subject.power_mode_for(device)
        unplug.assert_not_called()

    def test_a_device_on_its_own_battery_is_not_put_under_the_override(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = (
            b"  AC powered: false\n  USB powered: false\n  Wireless powered: false\n  Dock powered: false\n"
        )
        with patch.object(subject, "simulate_unplug") as unplug:
            mode = subject.power_mode_for(device)
            subject.apply_power_mode(device, mode)
        self.assertEqual(subject.POWER_SOURCE_DEVICE, mode)
        unplug.assert_not_called()

    def test_a_charging_device_is_simulated(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = (
            b"  AC powered: false\n  USB powered: true\n  Wireless powered: false\n  Dock powered: false\n"
        )
        with patch.object(subject, "simulate_unplug") as unplug:
            mode = subject.power_mode_for(device)
            subject.apply_power_mode(device, mode)
        self.assertEqual(subject.POWER_SOURCE_SIMULATED, mode)
        unplug.assert_called_once()

    def test_a_failed_unplug_still_marks_the_override_as_applied(self) -> None:
        # `cmd battery unplug` is a mutating command, and a timeout or a dropped transport leaves the
        # override on the device while the call raises. Deciding and applying are separate now, and the
        # phase records the decision before the write, so the restore still resets — reading the flag
        # back after the call would leave `simulated` False and skip the reset on a device that is
        # still simulated.
        device = self.device_on_the_home_screen()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_CHARGER_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(subject, "simulate_unplug", side_effect=subprocess.CalledProcessError(1, "unplug")),
            patch.object(subject, "undo_phase_changes", return_value=[]) as undo,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        undo.assert_called_once()
        self.assertTrue(undo.call_args.kwargs["simulated"])

    def device_on_the_home_screen(self) -> MagicMock:
        """Return a device that satisfies the home-screen guard, so a window can actually start."""
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        return device

    def test_a_window_that_saw_a_charger_is_refused(self) -> None:
        # The device has to get past `ensure_visible_home` first, or the refusal under test is never
        # reached and the test passes without exercising the charger check at all.
        device = self.device_on_the_home_screen()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(
                subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True, False), (True,), (False,))
            ),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        self.assertIn("charger connected inside the window", str(raised.exception))

    def test_a_visible_window_whose_display_went_off_is_refused(self) -> None:
        device = self.device_on_the_home_screen()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(
                subject,
                "run_window",
                return_value=(_window(cpu=1.0), (True, False), (True, True), (True, True), (False, False)),
            ),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        self.assertIn("the display was not on for the whole visible window", str(raised.exception))

    def test_a_visible_window_whose_pack_gained_charge_is_refused(self) -> None:
        # `Window.discharge_rate_ua` raises when the counter rose, which is the phase's own refusal
        # rather than the print's: it has to be reached on the abort path like the others.
        device = self.device_on_the_home_screen()
        gained = subject.Window(
            start_sample=subject.BatterySample(0.0, 3_980_000, 90, 4000, 10.0, 45_000),
            end_sample=subject.BatterySample(600.0, 4_000_000, 90, 3990, 16.0, 45_500),
            duration_seconds=600.0,
        )
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(gained, (True,), (True,), (True,), (False,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        self.assertIn("pack gained charge", str(raised.exception))

    def test_a_window_with_no_power_sample_is_refused(self) -> None:
        self.assertIsNotNone(subject.power_source_problem([]))
        self.assertIsNone(subject.power_source_problem([True, True]))

    def test_a_hidden_window_that_saw_a_charger_is_refused(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        sample = subject.BatterySample(0.0, 1, 100, 4000, 1.0, 1000)
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "take_battery_sample", side_effect=[sample, sample]),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (False, False), (False, True), [], [])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_hidden(device, minutes=1.0, recorder=recorder())

    def test_a_memory_window_that_saw_a_charger_is_refused(self) -> None:
        # As with the visible window: the home-screen guard has to be satisfied first, or this test
        # passes on the guard's refusal and never exercises the charger check.
        device = self.device_on_the_home_screen()
        sample = subject.BatterySample(0.0, 1, 100, 4000, 1.0, 1000)
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "take_battery_sample", side_effect=[sample, sample]),
            patch.object(
                subject,
                "sample_window_states",
                return_value=([True, True], [True, False], [True, True], [False, False]),
            ),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_memory(device, minutes=1.0, recorder=recorder())
        self.assertIn("charger connected inside the window", str(raised.exception))

    def test_a_visible_window_that_left_the_home_screen_is_refused(self) -> None:
        # The arm guard establishes the home screen *before* the window, and another app can come
        # forward after that with the bound wallpaper component unchanged, so the display, the
        # keyguard and the arm all still pass. `read_home_in_front` says what is needed here in its own
        # docstring — "a caller measuring a window must verify the surface at the points it requires
        # rather than trusting this one answer for the whole window" — so the surface is sampled with
        # the rest of the window's state, and one sample that is not the home screen refuses it.
        for samples in ([True, False], [True, None]):
            with (
                self.subTest(samples=samples),
                self.assertRaises(device_layer.ProbeError) as raised,
            ):
                subject.require_visible_measurement(
                    _window(cpu=1.0),
                    [True, True],
                    [True, True],
                    samples,
                    report.Arm.LIVE,
                    keyguard_locked=[False, False],
                )
            self.assertIn("not the surface in front", str(raised.exception))
        self.assertAlmostEqual(
            120000.0,
            subject.require_visible_measurement(
                _window(cpu=1.0),
                [True, True],
                [True, True],
                [True, True],
                report.Arm.LIVE,
                keyguard_locked=[False, False],
            ),
        )

    def test_a_memory_window_that_left_the_home_screen_is_refused(self) -> None:
        # B6 is defined over a window on the live wallpaper, so a different app in front means the
        # wallpaper is not the surface being drawn for the window the record claims.
        before = subject.BatterySample(0.0, 4_000_000, 90, 4000, 10.0, 45_000)
        after = subject.BatterySample(1800.0, 3_980_000, 90, 3990, 12.0, 45_500)
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.require_memory_measurement([True], [True], [False], before, after, keyguard_locked=[False])
        self.assertIn("not the surface in front", str(raised.exception))
        self.assertEqual(
            500,
            subject.require_memory_measurement([True], [True], [True], before, after, keyguard_locked=[False]),
        )

    def test_a_visible_window_that_locked_mid_window_is_refused(self) -> None:
        # `ensure_visible_home` reads the keyguard once before the window, but a screen that locks
        # after that draws its lock surface over the home screen while the display stays on and the
        # arm still matches. The keyguard samples are therefore part of the window, and one that
        # shows the lock surface — or that could not be read — refuses it.
        for samples in ([False, True], [False, None]):
            with (
                self.subTest(samples=samples),
                self.assertRaises(device_layer.ProbeError) as raised,
            ):
                subject.require_visible_measurement(
                    _window(cpu=1.0),
                    [True, True],
                    [True, True],
                    [True, True],
                    report.Arm.LIVE,
                    keyguard_locked=samples,
                )
            self.assertIn("keyguard was showing or unreadable", str(raised.exception))
        self.assertAlmostEqual(
            120000.0,
            subject.require_visible_measurement(
                _window(cpu=1.0),
                [True, True],
                [True, True],
                [True, True],
                report.Arm.LIVE,
                keyguard_locked=[False, False],
            ),
        )

    def test_a_memory_window_that_locked_mid_window_is_refused(self) -> None:
        # The same three-valued rule as the visible window: B6's growth is the growth of a wallpaper
        # that the lock surface was covering for part of the window the record claims.
        before = subject.BatterySample(0.0, 4_000_000, 90, 4000, 10.0, 45_000)
        after = subject.BatterySample(1800.0, 3_980_000, 90, 3990, 12.0, 45_500)
        for samples in ([False, True], [False, None]):
            with (
                self.subTest(samples=samples),
                self.assertRaises(device_layer.ProbeError) as raised,
            ):
                subject.require_memory_measurement(
                    [True, True], [True, True], [True, True], before, after, keyguard_locked=samples
                )
            self.assertIn("keyguard was showing or unreadable", str(raised.exception))
        self.assertEqual(
            500,
            subject.require_memory_measurement([True], [True], [True], before, after, keyguard_locked=[False]),
        )


class HiddenWindowSleepTest(unittest.TestCase):
    """Checks that a screen-off window confirms the display went off before it starts."""

    def test_a_display_that_did_not_go_off_refuses_the_window(self) -> None:
        # The sleep key event is a request, not the state — `device_layer.sleep_screen` reports only
        # that it was delivered. The first display sample is a whole sampling interval away, so a
        # display still lit, or one that could not be read, would appear in no later reading: every
        # sample would be `False` once it did sleep, and B3 would be accepted for a window that was
        # not screen-off throughout.
        for reading in (True, None):
            with self.subTest(reading=reading):
                device = MagicMock()
                device.read_screen_on.return_value = reading
                with patch.object(subject.time, "sleep"), self.assertRaises(device_layer.ProbeError) as raised:
                    subject.run_window(device, None, 1.0, screen_on=False, clk_tck=100)
                self.assertIn("did not read back as off", str(raised.exception))

    def test_a_display_that_is_off_lets_the_window_run(self) -> None:
        device = MagicMock()
        device.read_screen_on.return_value = False
        sample = subject.BatterySample(0.0, 4_000_000, 90, 4000, None, 45_000)
        with (
            patch.object(subject.time, "sleep"),
            patch.object(subject, "take_battery_sample", side_effect=[sample, sample]),
            patch.object(subject, "sample_window_states", return_value=([False], [True], [], [])),
        ):
            _, states, on_battery, homes, _locks = subject.run_window(device, None, 1.0, screen_on=False, clk_tck=100)
        self.assertEqual([False], states)
        self.assertEqual([True], on_battery)
        self.assertEqual([], homes)
        device.read_screen_on.assert_called_once()


class WindowSurfaceSamplingTest(unittest.TestCase):
    """Checks that a window's surface and keyguard are sampled through it, not only checked before it."""

    def test_a_visible_window_samples_the_surface_in_front_and_the_keyguard(self) -> None:
        device = MagicMock()
        device.read_screen_on.return_value = True
        device.read_home_in_front.return_value = True
        device.read_keyguard_locked.return_value = False
        with (
            patch.object(subject, "device_on_own_battery", return_value=True),
            patch.object(subject.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            states, on_battery, homes, locks = subject.sample_window_states(device, 60.0, home_required=True)
        self.assertEqual([True, True], states)
        self.assertEqual([True, True], on_battery)
        self.assertEqual([True, True], homes)
        # The keyguard is as much a fact about the surface as the foreground package is: it is read
        # with every sample rather than once before the window, so a screen that locks mid-window is
        # caught by the same evidence the surface check rests on.
        self.assertEqual([False, False], locks)
        self.assertEqual(2, device.read_home_in_front.call_count)
        self.assertEqual(2, device.read_keyguard_locked.call_count)

    def test_a_hidden_window_samples_no_surface(self) -> None:
        # The display is off for a hidden window, so there is no surface in front to require: the
        # sampler returns empty lists rather than reading ones nothing would use.
        device = MagicMock()
        device.read_screen_on.return_value = False
        with (
            patch.object(subject, "device_on_own_battery", return_value=True),
            patch.object(subject.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            states, on_battery, homes, locks = subject.sample_window_states(device, 60.0, home_required=False)
        self.assertEqual([False, False], states)
        self.assertEqual([True, True], on_battery)
        self.assertEqual([], homes)
        self.assertEqual([], locks)
        device.read_home_in_front.assert_not_called()
        device.read_keyguard_locked.assert_not_called()


class ArmGuardTest(unittest.TestCase):
    """Checks that a window is refused unless the device matches the requested arm."""

    def guard(
        self,
        pixels: tuple[int, int, bytes],
        component: str,
        arm: report.Arm,
        baseline: report.Baseline | None,
    ) -> tuple[str | None, str | None]:
        """Run the guard against a device showing the given screen with the given component bound."""
        device = MagicMock()
        device.serial = "serial"
        device.run_adb.return_value = b"screencap"
        with (
            patch.object(device_layer, "read_wallpaper_component", return_value=component),
            patch.object(device_layer, "decode_frame", return_value=pixels),
        ):
            try:
                return subject.verify_arm(device, arm, baseline), None
            except device_layer.ProbeError as error:
                return None, str(error)

    def refusal(
        self,
        pixels: tuple[int, int, bytes],
        component: str,
        arm: report.Arm,
        baseline: report.Baseline | None,
    ) -> str:
        """Run the guard expecting a refusal and return the reason it gave."""
        accepted, error = self.guard(pixels, component, arm, baseline)
        self.assertIsNone(accepted)
        if error is None:
            self.fail("the guard accepted a window it should have refused")
        return error

    def test_live_arm_accepts_the_service_component(self) -> None:
        accepted, error = self.guard(WHITE_FRAME, SERVICE_COMPONENT, report.Arm.LIVE, None)
        self.assertIsNone(error)
        self.assertEqual(SERVICE_COMPONENT, accepted)

    def test_live_arm_refuses_a_static_wallpaper(self) -> None:
        self.assertIn("arm A was requested", self.refusal(WHITE_FRAME, STOCK_COMPONENT, report.Arm.LIVE, None))

    def test_baseline_arm_refuses_a_bound_live_service(self) -> None:
        self.assertIn(
            "arm B was requested",
            self.refusal(WHITE_FRAME, SERVICE_COMPONENT, report.Arm.BASELINE, report.Baseline.STOCK),
        )

    def test_baseline_arm_requires_a_baseline_name(self) -> None:
        self.assertIn("needs --baseline", self.refusal(WHITE_FRAME, STOCK_COMPONENT, report.Arm.BASELINE, None))

    def test_both_baselines_are_admitted_on_the_component_alone(self) -> None:
        # The screen's luminance does not admit a baseline: the device's own stock wallpaper is a
        # dark image, so a brightness gate would refuse valid stock windows without separating the
        # two static baselines. Both are admitted here and the luminance is recorded instead.
        for baseline in (report.Baseline.STOCK, report.Baseline.BLACK):
            for pixels in (WHITE_FRAME, BLACK_FRAME):
                with self.subTest(baseline=baseline, luma=pixels is WHITE_FRAME):
                    accepted, error = self.guard(pixels, STOCK_COMPONENT, report.Arm.BASELINE, baseline)
                    self.assertIsNone(error)
                    self.assertEqual(STOCK_COMPONENT, accepted)

    def test_baseline_arm_refuses_another_package_s_live_wallpaper(self) -> None:
        # Arm B admitted "any component that is not this app's service", which a third-party live
        # wallpaper satisfies. Such a window would be recorded against the stock or black arm while
        # draining whatever that live service costs, so the baseline it claims is not the baseline
        # it measures.
        other_live = "ComponentInfo{com.example.other/com.example.other.LiveWallpaperService}"
        refused = self.refusal(WHITE_FRAME, other_live, report.Arm.BASELINE, report.Baseline.STOCK)
        self.assertIn("com.example.other", refused)
        self.assertIn("not a baseline", refused)

    def test_baseline_arm_refuses_a_static_component_from_another_package(self) -> None:
        # Naming the platform engine is stronger than "anything that is not ours": a component that
        # looks static but belongs elsewhere is still not the baseline the criterion defines.
        other_static = "ComponentInfo{com.example.launcher/com.example.launcher.ImageWallpaper}"
        for baseline in (report.Baseline.STOCK, report.Baseline.BLACK):
            with self.subTest(baseline=baseline):
                refused = self.refusal(BLACK_FRAME, other_static, report.Arm.BASELINE, baseline)
                self.assertIn("not a baseline", refused)

    def test_the_platform_engine_is_the_only_static_component_admitted(self) -> None:
        self.assertTrue(subject.component_is_static(STOCK_COMPONENT))
        self.assertTrue(subject.component_is_static(device_layer.PLATFORM_STATIC_WALLPAPER))
        self.assertFalse(subject.component_is_static(SERVICE_COMPONENT))
        self.assertFalse(
            subject.component_is_static("ComponentInfo{com.example.other/com.example.other.LiveWallpaperService}")
        )
        self.assertFalse(subject.component_is_static(""))

    def test_the_baseline_luminance_is_recorded_not_gated(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        device.run_adb.return_value = b"screencap"
        with (
            patch.object(device_layer, "read_wallpaper_component", return_value=STOCK_COMPONENT),
            patch.object(device_layer, "decode_frame", return_value=BLACK_FRAME),
        ):
            conditions = subject.arm_conditions(device, report.Arm.BASELINE, report.Baseline.STOCK)
        self.assertEqual(0.0, conditions["median_luma"])
        self.assertIn("not gated", str(conditions["median_luma_note"]))

    def test_median_luma_reads_the_screen(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"screencap"
        for pixels, expected in ((BLACK_FRAME, 0.0), (WHITE_FRAME, 255.0)):
            with self.subTest(expected=expected), patch.object(device_layer, "decode_frame", return_value=pixels):
                self.assertAlmostEqual(expected, subject.median_luma(device))

    def test_a_capture_that_cannot_be_decoded_is_refused_by_name(self) -> None:
        # `decode_frame` raises `device_layer.ScreencapError`, which is a sibling of `ProbeError` and
        # not one of the refusals `main` catches. A truncated `screencap` payload therefore left the
        # CLI printing a traceback instead of the named `ERROR` the rest of the harness reports.
        device = MagicMock()
        device.run_adb.return_value = b"not a screencap"
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.median_luma(device)
        self.assertIn("could not be decoded", str(raised.exception))


class VisiblePhaseGuardTest(unittest.TestCase):
    """Checks that the phase refuses a mismatched arm before it measures anything."""

    def device(self) -> MagicMock:
        """Return a device whose tick-rate probe answers, so the phase reaches its guard."""
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        return device

    def test_a_mismatched_arm_is_refused_before_the_window_starts(self) -> None:
        device = self.device()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "verify_arm", side_effect=device_layer.ProbeError("arm A was requested")) as guard,
            patch.object(subject, "run_window") as window,
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        guard.assert_called_once()
        window.assert_not_called()

    def test_the_guard_is_consulted_for_a_baseline_arm_too(self) -> None:
        device = self.device()
        with (
            patch.object(subject, "verify_arm") as guard,
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "static", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True,), (True,), (False,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
        ):
            subject.phase_visible(
                device, arm=report.Arm.BASELINE, baseline=report.Baseline.STOCK, minutes=1.0, recorder=recorder()
            )
        guard.assert_called_once()
        self.assertIs(report.Arm.BASELINE, guard.call_args.args[1])


class HiddenPhaseArmGuardTest(unittest.TestCase):
    """Finding 5: B3 is the live engine's CPU, so a static wallpaper must refuse the window."""

    def test_the_hidden_phase_verifies_the_live_arm_before_the_window(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm", side_effect=device_layer.ProbeError("arm A was requested")) as guard,
            patch.object(subject, "run_window") as window,
            patch.object(subject, "undo_phase_changes", return_value=[]),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_hidden(device, minutes=10.0, recorder=recorder())
        guard.assert_called_once()
        self.assertIs(report.Arm.LIVE, guard.call_args.args[1])
        window.assert_not_called()

    def test_the_hidden_phase_does_not_pin_the_device_before_the_guard_admits_it(self) -> None:
        snapshot = MagicMock()
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        with (
            patch.object(subject, "DeviceStateSnapshot") as captured,
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm", side_effect=device_layer.ProbeError("arm A was requested")),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            self.assertRaises(device_layer.ProbeError),
        ):
            captured.capture.return_value = snapshot
            subject.phase_hidden(device, minutes=10.0, recorder=recorder())
        snapshot.pin.assert_not_called()


class AbortedRestoreTest(unittest.TestCase):
    """Finding 8: a phase that aborts after changing the device still records its restore problems."""

    def test_an_aborted_phase_persists_the_restore_it_could_not_verify(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        device.read_installed_apk_sha256.return_value = "a" * 64
        state_dir = Path(tempfile.mkdtemp())
        sink = subject.Recorder(state_dir)
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", side_effect=device_layer.ProbeError("the window died")),
            patch.object(subject, "undo_phase_changes", return_value=["airplane_mode_on did not read back as 0"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_hidden(device, minutes=10.0, recorder=sink)
        # The original failure still propagates, but the restore it took with it is now on disk where
        # `report` can see it instead of only in a variable that went out of scope.
        recorded = subject.load_records(state_dir)
        self.assertEqual(["hidden_aborted"], [record["phase"] for record in recorded])
        self.assertEqual(["airplane_mode_on did not read back as 0"], recorded[0]["restore_problems"])
        lines = [verdict.line for verdict in report.report_lines(recorded)]
        self.assertTrue(any(line.startswith("DEVICE RESTORE: FAIL") for line in lines))
        self.assertEqual(1, report.report_exit_code(report.report_lines(recorded)))

    def test_a_clean_abort_writes_nothing(self) -> None:
        # An abort whose restore succeeded has no problems to carry, and an empty aborted record would
        # read as a phase that ran.
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        state_dir = Path(tempfile.mkdtemp())
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", side_effect=device_layer.ProbeError("the window died")),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_hidden(device, minutes=10.0, recorder=subject.Recorder(state_dir))
        self.assertFalse((state_dir / subject.RECORD_FILE_NAME).exists())

    def test_a_hidden_refusal_after_the_restore_persists_them_too(self) -> None:
        # The window's acceptance checks run after `undo_phase_changes`, so a window refused for a
        # reason that has nothing to do with the restore has to carry the restore's problems to disk
        # all the same: the device is left changed either way, and this record is the only place the
        # report can learn it. Before this path covered the checks, the `ProbeError` left through
        # `main`'s handler and the problems went out of scope unwritten.
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        device.read_installed_apk_sha256.return_value = "a" * 64
        state_dir = Path(tempfile.mkdtemp())
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True, True), (True, True), [], [])),
            patch.object(subject, "undo_phase_changes", return_value=["airplane_mode_on did not read back as 0"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_hidden(device, minutes=10.0, recorder=subject.Recorder(state_dir))
        # The refusal is the window's, not the restore's — the display stayed on — but the restore
        # problem that came with it is on disk.
        self.assertIn("the display was not off for the whole window", str(raised.exception))
        recorded = subject.load_records(state_dir)
        self.assertEqual(["hidden_aborted"], [record["phase"] for record in recorded])
        self.assertEqual(["airplane_mode_on did not read back as 0"], recorded[0]["restore_problems"])

    def test_a_visible_refusal_after_the_restore_persists_them_too(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        device.read_installed_apk_sha256.return_value = "a" * 64
        state_dir = Path(tempfile.mkdtemp())
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(
                subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True, False), (True,), (False,))
            ),
            patch.object(subject, "undo_phase_changes", return_value=["screen_brightness did not read back as 128"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(
                device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=subject.Recorder(state_dir)
            )
        self.assertIn("charger connected inside the window", str(raised.exception))
        recorded = subject.load_records(state_dir)
        self.assertEqual(["visible_aborted"], [record["phase"] for record in recorded])
        self.assertEqual(["screen_brightness did not read back as 128"], recorded[0]["restore_problems"])

    def test_a_memory_refusal_after_the_restore_persists_them_too(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        device.read_installed_apk_sha256.return_value = "a" * 64
        # A `dumpsys meminfo` that carried no TOTAL line, which is the third phase's own refusal.
        unparsable = subject.BatterySample(0.0, 1, 100, 4000, 1.0, None)
        state_dir = Path(tempfile.mkdtemp())
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "take_battery_sample", side_effect=[unparsable, unparsable]),
            patch.object(subject, "sample_window_states", return_value=([True], [True], [True], [False])),
            patch.object(subject, "undo_phase_changes", return_value=["the display could not be restored"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_memory(device, minutes=1.0, recorder=subject.Recorder(state_dir))
        self.assertIn("total-PSS sample could not be parsed", str(raised.exception))
        recorded = subject.load_records(state_dir)
        self.assertEqual(["memory_aborted"], [record["phase"] for record in recorded])
        self.assertEqual(["the display could not be restored"], recorded[0]["restore_problems"])


class MemoryPhaseTest(unittest.TestCase):
    """Checks that the B6 window verifies its arm and its display state."""

    def test_a_display_that_came_on_fails_the_window(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        sample = subject.BatterySample(
            timestamp=0.0,
            charge_counter_uah=1,
            level_pct=100,
            voltage_mv=4000,
            process_cpu_seconds=1.0,
            total_pss_kb=1000,
        )
        with (
            patch.object(subject, "verify_arm") as guard,
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "take_battery_sample", side_effect=[sample, sample]),
            patch.object(
                subject,
                "sample_window_states",
                return_value=([True, False], [True, True], [True, True], [False, False]),
            ),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_memory(device, minutes=1.0, recorder=recorder())
        guard.assert_called_once()

    def test_the_recorded_duration_is_the_span_the_samples_were_taken_over(self) -> None:
        # The hidden and visible phases record the span they measured. The memory phase recorded the
        # length that was *requested* instead, so its record asserted a window length it had not
        # measured — and `report` is the reader that gates a record on its own recorded length.
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        device.run_adb.return_value = b"36\n"
        before = subject.BatterySample(1000.0, 4_000_000, 90, 4000, 10.0, 45_000)
        after = subject.BatterySample(2805.0, 3_980_000, 90, 3990, 12.0, 45_500)
        with (
            patch.object(subject, "verify_arm"),
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "take_battery_sample", side_effect=[before, after]),
            patch.object(subject, "sample_window_states", return_value=([True], [True], [True], [False])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            record = subject.phase_memory(device, minutes=30.0, recorder=recorder())
        self.assertEqual(after.timestamp - before.timestamp, record.duration_seconds)
        self.assertNotEqual(30.0 * 60.0, record.duration_seconds)


class HiddenPhaseTest(unittest.TestCase):
    """Checks that the B3 window is only recorded when it is the measurement B3 is defined on."""

    def device(self) -> MagicMock:
        """Return a device whose arm, display state and CPU counters can all be read."""
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        return device

    def test_a_window_that_held_the_display_off_is_recorded_with_its_cpu(self) -> None:
        # The success path: every other hidden-window test stops at a refusal, so the record B3 is
        # actually judged on was never produced by the suite.
        device = self.device()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=0.5), [False, False], [True, True], [], [])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            record = subject.phase_hidden(device, minutes=1.0, recorder=recorder())
        self.assertEqual("hidden", record.phase)
        self.assertEqual(0.5, record.values["cpu_pct"])
        self.assertEqual([False, False], record.values["screen_on_samples"])
        # The window's length is `duration_seconds`, which is the monotonic delta the measurement is
        # taken on. The record also used to carry that same number again as `wall_clock_seconds`,
        # naming a provenance the value contradicts and duplicating the field nothing reads.
        self.assertNotIn("wall_clock_seconds", record.values)

    def test_a_window_that_left_the_display_on_is_refused(self) -> None:
        device = self.device()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "power_mode_for", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=0.5), (False, True), (True, True), [], [])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_hidden(device, minutes=1.0, recorder=recorder())
        self.assertIn("the display was not off for the whole window", str(raised.exception))


class WindowValidityTest(unittest.TestCase):
    """Checks the window itself refuses a measurement that is not one."""

    def test_a_hidden_window_whose_cpu_could_not_be_read_is_refused(self) -> None:
        # The pid can be recycled and the process restart inside a window, leaving no CPU delta to
        # read. Returning `None` as B3's figure would record a hidden-CPU criterion that no reading
        # supports, so the phase refuses instead. Nothing pinned this refusal before.
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.require_hidden_measurement(_window(cpu=None), [False], [True])
        self.assertIn("could not be read over the hidden window", str(raised.exception))

    def test_a_live_visible_window_whose_cpu_could_not_be_read_is_refused(self) -> None:
        # The same refusal on the B5 side, and only for the live arm: a baseline arm has no wallpaper
        # process to read CPU from, so `None` there is the expected reading rather than a failure.
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.require_visible_measurement(
                _window(cpu=None), [True], [True], [True], report.Arm.LIVE, keyguard_locked=[False]
            )
        self.assertIn("could not be read over a visible window", str(raised.exception))
        self.assertIsInstance(
            subject.require_visible_measurement(
                _window(cpu=None), [True], [True], [True], report.Arm.BASELINE, keyguard_locked=[False]
            ),
            float,
        )

    def test_a_cpu_counter_that_went_backwards_is_refused(self) -> None:
        # The pid was recycled, or the process restarted inside the window: the delta is not CPU this
        # process spent, and reporting it would put a nonsense figure into B3 or B5.
        window = subject.Window(
            start_sample=subject.BatterySample(0.0, 4_000_000, 90, 4000, 10.0, 45_000),
            end_sample=subject.BatterySample(600.0, 3_980_000, 90, 3990, 5.0, 45_500),
            duration_seconds=600.0,
        )
        with self.assertRaises(device_layer.ProbeError) as raised:
            _ = window.cpu_pct
        self.assertIn("went backwards", str(raised.exception))


class DisplaySnapshotOrderTest(unittest.TestCase):
    """Finding 7: the original display state is captured before the wake and restored on refusal."""

    def test_the_display_state_is_captured_before_the_wake(self) -> None:
        events: list[str] = []
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        snapshot = MagicMock()
        snapshot.restore.return_value = []

        def capture(_device: object) -> MagicMock:
            events.append("capture")
            return snapshot

        def wake() -> bool:
            events.append("wake")
            return True

        def restore(*_args: object, **_kwargs: object) -> list[str]:
            events.append("restore")
            return []

        device.wake_screen.side_effect = wake
        with (
            patch.object(subject.DeviceStateSnapshot, "capture", side_effect=capture),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm", side_effect=device_layer.ProbeError("arm refused")),
            patch.object(subject, "undo_phase_changes", side_effect=restore),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_visible(device, arm=report.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        # The capture precedes the wake, and the restore still runs even though the arm was refused
        # after the display had already been woken.
        self.assertEqual(["capture", "wake", "restore"], events)

    def test_a_phase_that_started_with_the_display_off_records_that(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = False
        with (
            patch.object(subject, "require_int_setting", return_value=128),
            # The device's own `null`, which is the reading that means the setting was never set.
            patch.object(subject, "read_setting_text", return_value=subject.UNSET_SETTING_TEXT),
        ):
            snapshot = subject.DeviceStateSnapshot.capture(device)
        self.assertIsNotNone(snapshot.screen_on)
        self.assertFalse(snapshot.screen_on)


class RestoreTest(unittest.TestCase):
    """Checks that a failed reset cannot skip putting the user's settings back."""

    def test_a_failed_battery_reset_still_runs_the_settings_restore(self) -> None:
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = ["system/screen_brightness did not read back as 35"]
        with patch.object(subject, "reset_battery_simulation", side_effect=subprocess.CalledProcessError(1, "reset")):
            problems = subject.undo_phase_changes(device, snapshot, simulated=True)
        snapshot.restore.assert_called_once()
        self.assertTrue(any("battery simulation reset failed" in problem for problem in problems))
        self.assertIn("system/screen_brightness did not read back as 35", problems)

    def test_the_reset_runs_only_when_the_simulation_was_applied(self) -> None:
        # A device already on its own battery is never put under the override, so there is nothing to
        # clear and clearing it would write to a device whose state is already correct.
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = []
        with patch.object(subject, "reset_battery_simulation") as reset:
            subject.undo_phase_changes(device, snapshot)
        reset.assert_not_called()
        with patch.object(subject, "reset_battery_simulation") as reset:
            subject.undo_phase_changes(device, snapshot, simulated=True)
        reset.assert_called_once()

    def test_a_failing_restore_step_does_not_skip_the_later_ones(self) -> None:
        # The airplane-mode write used to come first and its failure aborted the rest of the restore,
        # leaving brightness, the timeout and the stay-awake setting unset. Each step is isolated now,
        # so a device that rejects every write reports every step rather than only the first.
        device = MagicMock()
        device.run_adb.side_effect = subprocess.CalledProcessError(1, "settings put")
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=1,
            screen_on=True,
        )
        with patch.object(subject, "read_int_setting", return_value=None):
            problems = snapshot.restore()
        failures = [problem for problem in problems if "restore failed" in problem]
        self.assertGreaterEqual(len(failures), 5)

    def test_a_failing_restore_readback_does_not_escape(self) -> None:
        # The readback used to sit outside the guard, so a transport failure there escaped the whole
        # restore and skipped the display step below it.
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=1,
            screen_on=True,
        )
        with patch.object(subject, "read_int_setting", side_effect=subprocess.CalledProcessError(1, "settings get")):
            problems = snapshot.restore()
        self.assertTrue(any("could not be verified" in problem for problem in problems))
        self.assertGreaterEqual(len(problems), 5)
        device.wake_screen.assert_called_once()

    def test_a_failing_unset_stay_awake_readback_is_aggregated(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=None,
            screen_on=True,
        )
        with patch.object(
            subject, "read_unsettable_int_setting", side_effect=subprocess.CalledProcessError(1, "settings get")
        ):
            problems = snapshot.restore()
        self.assertTrue(any("stay-awake restore could not be verified" in problem for problem in problems))
        device.wake_screen.assert_called_once()

    def test_an_unreadable_unset_readback_is_a_problem_not_a_clean_restore(self) -> None:
        # The delete is verified by reading the key back, and `read_int_setting` answers None for text
        # it cannot parse — the same answer as the unset value the delete was meant to produce. A
        # readback nobody could read was therefore reported as a restore that succeeded, which is the
        # one reading of this step that lets a device left changed pass as restored.
        device = MagicMock()
        device.run_adb.return_value = b"Settings provider is dead"
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=None,
            screen_on=True,
        )
        problems = snapshot.restore()
        self.assertTrue(any("stay-awake restore could not be verified" in problem for problem in problems))

    def test_the_settings_restore_is_not_lost_when_only_the_reset_fails(self) -> None:
        # This test used to assert `[] == undo_phase_changes(...)` with a reset that did *not* fail and
        # a settings restore whose fake returns `[]`, so it held whether or not the settings restore
        # ran: deleting that step from `undo_phase_changes` left it passing. The premise the name
        # states is made to happen here, and the restore running anyway is what is asserted.
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = []
        with (
            patch.object(subject, "reset_battery_simulation", side_effect=subprocess.CalledProcessError(1, "reset")),
            patch.object(subject, "power_source_restored", return_value=True),
        ):
            problems = subject.undo_phase_changes(device, snapshot, ON_BATTERY_POWER_LINES, simulated=True)
        snapshot.restore.assert_called_once()
        self.assertEqual(1, len(problems))
        self.assertIn("battery simulation reset failed", problems[0])

    def test_an_unreadable_reset_readback_does_not_skip_the_settings_restore(self) -> None:
        # `power_source_restored` reads a battery dump, so it can raise `ProbeError` rather than
        # answer, and a dump that lost a field does exactly that. It shared the reset's guard, which
        # caught transport errors only, so the `ProbeError` escaped `undo_phase_changes` before the
        # settings restore below it ran: the shared device stayed pinned and the problems list stayed
        # empty, so nothing reached disk for `report` to print.
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = []
        prior_power = ("AC powered: false", "USB powered: false", "Wireless powered: false", "Dock powered: false")
        with patch.object(
            subject, "power_source_restored", side_effect=device_layer.ProbeError("the dump lost a field")
        ):
            problems = subject.undo_phase_changes(device, snapshot, prior_power, simulated=True)
        snapshot.restore.assert_called_once()
        self.assertTrue(any("power sources could not be read" in problem for problem in problems))

    def test_a_failing_display_readback_does_not_discard_the_settings_problems(self) -> None:
        # `restore()` is reached through `problems.extend(...)`, so a display read that raised would
        # discard every problem the settings steps had already collected — `extend` never runs and the
        # caller reports one generic failure in their place — and the sleep would be skipped too.
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.side_effect = subprocess.CalledProcessError(1, "dumpsys power")
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=None,
            screen_on=False,
        )
        with patch.object(subject, "read_int_setting", return_value=None):
            problems = snapshot.restore()
        self.assertTrue(any("display state could not be read" in problem for problem in problems))
        self.assertGreaterEqual(len(problems), 5)
        device.sleep_screen.assert_not_called()

    def test_a_readback_that_is_not_a_decimal_integer_does_not_escape_the_restore(self) -> None:
        # The same readback `read_int_setting` performs runs inside the restore. Text it cannot parse
        # is now `None`, which `_apply` reports as a value that did not read back, so the steps after
        # it and the display restore still run.
        device = MagicMock()
        device.run_adb.return_value = b"--5"
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=1,
            screen_on=True,
        )
        problems = snapshot.restore()
        self.assertGreaterEqual(len(problems), 5)
        device.wake_screen.assert_called_once()

    def test_airplane_mode_is_restored_to_the_value_it_was_found_with(self) -> None:
        # A device that had airplane mode on before a phase must still have it on afterwards; the
        # restore used to switch it off unconditionally and then report its own write as a failure.
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=1,
            stay_on=1,
            screen_on=True,
        )
        restored_values = {
            "screen_brightness": 35,
            "screen_brightness_mode": 1,
            "screen_off_timeout": 300_000,
            "airplane_mode_on": 1,
            "stay_on_while_plugged_in": 1,
        }
        with patch.object(subject, "read_int_setting", side_effect=lambda _d, _ns, key: restored_values[key]):
            problems = snapshot.restore()
        commands = [call.args[0] for call in device.run_adb.call_args_list]
        airplane = [command for command in commands if "airplane-mode" in command]
        self.assertEqual(1, len(airplane))
        self.assertIn("enable", airplane[0])
        self.assertEqual([], problems)

    def test_the_screen_timeout_is_pinned_under_the_key_the_platform_reads(self) -> None:
        device = MagicMock()
        snapshot = subject.DeviceStateSnapshot(
            device,
            brightness=35,
            brightness_mode=1,
            screen_timeout=300_000,
            airplane_mode=0,
            stay_on=None,
            screen_on=True,
        )
        snapshot.pin()
        written = [call.args[0] for call in device.run_adb.call_args_list]
        self.assertTrue(any("screen_off_timeout" in args for args in written))
        self.assertFalse(any("screen_timeout" in args for args in written))


class IntegerReadbackTest(unittest.TestCase):
    """
    Checks that a device's integer readback is parsed, not *tested* for digits.

    `str.isdigit()` answers a different question from "can this be read as an integer": `"²"` is a
    digit but not a decimal one, `"--5"` passes the `lstrip("-").isdigit()` guard the setting read
    used, and a value of more than a few thousand digits is refused by `int()` itself. Each of them
    raised `ValueError` from a readback whose callers treat "unreadable" as a value, so each one was a
    traceback rather than a `None` the caller could report.
    """

    def test_a_setting_that_is_not_a_decimal_integer_is_unreadable_not_a_crash(self) -> None:
        for raw in ("--5", "²", "9" * 5000, "null", "", "+"):
            with self.subTest(raw=raw[:8]):
                device = MagicMock()
                device.run_adb.return_value = raw.encode()
                self.assertIsNone(subject.read_int_setting(device, "system", "screen_brightness"))

    def test_a_signed_setting_still_reads(self) -> None:
        # `screen_brightness` is the reason the sign is allowed at all: the platform reports `-1` for
        # it on a device using the automatic brightness setting.
        device = MagicMock()
        device.run_adb.return_value = b"-1"
        self.assertEqual(-1, subject.read_int_setting(device, "system", "screen_brightness"))

    def test_a_uid_that_is_not_a_decimal_integer_is_unreadable_not_a_crash(self) -> None:
        for raw in ("²", "9" * 5000):
            with self.subTest(raw=raw[:8]):
                device = MagicMock()
                device.run_adb.return_value = raw.encode()
                self.assertIsNone(subject.read_process_uid(device, 1234))

    def test_a_negative_uid_is_unreadable_rather_than_used(self) -> None:
        # `stat -c %u` prints a UID, which is never negative, so `-1` is a read that failed rather
        # than a process's owner. The setting read shares a signed pattern because `screen_brightness`
        # is legitimately `-1`; a UID does not, because `phase_resources` refuses only `None` and the
        # wake-lock predicate built from `-1` matches no lock and reports a zero — a B2 pass from a
        # read that answered nothing.
        device = MagicMock()
        device.run_adb.return_value = b"-1"
        self.assertIsNone(subject.read_process_uid(device, 1234))

    def test_a_real_uid_still_reads(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"10026"
        self.assertEqual(10026, subject.read_process_uid(device, 1234))

    def test_an_unresolvable_pid_is_still_no_uid(self) -> None:
        device = MagicMock()
        for pid in (None, 0, -1):
            with self.subTest(pid=pid):
                self.assertIsNone(subject.read_process_uid(device, pid))
        device.run_adb.assert_not_called()


class MainExitCodeTest(unittest.TestCase):
    """Checks that main reports a restore failure as a failure rather than exiting zero."""

    def run_main(self, record: subject.Record) -> int:
        device = MagicMock()
        device.run_adb.return_value = b"36\n"

        def fake_dispatch(_args: object, _device: object, recorder: subject.Recorder) -> list[subject.Record]:
            recorder.emit(record)
            return [record]

        with (
            patch.object(device_layer, "select_target_serial", return_value="device"),
            patch.object(device_layer, "AdbDevice", return_value=device),
            patch.object(subject, "dispatch", side_effect=fake_dispatch),
            patch.object(subject, "append_record"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return subject.main(["resources"])

    def test_a_clean_run_exits_zero(self) -> None:
        record = subject.Record("resources", "now", "rev", None, 36, 0.0, {"restore_problems": []})
        self.assertEqual(0, self.run_main(record))

    def test_a_restore_problem_exits_non_zero(self) -> None:
        record = subject.Record(
            "hidden", "now", "rev", None, 36, 1.0, {"restore_problems": ["airplane_mode_on did not read back as 0"]}
        )
        self.assertEqual(1, self.run_main(record))

    def test_a_target_selection_failure_is_a_named_error_not_a_traceback(self) -> None:
        # `select_target_serial` runs `adb devices`, so a missing or wedged adb is the most common way
        # this harness fails. It was called outside the guard, so that failure printed a traceback
        # instead of the `ERROR:` line — and the exit code — every other failure gets.
        with (
            patch.object(device_layer, "select_target_serial", side_effect=subprocess.CalledProcessError(1, "adb")),
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = subject.main(["resources"])
        self.assertEqual(1, code)
        self.assertIn("ERROR:", stderr.getvalue())

    def test_an_unusable_selection_still_exits_rather_than_returning(self) -> None:
        # `select_target_serial` reports a selection it cannot make by exiting. Moving it inside the
        # guard must not turn that `SystemExit` into a returned exit code, or a caller would read a
        # refused selection as a phase that ran.
        with (
            patch.object(device_layer, "select_target_serial", side_effect=SystemExit(1)),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            subject.main(["resources"])


class ReportCommandRefusalTest(unittest.TestCase):
    """The `report` subcommand refuses a state file it cannot read by name, and touches no device."""

    def test_a_non_scalar_identity_value_is_a_named_error_not_a_traceback(self) -> None:
        # The CLI's promise for corrupt state is an `ERROR:` line on stderr and exit 1. The identity
        # check builds a set per field, so a list there raised `TypeError: unhashable type` out of
        # `main` instead — the one corrupt-record path that escaped as a crash, and reachable only
        # through this subcommand. No device is faked: `report` returns before
        # `select_target_serial`, so a test that had to patch one would be the regression.
        directory = Path(tempfile.mkdtemp())
        state_file = directory / subject.RECORD_FILE_NAME
        state_file.write_text('{"phase": "resources", "device_id": []}\n', encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = subject.main(["--state", str(directory), "report"])
        self.assertEqual(1, code)
        self.assertIn("ERROR:", stderr.getvalue())
        self.assertIn("device_id", stderr.getvalue())

    def test_a_non_finite_identity_value_is_refused_end_to_end(self) -> None:
        # The same promise for the other identity refusal, and the one whose reachability has to be
        # shown rather than argued: `NaN` is not JSON, but it is what Python's `json.loads` accepts by
        # default, so a hand-edited state file carries it into the report as a float. The record
        # carries nothing but the one field, so the identity rule is the only rule that can refuse it
        # — it is a float, and no identity field is written as one.
        directory = Path(tempfile.mkdtemp())
        state_file = directory / subject.RECORD_FILE_NAME
        state_file.write_text('{"phase": "resources", "clk_tck": NaN}\n', encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as stderr:
            code = subject.main(["--state", str(directory), "report"])
        self.assertEqual(1, code)
        self.assertIn("ERROR:", stderr.getvalue())
        self.assertIn("clk_tck", stderr.getvalue())
        self.assertIn("not the integer this field is written as", stderr.getvalue())


class ResourcesPhaseTest(unittest.TestCase):
    """Checks that a resources record says which wake-lock coverage its count measured."""

    def test_the_record_carries_the_all_type_coverage_stamp(self) -> None:
        # `report` will only bound the B2 wake-lock budget with counts whose record says they
        # enumerated every lock type. The stamp has to be written by the phase that measured it, or
        # every new record would read as a legacy partial-only census and the row would never be
        # measured again.
        device = MagicMock()
        device.serial = "R58M12345AB"
        device.run_adb.return_value = b"36\n"
        device.read_installed_apk_sha256.return_value = "a" * 64
        with (
            patch.object(subject, "wallpaper_pid", return_value=42),
            patch.object(subject, "read_process_uid", return_value=10026),
            patch.object(subject, "count_held_wakelocks", return_value=0),
            patch.object(subject, "count_alarms", return_value=0),
            patch.object(subject, "count_jobs", return_value=0),
            patch.object(subject, "read_batterystats_summary", return_value=None),
            patch.object(subject, "read_source_revision", return_value="rev"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            record = subject.phase_resources(device)
        self.assertEqual(report.WAKE_LOCK_COVERAGE, record.values["wake_lock_coverage"])
        self.assertEqual("all-types", record.values["wake_lock_coverage"])


class MeasurementIdentityTest(unittest.TestCase):
    """Finding 4: a record set that mixes builds or channels is refused, naming the values."""

    def test_a_consistent_set_is_accepted(self) -> None:
        records = [
            {"apk_sha256": "a", "api_level": 36, "power_source": "device", "clk_tck": 100},
            {"apk_sha256": "a", "api_level": 36, "power_source": "device", "clk_tck": 100},
        ]
        report.check_measurement_identity(records)

    def test_a_mixed_apk_is_refused_naming_both_values(self) -> None:
        records = [{"apk_sha256": "aaa", "api_level": 36}, {"apk_sha256": "bbb", "api_level": 36}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.check_measurement_identity(records)
        self.assertIn("aaa", str(raised.exception))
        self.assertIn("bbb", str(raised.exception))

    def test_a_mixed_api_level_is_refused(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.check_measurement_identity([{"api_level": 35}, {"api_level": 36}])

    def test_a_mixed_power_source_is_refused(self) -> None:
        records = [{"apk_sha256": "a", "power_source": "device"}, {"apk_sha256": "a", "power_source": "simulated"}]
        with self.assertRaises(device_layer.ProbeError):
            report.check_measurement_identity(records)

    def test_a_mixed_tick_rate_is_refused(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.check_measurement_identity([{"clk_tck": 100}, {"clk_tck": 250}])

    def test_a_field_a_record_does_not_carry_is_not_a_disagreement(self) -> None:
        report.check_measurement_identity([{"api_level": 36}, {"api_level": 36, "clk_tck": 100}])

    def test_a_null_apk_checksum_does_not_aggregate_with_a_named_one(self) -> None:
        # `append_record` always writes `apk_sha256`, so a null checksum is a recorded value meaning
        # "unknown build", not a missing field: a window measured before the APK was built must not
        # aggregate with one measured against it.
        records = [{"apk_sha256": None, "api_level": 36}, {"apk_sha256": "abc", "api_level": 36}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.check_measurement_identity(records)
        self.assertIn("unknown", str(raised.exception))
        self.assertIn("abc", str(raised.exception))

    def test_a_checksum_no_record_carries_is_not_bounded(self) -> None:
        report.check_measurement_identity([{"api_level": 36}, {"api_level": 36, "apk_sha256": "abc"}])

    def test_a_mixed_device_is_refused_naming_both_digests(self) -> None:
        # Two devices at one build and API level are still two measurements: the threshold and the
        # noise floor behind it are per device, so their records must not be aggregated into one B4
        # or B5 verdict.
        records = [{"device_id": "aaaa", "apk_sha256": "a"}, {"device_id": "bbbb", "apk_sha256": "a"}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.check_measurement_identity(records)
        self.assertIn("aaaa", str(raised.exception))
        self.assertIn("bbbb", str(raised.exception))

    def test_the_device_identity_is_a_digest_and_not_the_serial(self) -> None:
        # `docs/device-testing.md` keeps the serial off public surfaces and `phase_report` prints
        # every identity field, so the record carries a digest that tells two devices apart without
        # naming either.
        one, two = MagicMock(), MagicMock()
        one.serial, two.serial = "R58M12345AB", "R58M12345AC"
        identity = subject.device_identity(one)
        self.assertEqual(identity, subject.device_identity(one))
        self.assertNotEqual(identity, subject.device_identity(two))
        self.assertNotIn("R58M", identity)
        self.assertEqual(subject.DEVICE_ID_CHARS, len(identity))

    def test_a_device_that_reports_no_serial_is_refused(self) -> None:
        device = MagicMock()
        device.serial = ""
        with self.assertRaises(device_layer.ProbeError):
            subject.device_identity(device)

    def test_a_transport_address_is_not_a_device_identity(self) -> None:
        # `adb devices` reports a wirelessly-debugged device as `host:port`. That is an endpoint of
        # the transport, not a handle on the hardware: the port changes on every reconnect and the
        # address can be reused. Digesting it would split one device's records into several device
        # identities and merge two devices that happened to reuse an endpoint.
        def identify(endpoint: str, hardware_serial: str) -> str:
            device = MagicMock()
            device.serial = endpoint
            device.run_adb.return_value = f"{hardware_serial}\n".encode()
            return subject.device_identity(device)

        usb = MagicMock()
        usb.serial = "R58M12345AB"
        # The same device reached over two different ports is one identity, and it is the identity
        # its USB records already carry, so the records aggregate rather than being refused as a
        # mixed set.
        self.assertEqual(identify("192.0.2.5:37121", "R58M12345AB"), identify("192.0.2.5:40117", "R58M12345AB"))
        self.assertEqual(identify("192.0.2.5:37121", "R58M12345AB"), subject.device_identity(usb))
        # Two devices behind the same endpoint (reused after a disconnect) are two identities.
        self.assertNotEqual(identify("192.0.2.5:37121", "R58M12345AB"), identify("192.0.2.5:37121", "R58M12345AC"))

    def test_an_mdns_service_name_is_not_a_device_identity(self) -> None:
        # adb's own discovery publishes the device as `adb-<serial>-<suffix>._adb-tls-connect._tcp`,
        # which carries no `:`. The suffix belongs to the service advertisement, not to the device,
        # so a digest of the service name splits one device's records across mDNS, USB and
        # `host:port` connections. All three selectors must resolve to the hardware serial's digest.
        def identify(selector: str, hardware_serial: str) -> str:
            device = MagicMock()
            device.serial = selector
            device.run_adb.return_value = f"{hardware_serial}\n".encode()
            return subject.device_identity(device)

        usb = MagicMock()
        usb.serial = "R58M12345AB"
        expected = subject.device_identity(usb)
        for selector in (
            "adb-R58M12345AB-AbCdEf._adb-tls-connect._tcp",
            "adb-R58M12345AB-AbCdEf._adb-tls-connect._tcp.",
            "adb-R58M12345AB-ZyXwVu._adb._tcp",
        ):
            with self.subTest(selector=selector):
                self.assertEqual(expected, identify(selector, "R58M12345AB"))
        # A stale suffix for the same device is still the same device; a different device behind a
        # reused service name is not.
        self.assertEqual(
            identify("adb-R58M12345AB-AbCdEf._adb-tls-connect._tcp", "R58M12345AB"),
            identify("adb-R58M12345AB-ZyXwVu._adb-tls-connect._tcp", "R58M12345AB"),
        )
        self.assertNotEqual(
            identify("adb-R58M12345AB-AbCdEf._adb-tls-connect._tcp", "R58M12345AB"),
            identify("adb-R58M12345AB-AbCdEf._adb-tls-connect._tcp", "R58M12345AC"),
        )

    def test_the_boot_serial_is_used_when_the_serial_property_is_empty(self) -> None:
        # The fallback is not cosmetic: a device that does not expose `ro.serialno` still answers
        # for `ro.boot.serialno`, and the identity behind a transport address is the same either way.
        device = MagicMock()
        device.serial = "192.0.2.5:37121"
        device.run_adb.side_effect = [b"\n", b"R58M12345AB\n"]
        identity = subject.device_identity(device)
        usb = MagicMock()
        usb.serial = "R58M12345AB"
        self.assertEqual(subject.device_identity(usb), identity)

    def test_a_transport_device_with_no_readable_serial_is_refused(self) -> None:
        # A selector with no hardware serial behind it is not an identity: refusing is the only
        # answer that does not stamp the record with an address that names no device. Both shapes of
        # selector are covered, because the mDNS form carries no `:` and must not fall through.
        for selector in ("192.0.2.5:37121", "adb-R58M12345AB-AbCdEf._adb-tls-connect._tcp"):
            for answers in ([b"\n", b"unknown\n"], [b"\n", b"\n"]):
                with self.subTest(selector=selector, answers=answers):
                    device = MagicMock()
                    device.serial = selector
                    device.run_adb.side_effect = answers
                    with self.assertRaises(device_layer.ProbeError) as raised:
                        subject.device_identity(device)
                    self.assertIn("serial property could not be read", str(raised.exception))

    def test_an_adb_failure_while_reading_the_serial_is_refused(self) -> None:
        device = MagicMock()
        device.serial = "192.0.2.5:37121"
        device.run_adb.side_effect = subprocess.CalledProcessError(1, ["adb"])
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.device_identity(device)
        self.assertIn("serial property could not be read", str(raised.exception))

    def test_a_record_carries_the_device_it_was_measured_on(self) -> None:
        # The assertion used to be `assertEqual(device_identity(device), record["device_id"])`: the
        # expected value computed by the very function `build_record` calls, so it held for any
        # implementation — a constant one included. What the name claims is that the field *tells
        # devices apart*, so the same device is compared with itself and with another.
        def identify(serial: str) -> object:
            device = MagicMock()
            device.serial = serial
            device.run_adb.return_value = b"36\n"
            with (
                patch.object(subject, "read_source_revision", return_value="rev"),
                patch.object(subject, "read_apk_sha256", return_value="abc"),
            ):
                return subject.build_record(device, "resources", 0.0).values["device_id"]

        self.assertIn("device_id", report.IDENTITY_FIELDS)
        self.assertEqual(identify("R58M12345AB"), identify("R58M12345AB"))
        self.assertNotEqual(identify("R58M12345AB"), identify("R58M00000ZZ"))

    def test_the_digest_is_read_from_the_installed_apk(self) -> None:
        # The identity the record carries is the build the *device* is running. Hashing the host
        # checkout's artifact answers a different question, and answers it with `None` on a machine
        # that never built one, so "unknown build" could be recorded as though it were an identity.
        device = MagicMock()
        device.read_installed_apk_sha256.return_value = "b" * 64
        self.assertEqual("b" * 64, subject.read_apk_sha256(device))

    def test_a_digest_the_device_cannot_answer_is_refused(self) -> None:
        device = MagicMock()
        device.read_installed_apk_sha256.return_value = None
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.read_apk_sha256(device)
        self.assertIn("which build", str(raised.exception))

    def test_a_record_now_names_the_installed_build(self) -> None:
        device = MagicMock()
        device.serial = "R58M12345AB"
        device.run_adb.return_value = b"36\n"
        device.read_installed_apk_sha256.return_value = "c" * 64
        with patch.object(subject, "read_source_revision", return_value="rev"):
            record = subject.build_record(device, "resources", 0.0)
        self.assertEqual("c" * 64, record.apk_sha256)

    def test_phase_report_refuses_a_mixed_set(self) -> None:
        records = [
            {"phase": "hidden", "cpu_pct": 0.1, "apk_sha256": "aaa", "api_level": 36},
            {"phase": "hidden", "cpu_pct": 0.1, "apk_sha256": "bbb", "api_level": 36},
        ]
        with self.assertRaises(device_layer.ProbeError), contextlib.redirect_stdout(io.StringIO()):
            report.phase_report(records)


class PilotPersistenceTest(unittest.TestCase):
    """Finding 8: each window is persisted as it completes and the floor is its own record."""

    def record(self, rate: float) -> subject.Record:
        """Build a visible-window record as the pilot's inner phase would return it."""
        return subject.Record(
            "visible", "now", "rev", None, 36, 600.0, {"discharge_rate_ua": rate, "cpu_pct": 1.0, "arm": "A"}
        )

    def test_every_window_is_emitted_and_the_floor_is_a_separate_record(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"36\n"
        recorder = MagicMock()
        windows = [self.record(1000.0 + offset) for offset in (0.0, 5.0, 10.0, 15.0)]
        with patch.object(subject, "phase_visible", side_effect=windows):
            returned = subject.phase_pilot(
                device, arm=report.Arm.LIVE, baseline=None, runs=4, minutes=1.0, recorder=recorder
            )
        self.assertEqual(4, recorder.emit.call_count)
        self.assertEqual(["pilot_floor"], [record.phase for record in returned])
        self.assertIsNotNone(returned[0].values["noise_floor_rate_ua"])

    def test_a_window_that_did_not_restore_the_device_stops_the_pilot(self) -> None:
        # The windows are one measurement of one device in one state. A window whose restore failed
        # leaves a power override applied or a display in the state this run did not find it in, and
        # the next window would be measured on that. `main` reports the accumulated problems, but only
        # once the whole pilot has returned — up to two hours of windows later.
        device = MagicMock()
        device.run_adb.return_value = b"36\n"
        recorder = MagicMock()
        first = self.record(1000.0)
        first.values["restore_problems"] = ["global/stay_on_while_plugged_in did not return to unset"]
        windows = [first, self.record(1005.0), self.record(1010.0), self.record(1015.0)]
        with (
            patch.object(subject, "phase_visible", side_effect=windows),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_pilot(device, arm=report.Arm.LIVE, baseline=None, runs=4, minutes=1.0, recorder=recorder)
        # The window that failed is persisted before the run stops, so stopping loses no evidence.
        self.assertEqual(1, recorder.emit.call_count)
        self.assertIn("did not restore the device", str(raised.exception))
        self.assertIn("stay_on_while_plugged_in", str(raised.exception))

    def test_a_dead_discharge_channel_emits_no_floor_and_is_marked_unmeasured(self) -> None:
        # The target device's gauge reports a constant charge counter, so every window's rate is zero.
        # A 2-sigma half-width of zero divided by a zero mean is not a quiet channel, it is no reading
        # at all, and emitting it as a 0% floor would let a later report calibrate a threshold from a
        # counter that never moved.
        device = MagicMock()
        device.run_adb.return_value = b"36\n"
        recorder = MagicMock()
        windows = [self.record(0.0) for _ in range(4)]
        with patch.object(subject, "phase_visible", side_effect=windows):
            returned = subject.phase_pilot(
                device, arm=report.Arm.LIVE, baseline=None, runs=4, minutes=15.0, recorder=recorder
            )
        floor = returned[0]
        self.assertFalse(floor.values["discharge_channel_measured"])
        self.assertNotIn("noise_floor_rate_ua", floor.values)
        self.assertNotIn("noise_floor_pct", floor.values)

    def test_an_interrupted_pilot_keeps_the_windows_it_completed(self) -> None:
        device = MagicMock()
        recorder = MagicMock()
        windows = [self.record(1000.0), self.record(1010.0)]
        with (
            patch.object(subject, "phase_visible", side_effect=[*windows, device_layer.ProbeError("window 3 failed")]),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_pilot(device, arm=report.Arm.LIVE, baseline=None, runs=3, minutes=1.0, recorder=recorder)
        self.assertEqual(2, recorder.emit.call_count)


class DurationArgumentTest(unittest.TestCase):
    """Finding 4: an unusable duration is refused at the command line, not discovered mid-window."""

    def parse(self, *argv: str) -> argparse.Namespace:
        """Parse a command line, swallowing the usage message argparse prints before it exits."""
        with contextlib.redirect_stderr(io.StringIO()):
            return subject.build_argument_parser().parse_args(list(argv))

    def parse_refusing(self, *argv: str) -> None:
        """Assert that a command line is refused by argparse."""
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            subject.build_argument_parser().parse_args(list(argv))

    def test_a_non_finite_duration_is_refused(self) -> None:
        # `inf` is not a long window: `sample_window_states` subtracts its interval from it forever,
        # so a phase meant to be bounded would never return. `nan` slips past a `<= 0` test entirely.
        for raw in ("inf", "-inf", "nan", "Infinity"):
            with self.subTest(raw=raw), self.assertRaises(argparse.ArgumentTypeError):
                subject.positive_float(raw)

    def test_a_finite_positive_duration_is_accepted(self) -> None:
        # `--minutes` is in minutes, so the value that comes back is the one that went in.
        self.assertEqual(15.0, subject.build_argument_parser().parse_args(["hidden", "--minutes", "15"]).minutes)

    def test_the_black_baseline_exemption_is_a_declaration_with_a_reason(self) -> None:
        # The exemption cannot be inferred from the windows being absent, so the command line is where
        # it is declared — and a blank declaration earns nothing, because the report quotes it.
        parsed = self.parse("report", "--black-unavailable", "  setting one rewrites the home image  ")
        self.assertEqual("setting one rewrites the home image", parsed.black_unavailable)
        self.assertIsNone(self.parse("report").black_unavailable)
        for raw in ("", "   "):
            with self.subTest(raw=raw), self.assertRaises(argparse.ArgumentTypeError):
                subject.nonblank_reason(raw)

    def test_each_windowed_phase_refuses_a_window_below_its_documented_length(self) -> None:
        cases = (
            (("hidden", "--minutes", "9"), ("hidden", "--minutes", "10"), 10.0),
            (("visible", "--arm", "A", "--minutes", "14"), ("visible", "--arm", "A", "--minutes", "15"), 15.0),
            (("pilot", "--arm", "A", "--minutes", "14"), ("pilot", "--arm", "A", "--minutes", "15"), 15.0),
            (("memory", "--minutes", "29"), ("memory", "--minutes", "30"), 30.0),
        )
        for refused, accepted, minimum in cases:
            with self.subTest(phase=refused[0]):
                self.parse_refusing(*refused)
                self.assertEqual(minimum, self.parse(*accepted).minutes)

    def test_the_default_is_the_documented_minimum(self) -> None:
        for argv, minimum in (
            (("hidden",), 10),
            (("visible", "--arm", "A"), 15),
            (("memory",), 30),
        ):
            with self.subTest(argv=argv):
                self.assertEqual(minimum, self.parse(*argv).minutes)

    def test_a_duration_the_sampling_loop_could_never_finish_is_refused(self) -> None:
        # `inf` was refused by name and the *finite* values that behave the same way were not.
        # `1e308` minutes is finite and overflows to `inf` seconds when the phase multiplies it by 60;
        # at `1e17` seconds, subtracting the 30-second interval leaves the remaining time unchanged,
        # because 30 is far below the gap between adjacent floats that far out. `sample_window_states`
        # would loop forever on either, holding the shared device while it did not return.
        for raw in ("1e308", "1e20", "1e17"):
            with self.subTest(raw=raw):
                self.parse_refusing("hidden", "--minutes", raw)
        self.assertEqual(45.0, self.parse("hidden", "--minutes", "45").minutes)

    def test_a_window_longer_than_a_phase_will_hold_the_device_is_refused(self) -> None:
        # Progress through the sampling loop is necessary and not sufficient, which is what an earlier
        # revision of this test got wrong: `1e15` minutes is finite, positive and one the loop makes
        # progress through, and it is also about two billion years of thirty-second samples on a device
        # the project treats as a shared resource. The rule is a maximum, and its size is read off
        # `docs/battery-budget.md` — a window is minutes, not hours.
        for raw in ("46", "1440", "525600", "1e15"):
            with self.subTest(raw=raw):
                self.parse_refusing("hidden", "--minutes", raw)
        self.assertEqual(45.0, self.parse("hidden", "--minutes", "45").minutes)

    def test_a_window_within_the_bound_is_still_offered(self) -> None:
        # The bound is not a single documented length: every phase's own minimum still applies and
        # anything between it and the maximum is a legitimate measurement. Forty-five minutes is the
        # ceiling, so it has to remain reachable — the longest documented criterion is thirty.
        self.assertEqual(
            (10.0, 30.0, 45.0),
            (
                self.parse("hidden", "--minutes", "10").minutes,
                self.parse("memory", "--minutes", "30").minutes,
                self.parse("memory", "--minutes", "45").minutes,
            ),
        )

    def test_the_pilot_refuses_fewer_runs_than_a_noise_floor_needs(self) -> None:
        # The refusal used to arrive from `two_sigma_noise_floor` after every requested window had
        # already been run, so `--runs 3` spent up to 45 minutes of the shared device on a run that
        # could never produce a floor. It is a command-line minimum now, like `--minutes`; `0` is
        # refused by the positive-count rule beneath it.
        for runs in ("0", "1", "2", "3"):
            with self.subTest(runs=runs):
                self.parse_refusing("pilot", "--arm", "A", "--runs", runs)
        self.assertEqual(4, self.parse("pilot", "--arm", "A").runs)
        self.assertEqual(5, self.parse("pilot", "--arm", "A", "--runs", "5").runs)

    def test_the_pilot_refuses_more_runs_than_a_phase_will_hold_the_device_for(self) -> None:
        # The lower bound was the only one, so `--runs 1000000000` was accepted and bought one billion
        # fifteen-minute windows — fifteen billion minutes on a device the project treats as a shared
        # resource, with the rate and CPU lists growing beside them. The count ended up derived from the
        # invocation bound and the shortest window, so at the floor it is exactly that many.
        for runs in ("9", "1000000000"):
            with self.subTest(runs=runs):
                self.parse_refusing("pilot", "--arm", "A", "--runs", runs)
        self.assertEqual(8, self.parse("pilot", "--arm", "A", "--runs", "8").runs)

    def test_a_pilot_run_longer_than_the_invocation_bound_is_refused(self) -> None:
        # Neither bound covers this on its own, which is why the product is checked as well:
        # `--runs 4 --minutes 45` is legal at both ends and is three hours of windows, past the two
        # hours `docs/battery-budget.md` allows an invocation to take. The check is before the first
        # window opens, so the refusal costs no device time.
        for runs, minutes in ((4, 45.0), (6, 30.0), (8, 45.0)):
            with self.subTest(runs=runs, minutes=minutes), self.assertRaises(device_layer.ProbeError) as raised:
                subject.phase_pilot(
                    MagicMock(), arm=report.Arm.LIVE, baseline=None, runs=runs, minutes=minutes, recorder=recorder()
                )
            self.assertIn("a pilot invocation will hold the device for", str(raised.exception))
        # At the bound the run is admitted and reaches its first window, which is where this stub stops.
        with (
            patch.object(subject, "phase_visible", side_effect=device_layer.ProbeError("the window ran")),
            self.assertRaises(device_layer.ProbeError) as admitted,
        ):
            subject.phase_pilot(
                MagicMock(), arm=report.Arm.LIVE, baseline=None, runs=8, minutes=15.0, recorder=recorder()
            )
        self.assertIn("the window ran", str(admitted.exception))

    def test_a_longer_window_is_still_offered(self) -> None:
        # The minimum is a floor, not a fixed length: a longer window is a legitimate measurement.
        self.assertEqual(45.0, self.parse("memory", "--minutes", "45").minutes)


class ParseApiLevelTest(unittest.TestCase):
    """Checks the SDK-level probe."""

    def test_reads_the_platform_version(self) -> None:
        self.assertEqual(36, subject.parse_api_level("36\n"))

    def test_refuses_anything_that_is_not_an_api_level(self) -> None:
        for raw in ("", "unknown\n", "36.1"):
            with self.subTest(raw=raw), self.assertRaises(device_layer.ProbeError):
                subject.parse_api_level(raw)

    def test_an_oversized_level_is_a_named_refusal_not_a_traceback(self) -> None:
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.parse_api_level(OVERSIZED_DIGITS)
        self.assertIn("not an API level", str(raised.exception))


class ParseTotalPssTest(unittest.TestCase):
    """Checks the `dumpsys meminfo` total-PSS parsing."""

    def test_reads_the_total_line(self) -> None:
        dump = "  TOTAL      26697    27689\n"
        self.assertEqual(26697, subject.parse_total_pss_kb(dump))

    def test_has_no_value_without_a_total_line(self) -> None:
        self.assertIsNone(subject.parse_total_pss_kb("  Graphics   100\n"))

    def test_an_oversized_total_is_a_refusal_rather_than_a_traceback(self) -> None:
        self.assertIsNone(subject.parse_total_pss_kb(f"  TOTAL {OVERSIZED_DIGITS}\n"))

    def test_a_total_that_would_have_to_cross_a_line_is_not_a_reading(self) -> None:
        # `\s` matches a newline, so `TOTAL` followed by `\s+` reached the *next* line for its number:
        # a truncated `TOTAL` line above any numeric line reported that line's figure as the app's
        # total PSS, which is B6 growth fabricated from a different part of the dump. The separators
        # are horizontal whitespace now, and a blank line above the field is not a field either.
        for dump in ("TOTAL\n123\n", "  TOTAL\n  45000\n", "TOTAL\n123 456\n"):
            with self.subTest(dump=dump[:14]):
                self.assertIsNone(subject.parse_total_pss_kb(dump))
        self.assertEqual(45000, subject.parse_total_pss_kb("  TOTAL   45000    46000\n"))

    def test_a_total_that_is_not_a_number_is_not_a_reading(self) -> None:
        # This is the one capture with no field name or anchor after it, so the bound alone was not
        # enough: `(?!\d)` refuses a following *digit*, and `123` of a corrupted `TOTAL 123oops` was
        # recorded as the figure. The value has to end at horizontal whitespace or the line end.
        for dump in ("TOTAL 123oops\n", "  TOTAL 45000kB\n", "TOTAL 1234.5\n"):
            with self.subTest(dump=dump[:14]):
                self.assertIsNone(subject.parse_total_pss_kb(dump))
        self.assertEqual(123, subject.parse_total_pss_kb("TOTAL 123\n"))
        self.assertEqual(123, subject.parse_total_pss_kb("  TOTAL\t123  456\n"))


class VisibleHomeStateTest(unittest.TestCase):
    """Finding 2: a visible window is the home screen, not merely a lit display."""

    def device(self) -> MagicMock:
        """Return a device that is awake, unlocked and on the launcher."""
        device = MagicMock()
        device.wake_screen.return_value = True
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
        device.read_home_in_front.return_value = True
        return device

    def test_the_keyguard_is_dismissed_and_home_is_shown_before_measuring(self) -> None:
        calls: list[str] = []
        device = self.device()
        device.wake_screen.side_effect = lambda: calls.append("wake") or True
        device.dismiss_keyguard.side_effect = lambda: calls.append("dismiss")
        device.show_home.side_effect = lambda: calls.append("home")
        subject.ensure_visible_home(device)
        self.assertEqual(["wake", "dismiss", "home"], calls)

    def test_a_locked_device_is_refused_before_the_keyguard_is_touched(self) -> None:
        # Refusing only *after* dismissing cannot tell "the device was already unlocked" from "this
        # call unlocked it": `wm dismiss-keyguard` changes a shared device that nothing in this
        # protocol puts back, so a run started on a locked device would unlock it and then pass this
        # very guard. The lock state is read first, and a locked one stops the phase untouched.
        device = self.device()
        device.read_keyguard_locked.return_value = True
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.ensure_visible_home(device)
        self.assertIn("the keyguard is showing", str(raised.exception))
        device.dismiss_keyguard.assert_not_called()
        device.show_home.assert_not_called()

    def test_a_display_that_did_not_read_back_on_is_refused(self) -> None:
        device = self.device()
        device.read_screen_on.return_value = False
        with self.assertRaises(device_layer.ProbeError):
            subject.ensure_visible_home(device)

    def test_an_unreadable_keyguard_state_is_refused(self) -> None:
        # The keyguard read is three-valued: locked, unlocked, and could not be determined. Only a
        # positive unlocked reading shows the home screen is what the window will measure — treating
        # "could not be determined" as unlocked would let a locked device have its lock screen
        # measured for the whole window, with the bound home component still matching the arm.
        device = self.device()
        device.read_keyguard_locked.return_value = None
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.ensure_visible_home(device)
        self.assertIn("could not be read", str(raised.exception))

    def test_a_display_that_could_not_be_woken_is_refused(self) -> None:
        device = self.device()
        device.wake_screen.return_value = False
        with self.assertRaises(device_layer.ProbeError):
            subject.ensure_visible_home(device)

    def test_another_package_in_front_is_refused(self) -> None:
        # `show_home()` sends KEYCODE_HOME and reports nothing about what it did. When the event is
        # dropped the display, the keyguard and the arm all still read as the visible home state,
        # and the app left in front is the surface the window would measure — a visible window whose
        # wallpaper is not the one it names.
        device = self.device()
        device.read_home_in_front.return_value = False
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.ensure_visible_home(device)
        self.assertIn("another package is in front", str(raised.exception))

    def test_an_unreadable_foreground_package_is_refused(self) -> None:
        # Three-valued, like the keyguard read: "could not be read" is not "the home screen is there".
        device = self.device()
        device.read_home_in_front.return_value = None
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.ensure_visible_home(device)
        self.assertIn("could not be read", str(raised.exception))

    def test_the_home_screen_is_read_back_until_it_is_in_front(self) -> None:
        # The launcher takes a moment to come forward, so a single reading would refuse a window that
        # is about to be valid. The readback is bounded the way the service-log confirmations are.
        device = self.device()
        device.read_home_in_front.side_effect = [False, None, True]
        with patch.object(subject.time, "sleep") as sleep:
            subject.ensure_visible_home(device)
        self.assertEqual(3, device.read_home_in_front.call_count)
        self.assertEqual(2, sleep.call_count)

    def test_the_readback_is_bounded_and_refuses_once_it_runs_out(self) -> None:
        device = self.device()
        device.read_home_in_front.return_value = False
        with patch.object(subject.time, "sleep") as sleep, self.assertRaises(device_layer.ProbeError):
            subject.ensure_visible_home(device)
        self.assertEqual(subject.HOME_CONFIRM_ATTEMPTS, device.read_home_in_front.call_count)
        self.assertEqual(subject.HOME_CONFIRM_ATTEMPTS - 1, sleep.call_count)


def _all_set_except(missing: str) -> Callable[[object, str, str], int | None]:
    """Return a settings reader that answers every key but `missing`, which it cannot read."""

    def read(_device: object, _namespace: str, key: str) -> int | None:
        return None if key == missing else 1

    return read


def _counter_window(start_uah: int, end_uah: int) -> subject.Window:
    """Build one window whose charge counter moved from `start_uah` to `end_uah` over 900 s."""

    def sample(seconds: float, uah: int) -> subject.BatterySample:
        return subject.BatterySample(
            timestamp=seconds,
            charge_counter_uah=uah,
            level_pct=90,
            voltage_mv=4000,
            process_cpu_seconds=None,
            total_pss_kb=45_000,
        )

    return subject.Window(
        start_sample=sample(0.0, start_uah), end_sample=sample(900.0, end_uah), duration_seconds=900.0
    )


class WindowDischargeRateTest(unittest.TestCase):
    """
    Finding 1: a pack that gained charge across a window is not a discharge measurement.

    The report's own half of this rule — a state file carrying a negative rate — is
    `NegativeDischargeRecordTest` in `test_battery_report.py`.
    """

    def test_a_rising_charge_counter_is_refused(self) -> None:
        window = _counter_window(3_339_000, 3_501_000)
        with self.assertRaises(device_layer.ProbeError) as raised:
            _ = window.discharge_rate_ua
        self.assertIn("gained charge", str(raised.exception))

    def test_a_falling_charge_counter_is_still_a_measurement(self) -> None:
        self.assertGreater(_counter_window(3_501_000, 3_339_000).discharge_rate_ua, 0.0)


class RequiredCaptureTest(unittest.TestCase):
    """Finding 3: a setting that cannot be read cannot be restored, so it must not be pinned."""

    def test_an_unreadable_pinned_setting_stops_the_phase(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = True
        with (
            patch.object(subject, "read_int_setting", return_value=None),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.DeviceStateSnapshot.capture(device)
        self.assertIn("could not be read before the phase", str(raised.exception))

    def test_each_pinned_setting_is_required_not_just_the_first(self) -> None:
        required = ("screen_brightness", "screen_brightness_mode", "screen_off_timeout", "airplane_mode_on")
        for missing in required:
            device = MagicMock()
            device.run_adb.return_value = b""
            device.read_screen_on.return_value = True
            with (
                self.subTest(missing=missing),
                patch.object(subject, "read_int_setting", side_effect=_all_set_except(missing)),
                self.assertRaises(device_layer.ProbeError),
            ):
                subject.DeviceStateSnapshot.capture(device)

    def test_the_stay_awake_setting_may_still_be_unset(self) -> None:
        # It is legitimately unset on a device that never had it, and an unset value is captured as
        # None and restored by deleting the key rather than by writing one back. The device says so
        # with its own `null`, which is what this now feeds it: an unreadable answer is a different
        # reading and is refused below, so faking one here would have pinned the wrong behaviour.
        device = MagicMock()
        device.run_adb.side_effect = lambda argv: b"null" if argv[-1] == "stay_on_while_plugged_in" else b"1"
        device.read_screen_on.return_value = True
        snapshot = subject.DeviceStateSnapshot.capture(device)
        self.assertEqual(1, snapshot.brightness)
        self.assertIsNone(snapshot.stay_on)

    def test_an_unreadable_stay_awake_reading_is_refused_not_read_as_unset(self) -> None:
        # `read_int_setting` answers None both for the unset text and for text it cannot parse, and
        # the capture took that None as proof the setting had never been set. A settings service that
        # did not answer therefore had its value captured as "unset", and the restore *deleted* the
        # key — losing a value the device did hold and reporting a clean restore of a device it left
        # changed. The unset reading is the device's own `null` and nothing else, so anything else
        # stops the phase before it pins anything, as the four required settings already do.
        device = MagicMock()
        device.run_adb.side_effect = lambda argv: (
            b"Settings provider is dead" if argv[-1] == "stay_on_while_plugged_in" else b"1"
        )
        device.read_screen_on.return_value = True
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.DeviceStateSnapshot.capture(device)
        self.assertIn("stay_on_while_plugged_in", str(raised.exception))
        self.assertIn("neither a decimal integer nor", str(raised.exception))


class MonotonicTimestampTest(unittest.TestCase):
    """Finding 4: a window's duration comes from a clock that cannot be stepped."""

    def test_a_measurement_timestamp_is_not_taken_from_the_wall_clock(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = REALISTIC_BATTERY_DUMP.encode()
        with patch.object(subject.time, "time", side_effect=AssertionError("the wall clock was used")):
            sample = subject.take_battery_sample(device, None, 100)
        self.assertIsInstance(sample.timestamp, float)

    def test_two_samples_are_ordered_on_the_monotonic_clock(self) -> None:
        # The ordering alone holds on any clock that does not go backwards, so it did not pin what the
        # name claims. The wall clock is refused here too, which is what makes the ordering an
        # ordering *on the monotonic clock*.
        device = MagicMock()
        device.run_adb.return_value = REALISTIC_BATTERY_DUMP.encode()
        with patch.object(subject.time, "time", side_effect=AssertionError("the wall clock was used")):
            first = subject.take_battery_sample(device, None, 100)
            second = subject.take_battery_sample(device, None, 100)
        self.assertLessEqual(first.timestamp, second.timestamp)


class WakeLockUidMatchTest(unittest.TestCase):
    """Finding 5: one uid must not be matched by a prefix of another."""

    def test_a_shorter_uid_is_not_matched_inside_a_longer_one(self) -> None:
        # This dump holds a lock for uid 10026, whose text also contains `uid=1002`. Counting by
        # substring would attribute that lock to an app whose uid is 1002 and report a B2 failure
        # the wallpaper did not cause.
        self.assertEqual(0, subject.parse_wakelock_count(POWER_DUMP, 1002))
        self.assertEqual(0, subject.parse_wakelock_count(POWER_DUMP, 101))

    def test_the_exact_uid_is_still_counted(self) -> None:
        self.assertEqual(1, subject.parse_wakelock_count(POWER_DUMP, 10026))
        self.assertEqual(1, subject.parse_wakelock_count(POWER_DUMP, 1013))


class LiveComponentIdentityTest(unittest.TestCase):
    """Finding 7: the live arm is the whole component, not a substring of one."""

    def test_the_wrapper_is_unwrapped_to_package_slash_class(self) -> None:
        self.assertEqual(device_layer.SERVICE_NAME, subject.component_name(SERVICE_COMPONENT))
        self.assertEqual(device_layer.SERVICE_NAME, subject.component_name(device_layer.SERVICE_NAME))

    def test_a_look_alike_service_from_another_package_is_not_arm_a(self) -> None:
        look_alike = "ComponentInfo{com.example.other/com.example.AstronomicalClocksWallpaperService}"
        device = MagicMock()
        device.serial = "serial"
        with (
            patch.object(device_layer, "read_wallpaper_component", return_value=look_alike),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.verify_arm(device, report.Arm.LIVE, None)
        self.assertIn("com.example.other", str(raised.exception))

    def test_the_exact_service_is_arm_a(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        with patch.object(device_layer, "read_wallpaper_component", return_value=SERVICE_COMPONENT):
            self.assertEqual(SERVICE_COMPONENT, subject.verify_arm(device, report.Arm.LIVE, None))

    def test_arm_b_admits_the_platform_static_engine(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        with patch.object(device_layer, "read_wallpaper_component", return_value=STOCK_COMPONENT):
            self.assertEqual(
                STOCK_COMPONENT,
                subject.verify_arm(device, report.Arm.BASELINE, report.Baseline.STOCK),
            )

    def test_arm_b_refuses_a_live_service_from_another_package(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        look_alike = "ComponentInfo{com.example.other/com.example.other.LiveWallpaperService}"
        with (
            patch.object(device_layer, "read_wallpaper_component", return_value=look_alike),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.verify_arm(device, report.Arm.BASELINE, report.Baseline.STOCK)
        self.assertIn("com.example.other", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
