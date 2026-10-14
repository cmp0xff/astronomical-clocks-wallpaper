#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the battery protocol's parsing, statistics, arm guard, and report."""

import argparse
import contextlib
import io
import subprocess
import tempfile
import unittest
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from unittest.mock import MagicMock, patch

import battery_protocol as subject
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


def visible_record(  # noqa: PLR0913 - the arm, its readings, and the window shape are all its inputs
    arm: str,
    rate: float,
    *,
    cpu: float | None = None,
    pilot: bool = False,
    baseline: str | None = None,
    duration: float = 900.0,
) -> dict[str, object]:
    """Build one visible-window record as the state file would carry it."""
    return {
        "phase": "visible",
        "arm": arm,
        "baseline": baseline,
        "discharge_rate_ua": rate,
        "cpu_pct": cpu,
        "pilot": pilot,
        "duration_seconds": duration,
        "revision": "rev",
        "api_level": 36,
    }


def pairs(count: int, live_rate: float, base_rate: float, *, cpu: float = 1.0) -> list[dict[str, object]]:
    """Build `count` whole A/B pairs in the order an alternation records them."""
    records: list[dict[str, object]] = []
    for _ in range(count):
        records.append(visible_record("A", live_rate, cpu=cpu))
        records.append(visible_record("B", base_rate, baseline="stock"))
    return records


# The four `powered:` fields `dumpsys battery` prints, all false: the device is on its own battery.
# Every source is required, so a phase's power read is stubbed with the whole reading rather than one
# line of it.
ON_BATTERY_POWER_LINES = (
    "AC powered: false",
    "USB powered: false",
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
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
        ):
            record = subject.phase_visible(
                device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder()
            )
        self.assertEqual(250, record.values["clk_tck"])
        self.assertEqual(subject.POWER_SOURCE_DEVICE, record.values["power_source"])

    def test_a_device_without_a_tick_rate_fails_before_the_window(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = None
        with (
            patch.object(subject, "run_window") as window,
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
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
        # The refusal above must not turn a device that genuinely holds nothing into an error.
        empty = POWER_DUMP.replace("size=2", "size=0").replace("_WAKE_LOCK", "_NOT_A_LOCK")
        self.assertEqual(0, subject.parse_wakelock_count(empty, 10026))

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
        with self.assertRaises(device_layer.ProbeError):
            subject.apply_power_mode(device)

    def test_a_device_on_its_own_battery_is_not_put_under_the_override(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = (
            b"  AC powered: false\n  USB powered: false\n  Wireless powered: false\n  Dock powered: false\n"
        )
        with patch.object(subject, "simulate_unplug") as unplug:
            mode = subject.apply_power_mode(device)
        self.assertEqual(subject.POWER_SOURCE_DEVICE, mode)
        unplug.assert_not_called()

    def test_a_charging_device_is_simulated(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = (
            b"  AC powered: false\n  USB powered: true\n  Wireless powered: false\n  Dock powered: false\n"
        )
        with patch.object(subject, "simulate_unplug") as unplug:
            mode = subject.apply_power_mode(device)
        self.assertEqual(subject.POWER_SOURCE_SIMULATED, mode)
        unplug.assert_called_once()

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
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True, False))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        self.assertIn("charger connected inside the window", str(raised.exception))

    def test_a_visible_window_whose_display_went_off_is_refused(self) -> None:
        device = self.device_on_the_home_screen()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True, False), (True, True))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
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
            patch.object(subject, "run_window", return_value=(gained, (True,), (True,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
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
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (False, False), (False, True))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
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
            patch.object(subject, "sample_window_states", return_value=([True, True], [True, False])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_memory(device, minutes=1.0, recorder=recorder())
        self.assertIn("charger connected inside the window", str(raised.exception))


class TCriticalTest(unittest.TestCase):
    """Checks the degrees-of-freedom-indexed t table and its tail."""

    def test_table_is_indexed_by_degrees_of_freedom(self) -> None:
        self.assertAlmostEqual(12.706, subject.t_critical_95(1))
        self.assertAlmostEqual(4.303, subject.t_critical_95(2))
        self.assertAlmostEqual(3.182, subject.t_critical_95(3))
        self.assertAlmostEqual(2.776, subject.t_critical_95(4))

    def test_table_reaches_thirty_degrees_of_freedom(self) -> None:
        self.assertAlmostEqual(2.042, subject.t_critical_95(30))

    def test_the_tail_is_expanded_rather_than_replaced_by_the_normal_value(self) -> None:
        # The two-sided 95% t at 31 degrees of freedom is 2.0395; returning the normal 1.960 there
        # would understate the interval by about 4%.
        self.assertAlmostEqual(2.0395, subject.t_critical_95(31), places=3)
        self.assertAlmostEqual(2.0003, subject.t_critical_95(60), places=3)
        self.assertGreater(subject.t_critical_95(31), subject.NORMAL_T_CRITICAL_95)

    def test_the_tail_tends_to_the_normal_value(self) -> None:
        self.assertAlmostEqual(subject.NORMAL_T_CRITICAL_95, subject.t_critical_95(100_000), places=3)
        self.assertGreater(subject.t_critical_95(1000), subject.NORMAL_T_CRITICAL_95)

    def test_two_and_three_pairs_do_not_borrow_the_four_pair_value(self) -> None:
        # The old two-branch lookup returned 3.182 for every n below 5, which understates the
        # interval for two or three pairs.
        self.assertNotAlmostEqual(3.182, subject.t_critical_95(2))
        self.assertNotAlmostEqual(3.182, subject.t_critical_95(1))

    def test_refuses_a_sample_with_no_degrees_of_freedom(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.t_critical_95(0)


class PairedStatisticsTest(unittest.TestCase):
    """Checks the paired relative increase and its 95% interval."""

    def test_identical_pairs_have_no_increase(self) -> None:
        rates = [100.0, 102.0, 99.0, 101.0]
        statistics = subject.paired_statistics(rates, rates)
        self.assertAlmostEqual(0.0, statistics.relative_delta_pct)
        self.assertAlmostEqual(0.0, statistics.ci_95_upper_pct)
        self.assertEqual(3, statistics.degrees_of_freedom)

    def test_uniform_increase_has_a_zero_width_interval(self) -> None:
        statistics = subject.paired_statistics([100.0] * 4, [103.0] * 4)
        self.assertAlmostEqual(3.0, statistics.relative_delta_pct)
        self.assertAlmostEqual(3.0, statistics.ci_95_upper_pct)

    def test_the_bound_uses_the_critical_value_for_the_actual_sample_count(self) -> None:
        baseline = [100.0] * 5
        treatment = [100.0, 100.0, 100.0, 100.0, 120.0]
        statistics = subject.paired_statistics(baseline, treatment)
        self.assertEqual(4, statistics.degrees_of_freedom)
        self.assertAlmostEqual(2.776, statistics.t_critical)

    def test_refuses_fewer_than_four_pairs(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.paired_statistics([100.0] * 3, [101.0] * 3)

    def test_refuses_mismatched_arms(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.paired_statistics([100.0] * 4, [101.0] * 5)

    def test_refuses_a_non_positive_baseline(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.paired_statistics([0.0] * 4, [1.0] * 4)


class NoiseFloorTest(unittest.TestCase):
    """Checks the pilot's mean, sigma, and two-sigma half-width."""

    def test_reports_mean_sigma_and_two_sigma(self) -> None:
        mean, deviation, half_width = subject.two_sigma_noise_floor([100.0, 102.0, 98.0, 100.0])
        self.assertAlmostEqual(100.0, mean)
        self.assertAlmostEqual(1.632993161855452, deviation)
        self.assertAlmostEqual(2 * deviation, half_width)

    def test_refuses_too_few_windows(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.two_sigma_noise_floor([100.0, 101.0, 99.0])


class ArmGuardTest(unittest.TestCase):
    """Checks that a window is refused unless the device matches the requested arm."""

    def guard(
        self,
        pixels: tuple[int, int, bytes],
        component: str,
        arm: subject.Arm,
        baseline: subject.Baseline | None,
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
        arm: subject.Arm,
        baseline: subject.Baseline | None,
    ) -> str:
        """Run the guard expecting a refusal and return the reason it gave."""
        accepted, error = self.guard(pixels, component, arm, baseline)
        self.assertIsNone(accepted)
        if error is None:
            self.fail("the guard accepted a window it should have refused")
        return error

    def test_live_arm_accepts_the_service_component(self) -> None:
        accepted, error = self.guard(WHITE_FRAME, SERVICE_COMPONENT, subject.Arm.LIVE, None)
        self.assertIsNone(error)
        self.assertEqual(SERVICE_COMPONENT, accepted)

    def test_live_arm_refuses_a_static_wallpaper(self) -> None:
        self.assertIn("arm A was requested", self.refusal(WHITE_FRAME, STOCK_COMPONENT, subject.Arm.LIVE, None))

    def test_baseline_arm_refuses_a_bound_live_service(self) -> None:
        self.assertIn(
            "arm B was requested",
            self.refusal(WHITE_FRAME, SERVICE_COMPONENT, subject.Arm.BASELINE, subject.Baseline.STOCK),
        )

    def test_baseline_arm_requires_a_baseline_name(self) -> None:
        self.assertIn("needs --baseline", self.refusal(WHITE_FRAME, STOCK_COMPONENT, subject.Arm.BASELINE, None))

    def test_both_baselines_are_admitted_on_the_component_alone(self) -> None:
        # The screen's luminance does not admit a baseline: the device's own stock wallpaper is a
        # dark image, so a brightness gate would refuse valid stock windows without separating the
        # two static baselines. Both are admitted here and the luminance is recorded instead.
        for baseline in (subject.Baseline.STOCK, subject.Baseline.BLACK):
            for pixels in (WHITE_FRAME, BLACK_FRAME):
                with self.subTest(baseline=baseline, luma=pixels is WHITE_FRAME):
                    accepted, error = self.guard(pixels, STOCK_COMPONENT, subject.Arm.BASELINE, baseline)
                    self.assertIsNone(error)
                    self.assertEqual(STOCK_COMPONENT, accepted)

    def test_baseline_arm_refuses_another_package_s_live_wallpaper(self) -> None:
        # Arm B admitted "any component that is not this app's service", which a third-party live
        # wallpaper satisfies. Such a window would be recorded against the stock or black arm while
        # draining whatever that live service costs, so the baseline it claims is not the baseline
        # it measures.
        other_live = "ComponentInfo{com.example.other/com.example.other.LiveWallpaperService}"
        refused = self.refusal(WHITE_FRAME, other_live, subject.Arm.BASELINE, subject.Baseline.STOCK)
        self.assertIn("com.example.other", refused)
        self.assertIn("not a baseline", refused)

    def test_baseline_arm_refuses_a_static_component_from_another_package(self) -> None:
        # Naming the platform engine is stronger than "anything that is not ours": a component that
        # looks static but belongs elsewhere is still not the baseline the criterion defines.
        other_static = "ComponentInfo{com.example.launcher/com.example.launcher.ImageWallpaper}"
        for baseline in (subject.Baseline.STOCK, subject.Baseline.BLACK):
            with self.subTest(baseline=baseline):
                refused = self.refusal(BLACK_FRAME, other_static, subject.Arm.BASELINE, baseline)
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
            conditions = subject.arm_conditions(device, subject.Arm.BASELINE, subject.Baseline.STOCK)
        self.assertEqual(0.0, conditions["median_luma"])
        self.assertIn("not gated", str(conditions["median_luma_note"]))

    def test_median_luma_reads_the_screen(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"screencap"
        for pixels, expected in ((BLACK_FRAME, 0.0), (WHITE_FRAME, 255.0)):
            with self.subTest(expected=expected), patch.object(device_layer, "decode_frame", return_value=pixels):
                self.assertAlmostEqual(expected, subject.median_luma(device))


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
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        guard.assert_called_once()
        window.assert_not_called()

    def test_the_guard_is_consulted_for_a_baseline_arm_too(self) -> None:
        device = self.device()
        with (
            patch.object(subject, "verify_arm") as guard,
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "static", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
        ):
            subject.phase_visible(
                device, arm=subject.Arm.BASELINE, baseline=subject.Baseline.STOCK, minutes=1.0, recorder=recorder()
            )
        guard.assert_called_once()
        self.assertIs(subject.Arm.BASELINE, guard.call_args.args[1])


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
        self.assertIs(subject.Arm.LIVE, guard.call_args.args[1])
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
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
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
        lines = [verdict.line for verdict in subject.report_lines(recorded)]
        self.assertTrue(any(line.startswith("DEVICE RESTORE: FAIL") for line in lines))
        self.assertEqual(1, subject.report_exit_code(subject.report_lines(recorded)))

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
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
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
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True, True), (True, True))),
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
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True, False))),
            patch.object(subject, "undo_phase_changes", return_value=["screen_brightness did not read back as 128"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_visible(
                device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=subject.Recorder(state_dir)
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
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "take_battery_sample", side_effect=[unparsable, unparsable]),
            patch.object(subject, "sample_window_states", return_value=([True], [True])),
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
            patch.object(subject, "sample_window_states", return_value=([True, False], [True, True])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_memory(device, minutes=1.0, recorder=recorder())
        guard.assert_called_once()


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
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=0.5), [False, False], [True, True])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            record = subject.phase_hidden(device, minutes=1.0, recorder=recorder())
        self.assertEqual("hidden", record.phase)
        self.assertEqual(0.5, record.values["cpu_pct"])
        self.assertEqual([False, False], record.values["screen_on_samples"])

    def test_a_window_that_left_the_display_on_is_refused(self) -> None:
        device = self.device()
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            patch.object(subject, "run_window", return_value=(_window(cpu=0.5), (False, True), (True, True))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.phase_hidden(device, minutes=1.0, recorder=recorder())
        self.assertIn("the display was not off for the whole window", str(raised.exception))


class WindowValidityTest(unittest.TestCase):
    """Checks the window itself refuses a measurement that is not one."""

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
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())
        # The capture precedes the wake, and the restore still runs even though the arm was refused
        # after the display had already been woken.
        self.assertEqual(["capture", "wake", "restore"], events)

    def test_a_phase_that_started_with_the_display_off_records_that(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = False
        with (
            patch.object(subject, "require_int_setting", return_value=128),
            patch.object(subject, "read_int_setting", return_value=None),
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
        with patch.object(subject, "read_int_setting", side_effect=subprocess.CalledProcessError(1, "settings get")):
            problems = snapshot.restore()
        self.assertTrue(any("stay-awake restore could not be verified" in problem for problem in problems))
        device.wake_screen.assert_called_once()

    def test_the_settings_restore_is_not_lost_when_only_the_reset_fails(self) -> None:
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = []
        self.assertEqual([], subject.undo_phase_changes(device, snapshot, simulated=True))

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


def full_pass_records() -> list[dict[str, object]]:
    """Build a record set in which every criterion is measured and passes."""
    records: list[dict[str, object]] = [
        {"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 0, "revision": "rev", "api_level": 36},
        {"phase": "hidden", "cpu_pct": 0.05, "duration_seconds": 604.0, "revision": "rev", "api_level": 36},
        {"phase": "memory", "pss_growth_kb": 992, "duration_seconds": 1800.0, "revision": "rev", "api_level": 36},
    ]
    records.extend(pairs(4, 1000.0, 1000.0))
    return records


class ReportTest(unittest.TestCase):
    """Checks the aggregate pass/fail lines against the committed thresholds."""

    def lines(self, records: Sequence[Mapping[str, object]]) -> list[str]:
        """Render the report's verdicts to their text lines."""
        return [verdict.line for verdict in subject.report_lines(records)]

    def test_every_budget_is_not_measured_without_records(self) -> None:
        lines = self.lines([])
        for label in (
            "B2 wake locks",
            "B2 alarms",
            "B2 jobs",
            "B3 hidden CPU",
            "B4 vs stock",
            "B4 vs black",
            "B5 visible CPU",
            "B6 PSS growth",
        ):
            self.assertTrue(any(line.startswith(label) and "NOT MEASURED" in line for line in lines))

    def test_b1_is_declared_out_of_scope(self) -> None:
        verdicts = subject.report_lines([])
        self.assertTrue(
            any(
                verdict.label == "B1 hidden frames"
                and verdict.status is subject.VerdictStatus.OUT_OF_SCOPE
                and "NOT MEASURED HERE" in verdict.line
                for verdict in verdicts
            )
        )

    def test_zero_wake_locks_alarms_and_jobs_pass(self) -> None:
        records = [{"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 0, "revision": "rev", "api_level": 36}]
        lines = self.lines(records)
        for label in ("B2 wake locks", "B2 alarms", "B2 jobs"):
            self.assertTrue(any(line.startswith(label) and "PASS" in line for line in lines))

    def test_a_held_wake_lock_fails(self) -> None:
        records = [{"phase": "resources", "wake_locks": 1, "alarms": 0, "jobs": 0, "revision": "rev", "api_level": 36}]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks") and "FAIL" in line for line in lines))

    def test_a_held_job_fails(self) -> None:
        records = [{"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 2, "revision": "rev", "api_level": 36}]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 jobs") and "FAIL" in line for line in lines))

    def test_the_b2_census_is_a_maximum_across_every_resources_record(self) -> None:
        # The state file is append-only, so a later clean rerun must not hide an earlier dirty one.
        records = [
            {"phase": "resources", "wake_locks": 3, "alarms": 0, "jobs": 1, "revision": "rev", "api_level": 36},
            {"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 0, "revision": "rev", "api_level": 36},
        ]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks") and "FAIL" in line for line in lines))
        self.assertTrue(any(line.startswith("B2 jobs") and "FAIL" in line for line in lines))

    def test_hidden_cpu_is_checked_against_its_budget(self) -> None:
        under = [{"phase": "hidden", "cpu_pct": 0.05, "duration_seconds": 604.0, "revision": "rev", "api_level": 36}]
        over = [{"phase": "hidden", "cpu_pct": 5.4, "duration_seconds": 604.0, "revision": "rev", "api_level": 36}]
        self.assertTrue(any("PASS" in line for line in self.lines(under)))
        self.assertTrue(any("FAIL" in line for line in self.lines(over)))

    def test_matched_arms_within_the_budget_pass(self) -> None:
        records = [
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0, baseline="stock"),
        ] * 4
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 vs stock CI upper bound") and "PASS" in line for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU") and "PASS" in line for line in lines))

    def test_the_interval_names_the_difference_sd_that_sizes_a_future_run(self) -> None:
        # The CI margin is t * sd / sqrt(n), so the sd is the number a later run's pair count is
        # computed from; a report that omitted it would leave n to be guessed.
        records: list[dict[str, object]] = []
        for live, base in ((1200.0, 1000.0), (1400.0, 1000.0), (1600.0, 1000.0), (1800.0, 1000.0)):
            records.append(visible_record("A", live, cpu=1.0))
            records.append(visible_record("B", base, baseline="stock"))
        paired = subject.alternation_pairs(records)
        statistics = subject.paired_statistics(
            [subject.window_rate(base) for _, base in paired],
            [subject.window_rate(live) for live, _ in paired],
        )
        # Per-pair differences 200, 400, 600 and 800 uA: mean 500, sample sd 258.198 uA.
        self.assertAlmostEqual(258.199, statistics.standard_deviation, places=2)
        line = next(verdict.line for verdict in subject.report_lines(records) if verdict.label == "B4 vs stock")
        self.assertIn("sigma_d", line)
        self.assertIn("of the baseline", line)

    def test_a_large_increase_fails_both_b4_lines(self) -> None:
        lines = self.lines(pairs(4, 1200.0, 1000.0))
        self.assertTrue(any(line.startswith("B4 vs stock point estimate") and "FAIL" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 vs stock CI upper bound") and "FAIL" in line for line in lines))

    def test_visible_cpu_over_its_budget_fails(self) -> None:
        lines = self.lines(pairs(4, 1000.0, 1000.0, cpu=6.4))
        self.assertTrue(any(line.startswith("B5 visible CPU") and "FAIL" in line for line in lines))

    def test_pilot_windows_are_excluded_from_the_final_comparison(self) -> None:
        records = pairs(4, 1000.0, 1000.0)
        records.append(visible_record("A", 99999.0, pilot=True))
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines))

    def test_a_silent_discharge_channel_is_reported_not_crashed(self) -> None:
        # The target device's fuel gauge reports a constant counter, so every window's rate is zero.
        # B4 is then unmeasurable, and the criteria that *were* measured must still be reported.
        records = [visible_record("A", 0.0, cpu=1.0), visible_record("B", 0.0, baseline="stock")] * 4
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU") for line in lines))
        self.assertFalse(any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines))

    def test_too_few_pairs_leaves_b4_unmeasured(self) -> None:
        lines = self.lines(pairs(3, 1000.0, 1000.0))
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))

    def test_pss_growth_is_checked_against_its_budget(self) -> None:
        records = [
            {"phase": "memory", "pss_growth_kb": 992, "duration_seconds": 1800.0, "revision": "rev", "api_level": 36}
        ]
        self.assertTrue(any(line.startswith("B6 PSS growth") and "PASS" in line for line in self.lines(records)))

    def test_a_six_second_window_fails_and_does_not_qualify(self) -> None:
        # The CLI refuses `--minutes 0.1`, but the report reads a state file it did not write: a window
        # that ran for six seconds is not the fifteen minutes B4 is defined over, so it is named and
        # dropped rather than averaged in beside real ones.
        records = [
            visible_record("A", 1000.0, cpu=1.0, duration=6.0),
            visible_record("B", 1000.0, baseline="stock", duration=6.0),
        ] * 4
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("WINDOW DURATIONS: FAIL") for line in lines))
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
        self.assertFalse(any(line.startswith("B4 vs stock point estimate") for line in lines))

    def test_a_record_without_a_recorded_duration_is_not_measurable_either(self) -> None:
        records = [{"phase": "hidden", "cpu_pct": 0.05, "revision": "rev", "api_level": 36}]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("WINDOW DURATIONS: FAIL") for line in lines))
        self.assertTrue(any(line.startswith("B3 hidden CPU") and "NOT MEASURED" in line for line in lines))

    def test_a_window_inside_the_length_tolerance_still_qualifies(self) -> None:
        # A 15-minute window records a few seconds either side of 900s; that jitter is not a short
        # window, and refusing it would fail every real run.
        records = [
            visible_record("A", 1000.0, cpu=1.0, duration=902.0),
            visible_record("B", 1000.0, baseline="stock", duration=898.0),
        ] * 4
        lines = self.lines(records)
        self.assertFalse(any(line.startswith("WINDOW DURATIONS") for line in lines))
        self.assertTrue(any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines))

    def test_an_increase_between_the_provisional_and_committed_threshold_passes(self) -> None:
        # The committed B4 threshold is 12.0123%, raised from the 5% provisional by the pilot's own
        # noise floor, and every other B4 case in this file sits at 0% or above 20% — so a regression
        # that put the provisional value back would pass the whole suite. +8% discriminates.
        self.assertEqual(12.0123, subject.FINAL_MAX_VISIBLE_DELTA_PCT)
        statuses = {(verdict.label, verdict.status) for verdict in subject.report_lines(pairs(4, 1080.0, 1000.0))}
        self.assertIn(("B4 vs stock point estimate", subject.VerdictStatus.PASS), statuses)
        self.assertIn(("B4 vs stock CI upper bound", subject.VerdictStatus.PASS), statuses)

    def test_a_sequence_that_is_not_an_ab_alternation_is_refused(self) -> None:
        # The document names this refusal as the reason the pairing means anything: pairing the nth
        # baseline with the nth live window by file order would pair windows taken under different
        # conditions, so a sequence that is not an alternation has no comparison in it.
        records = [
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0, baseline="stock"),
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0, baseline="stock"),
            visible_record("B", 1000.0, baseline="stock"),
        ]
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.alternation_pairs(records)
        self.assertIn("not an A/B alternation", str(raised.exception))
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4/B5") and "NOT MEASURED" in line for line in lines))

    def test_a_live_window_with_no_cpu_reading_leaves_b5_unmeasured(self) -> None:
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        records[0]["cpu_pct"] = None
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines))

    def test_a_window_with_no_discharge_rate_leaves_b4_unmeasured(self) -> None:
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        del records[1]["discharge_rate_ua"]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))

    def test_a_non_finite_window_length_is_not_a_long_window(self) -> None:
        # `inf` and `nan` satisfy the `isinstance` the report checks and neither is below the minimum,
        # so a hand-edited or corrupt record used to be averaged in as a window of the documented
        # length. The CLI refuses both by name; the report is fed a file it did not write, so it does
        # too.
        for duration in (float("inf"), float("nan"), float("-inf")):
            with self.subTest(duration=duration):
                record = visible_record("A", 1000.0, cpu=1.0, duration=duration)
                self.assertIsNotNone(subject.short_window_reason(record))
                lines = self.lines([record])
                self.assertTrue(any(line.startswith("WINDOW DURATIONS: FAIL") for line in lines))

    def test_a_stock_only_run_reports_the_black_arm_as_unmeasured(self) -> None:
        # One block per documented baseline, not one per baseline the state file happens to carry: a
        # stock-only set used to read as a complete verdict with the secondary baseline simply absent.
        # And the absence is *unmeasured*, not exempt: a run that never attempted that arm leaves the
        # same state file as one on a device that cannot set a static wallpaper, so the exemption has
        # to be declared rather than inferred from the missing windows.
        lines = self.lines(full_pass_records())
        self.assertTrue(any(line.startswith("B4 vs black") and "NOT MEASURED" in line for line in lines))
        self.assertFalse(any("DEVICE-LIMITED" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 vs stock CI upper bound") and "PASS" in line for line in lines))

    def test_a_declared_device_limit_exempts_the_black_arm_and_quotes_the_reason(self) -> None:
        records = full_pass_records()
        verdicts = subject.report_lines(records, black_unavailable="the build has no root")
        line = next(verdict.line for verdict in verdicts if verdict.label == "B4 vs black")
        self.assertIn("DEVICE-LIMITED", line)
        self.assertIn("the build has no root", line)
        self.assertEqual(0, subject.report_exit_code(verdicts))

    def test_an_undeclared_black_baseline_gates_the_report(self) -> None:
        verdicts = subject.report_lines(full_pass_records())
        self.assertTrue(
            any(
                verdict.label == "B4 vs black" and verdict.status is subject.VerdictStatus.NOT_MEASURED
                for verdict in verdicts
            )
        )
        self.assertEqual(1, subject.report_exit_code(verdicts))

    def test_a_black_only_run_leaves_the_gated_comparison_unmeasured(self) -> None:
        records = [
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0, baseline="black"),
        ] * 4
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 vs black CI upper bound") for line in lines))


class ReportExitCodeTest(unittest.TestCase):
    """Finding 5: the exit status comes from the verdicts, so a printed FAIL is non-zero."""

    def test_a_fully_measured_passing_report_exits_zero(self) -> None:
        verdicts = subject.report_lines(full_pass_records(), black_unavailable="the build has no root")
        self.assertEqual(0, subject.report_exit_code(verdicts))

    def test_is_failure_agrees_with_the_exit_rule_on_every_status(self) -> None:
        # `is_failure` is the type's own answer to "should the report fail", and the exit code is built
        # from it: a status the two disagreed about would be a printed verdict whose text and exit
        # status parted company.
        for status in subject.VerdictStatus:
            verdict = subject.Verdict("a criterion", status, f"a criterion: {status.value}")
            self.assertEqual(
                subject.report_exit_code([verdict]),
                1 if verdict.is_failure else 0,
                msg=status,
            )
        self.assertEqual(1, subject.report_exit_code([subject.Verdict("c", subject.VerdictStatus.NOT_MEASURED, "c")]))

    def test_a_fail_exits_non_zero(self) -> None:
        verdicts = subject.report_lines(pairs(4, 1200.0, 1000.0))
        self.assertEqual(1, subject.report_exit_code(verdicts))

    def test_an_unmeasured_criterion_exits_non_zero(self) -> None:
        self.assertEqual(1, subject.report_exit_code(subject.report_lines([])))

    def test_a_lone_out_of_scope_criterion_is_not_a_failure(self) -> None:
        verdicts = [subject.Verdict("B1 hidden frames", subject.VerdictStatus.OUT_OF_SCOPE, "B1: not measured here")]
        self.assertEqual(0, subject.report_exit_code(verdicts))

    def test_a_device_limited_criterion_is_recorded_but_does_not_gate(self) -> None:
        # The black baseline is required only where the device permits setting one, so its absence is
        # printed and carried by the verdict list without turning every run on this device into a
        # failure. It is emphatically not reported as a pass, which is what a silent omission did.
        verdicts = [
            subject.Verdict("B4 vs black", subject.VerdictStatus.DEVICE_LIMITED, "B4 vs black: NOT MEASURED"),
            subject.Verdict("B4 vs stock", subject.VerdictStatus.PASS, "B4 vs stock: PASS"),
        ]
        self.assertEqual(0, subject.report_exit_code(verdicts))
        self.assertFalse(verdicts[0].is_failure)
        self.assertIsNot(subject.VerdictStatus.PASS, verdicts[0].status)
        self.assertIsNot(subject.VerdictStatus.NOT_MEASURED, verdicts[0].status)

    def test_a_stock_only_full_pass_exits_zero_once_the_black_arm_is_declared(self) -> None:
        verdicts = subject.report_lines(full_pass_records(), black_unavailable="the build has no root")
        self.assertEqual(0, subject.report_exit_code(verdicts))
        self.assertTrue(
            any(
                verdict.label == "B4 vs black" and verdict.status is subject.VerdictStatus.DEVICE_LIMITED
                for verdict in verdicts
            )
        )

    def test_phase_report_returns_the_derived_status(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, subject.phase_report(full_pass_records(), black_unavailable="no root"))
            self.assertEqual(1, subject.phase_report(pairs(4, 1200.0, 1000.0)))


class MeasurementIdentityTest(unittest.TestCase):
    """Finding 4: a record set that mixes builds or channels is refused, naming the values."""

    def test_a_consistent_set_is_accepted(self) -> None:
        records = [
            {"apk_sha256": "a", "api_level": 36, "power_source": "device", "clk_tck": 100},
            {"apk_sha256": "a", "api_level": 36, "power_source": "device", "clk_tck": 100},
        ]
        subject.check_measurement_identity(records)

    def test_a_mixed_apk_is_refused_naming_both_values(self) -> None:
        records = [{"apk_sha256": "aaa", "api_level": 36}, {"apk_sha256": "bbb", "api_level": 36}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.check_measurement_identity(records)
        self.assertIn("aaa", str(raised.exception))
        self.assertIn("bbb", str(raised.exception))

    def test_a_mixed_api_level_is_refused(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.check_measurement_identity([{"api_level": 35}, {"api_level": 36}])

    def test_a_mixed_power_source_is_refused(self) -> None:
        records = [{"apk_sha256": "a", "power_source": "device"}, {"apk_sha256": "a", "power_source": "simulated"}]
        with self.assertRaises(device_layer.ProbeError):
            subject.check_measurement_identity(records)

    def test_a_mixed_tick_rate_is_refused(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            subject.check_measurement_identity([{"clk_tck": 100}, {"clk_tck": 250}])

    def test_a_field_a_record_does_not_carry_is_not_a_disagreement(self) -> None:
        subject.check_measurement_identity([{"api_level": 36}, {"api_level": 36, "clk_tck": 100}])

    def test_a_null_apk_checksum_does_not_aggregate_with_a_named_one(self) -> None:
        # `append_record` always writes `apk_sha256`, so a null checksum is a recorded value meaning
        # "unknown build", not a missing field: a window measured before the APK was built must not
        # aggregate with one measured against it.
        records = [{"apk_sha256": None, "api_level": 36}, {"apk_sha256": "abc", "api_level": 36}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.check_measurement_identity(records)
        self.assertIn("unknown", str(raised.exception))
        self.assertIn("abc", str(raised.exception))

    def test_a_checksum_no_record_carries_is_not_bounded(self) -> None:
        subject.check_measurement_identity([{"api_level": 36}, {"api_level": 36, "apk_sha256": "abc"}])

    def test_a_mixed_device_is_refused_naming_both_digests(self) -> None:
        # Two devices at one build and API level are still two measurements: the threshold and the
        # noise floor behind it are per device, so their records must not be aggregated into one B4
        # or B5 verdict.
        records = [{"device_id": "aaaa", "apk_sha256": "a"}, {"device_id": "bbbb", "apk_sha256": "a"}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.check_measurement_identity(records)
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

    def test_a_record_carries_the_device_it_was_measured_on(self) -> None:
        device = MagicMock()
        device.serial = "R58M12345AB"
        device.run_adb.return_value = b"36\n"
        with (
            patch.object(subject, "read_source_revision", return_value="rev"),
            patch.object(subject, "read_apk_sha256", return_value="abc"),
        ):
            record = subject.build_record(device, "resources", 0.0)
        self.assertIn("device_id", subject.IDENTITY_FIELDS)
        self.assertEqual(subject.device_identity(device), record.values["device_id"])

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
            subject.phase_report(records)


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
                device, arm=subject.Arm.LIVE, baseline=None, runs=4, minutes=1.0, recorder=recorder
            )
        self.assertEqual(4, recorder.emit.call_count)
        self.assertEqual(["pilot_floor"], [record.phase for record in returned])
        self.assertIsNotNone(returned[0].values["noise_floor_rate_ua"])

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
                device, arm=subject.Arm.LIVE, baseline=None, runs=4, minutes=15.0, recorder=recorder
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
            subject.phase_pilot(device, arm=subject.Arm.LIVE, baseline=None, runs=3, minutes=1.0, recorder=recorder)
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


class ParseTotalPssTest(unittest.TestCase):
    """Checks the `dumpsys meminfo` total-PSS parsing."""

    def test_reads_the_total_line(self) -> None:
        dump = "  TOTAL      26697    27689\n"
        self.assertEqual(26697, subject.parse_total_pss_kb(dump))

    def test_has_no_value_without_a_total_line(self) -> None:
        self.assertIsNone(subject.parse_total_pss_kb("  Graphics   100\n"))


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


class NegativeDischargeTest(unittest.TestCase):
    """Finding 1: a pack that gained charge across a window is not a discharge measurement."""

    def test_a_rising_charge_counter_is_refused(self) -> None:
        window = _counter_window(3_339_000, 3_501_000)
        with self.assertRaises(device_layer.ProbeError) as raised:
            _ = window.discharge_rate_ua
        self.assertIn("gained charge", str(raised.exception))

    def test_a_falling_charge_counter_is_still_a_measurement(self) -> None:
        self.assertGreater(_counter_window(3_501_000, 3_339_000).discharge_rate_ua, 0.0)

    def test_a_state_file_carrying_a_negative_rate_is_not_a_comparison(self) -> None:
        records = [visible_record("A", -1000.0, cpu=1.0), visible_record("B", 1000.0, baseline="stock")] * 4
        lines = [verdict.line for verdict in subject.report_lines(records)]
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
        self.assertFalse(any(line.startswith("B4 vs stock point estimate") for line in lines))


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

    def test_a_still_locked_device_is_refused(self) -> None:
        device = self.device()
        device.read_keyguard_locked.return_value = True
        with self.assertRaises(device_layer.ProbeError) as raised:
            subject.ensure_visible_home(device)
        self.assertIn("still locked", str(raised.exception))

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
        # None and restored by deleting the key rather than by writing one back.
        device = MagicMock()
        device.run_adb.return_value = b""
        device.read_screen_on.return_value = True
        with patch.object(subject, "read_int_setting", side_effect=_all_set_except("stay_on_while_plugged_in")):
            snapshot = subject.DeviceStateSnapshot.capture(device)
        self.assertEqual(1, snapshot.brightness)
        self.assertIsNone(snapshot.stay_on)


class MonotonicTimestampTest(unittest.TestCase):
    """Finding 4: a window's duration comes from a clock that cannot be stepped."""

    def test_a_measurement_timestamp_is_not_taken_from_the_wall_clock(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = REALISTIC_BATTERY_DUMP.encode()
        with patch.object(subject.time, "time", side_effect=AssertionError("the wall clock was used")):
            sample = subject.take_battery_sample(device, None, 100)
        self.assertIsInstance(sample.timestamp, float)

    def test_two_samples_are_ordered_on_the_monotonic_clock(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = REALISTIC_BATTERY_DUMP.encode()
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


class AlarmBudgetWordingTest(unittest.TestCase):
    """Finding 6: the matrix and the probe must describe the same budget."""

    def b2_row(self) -> str:
        """Return the acceptance matrix's B2 row."""
        budget = Path(__file__).resolve().parent.parent / "docs" / "battery-budget.md"
        return next(line for line in budget.read_text(encoding="utf-8").splitlines() if line.startswith("| **B2:"))

    def test_the_matrix_budget_matches_what_the_probe_counts(self) -> None:
        # The probe counts every pending alarm the package holds, because the pending list does not
        # say whether an alarm repeats. A matrix row promising a narrower budget than the probe can
        # check would describe a criterion nothing measures.
        row = self.b2_row()
        self.assertIn("0 pending alarms of any kind", row)
        self.assertNotIn("repeating alarms", row)

    def test_the_row_names_every_resource_the_report_judges(self) -> None:
        # `report` emits a `B2 jobs` verdict and the probe reads `dumpsys jobscheduler`, so a row
        # naming only wake locks and alarms would judge a budget whose target and method the matrix
        # never states.
        row = self.b2_row()
        self.assertIn("0 JobScheduler jobs", row)
        self.assertIn("dumpsys jobscheduler", row)


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
            subject.verify_arm(device, subject.Arm.LIVE, None)
        self.assertIn("com.example.other", str(raised.exception))

    def test_the_exact_service_is_arm_a(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        with patch.object(device_layer, "read_wallpaper_component", return_value=SERVICE_COMPONENT):
            self.assertEqual(SERVICE_COMPONENT, subject.verify_arm(device, subject.Arm.LIVE, None))

    def test_arm_b_admits_the_platform_static_engine(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        with patch.object(device_layer, "read_wallpaper_component", return_value=STOCK_COMPONENT):
            self.assertEqual(
                STOCK_COMPONENT,
                subject.verify_arm(device, subject.Arm.BASELINE, subject.Baseline.STOCK),
            )

    def test_arm_b_refuses_a_live_service_from_another_package(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        look_alike = "ComponentInfo{com.example.other/com.example.other.LiveWallpaperService}"
        with (
            patch.object(device_layer, "read_wallpaper_component", return_value=look_alike),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            subject.verify_arm(device, subject.Arm.BASELINE, subject.Baseline.STOCK)
        self.assertIn("com.example.other", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
