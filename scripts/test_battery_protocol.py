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
# entry is a `<TYPE> #<n>: Alarm{...}` line whose last token is the package.
ALARM_DUMP = """\
Current Alarm Manager state:
  Settings:
    version=2
    min_futurity=+5s0ms
  2 pending alarms:
    ELAPSED #1: Alarm{eea5bdc type 3 origWhen 87399351 whenElapsed 87399351 android}
    RTC_WAKEUP #0: Alarm{7d0e24a type 0 origWhen 1712345678 whenElapsed 1712345678 \
io.github.godaniya.astronomicalclockswallpaper.debug}
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
        self.assertEqual(1, subject.parse_alarm_count(ALARM_DUMP))
        other_package = ALARM_DUMP.replace("io.github.godaniya.astronomicalclockswallpaper.debug", "com.other.pkg")
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

    def test_an_unresolved_uid_is_refused_before_the_probe_runs(self) -> None:
        device = MagicMock()
        with patch.object(device, "run_adb") as probe, self.assertRaises(device_layer.ProbeError):
            subject.count_held_wakelocks(device, None)
        probe.assert_not_called()


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

    def test_a_window_that_saw_a_charger_is_refused(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.run_adb.return_value = b"36\n"
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "c", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,), (True, False))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0, recorder=recorder())

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
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        sample = subject.BatterySample(0.0, 1, 100, 4000, 1.0, 1000)
        with (
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "read_power_source_lines", return_value=ON_BATTERY_POWER_LINES),
            patch.object(subject, "verify_arm"),
            patch.object(subject, "take_battery_sample", side_effect=[sample, sample]),
            patch.object(subject, "sample_window_states", return_value=([True, True], [True, False])),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            patch.object(subject, "apply_power_mode", return_value=subject.POWER_SOURCE_DEVICE),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_memory(device, minutes=1.0, recorder=recorder())


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


class MemoryPhaseTest(unittest.TestCase):
    """Checks that the B6 window verifies its arm and its display state."""

    def test_a_display_that_came_on_fails_the_window(self) -> None:
        device = MagicMock()
        device.read_process_cpu_clock_ticks.return_value = 100
        device.read_screen_on.return_value = True
        device.read_keyguard_locked.return_value = False
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

    def test_a_stock_only_run_still_reports_the_black_arm_as_not_measured(self) -> None:
        # One block per documented baseline, not one per baseline the state file happens to carry: a
        # stock-only set used to read as a complete verdict with the secondary baseline simply absent.
        lines = self.lines(full_pass_records())
        self.assertTrue(any(line.startswith("B4 vs black") and "NOT MEASURED" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 vs stock CI upper bound") and "PASS" in line for line in lines))

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
        verdicts = subject.report_lines(full_pass_records())
        self.assertEqual(0, subject.report_exit_code(verdicts))

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

    def test_a_stock_only_full_pass_exits_zero_with_the_black_line_present(self) -> None:
        verdicts = subject.report_lines(full_pass_records())
        self.assertEqual(0, subject.report_exit_code(verdicts))
        self.assertTrue(
            any(
                verdict.label == "B4 vs black" and verdict.status is subject.VerdictStatus.DEVICE_LIMITED
                for verdict in verdicts
            )
        )

    def test_phase_report_returns_the_derived_status(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, subject.phase_report(full_pass_records()))
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

    def test_a_display_that_could_not_be_woken_is_refused(self) -> None:
        device = self.device()
        device.wake_screen.return_value = False
        with self.assertRaises(device_layer.ProbeError):
            subject.ensure_visible_home(device)


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

    def test_the_matrix_budget_matches_what_the_probe_counts(self) -> None:
        # The probe counts every pending alarm the package holds, because the pending list does not
        # say whether an alarm repeats. A matrix row promising a narrower budget than the probe can
        # check would describe a criterion nothing measures.
        budget = Path(__file__).resolve().parent.parent / "docs" / "battery-budget.md"
        row = next(line for line in budget.read_text(encoding="utf-8").splitlines() if line.startswith("| **B2:"))
        self.assertIn("0 pending alarms of any kind", row)
        self.assertNotIn("repeating alarms", row)


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

    def test_arm_b_refuses_a_look_alike_service_too(self) -> None:
        device = MagicMock()
        device.serial = "serial"
        with patch.object(device_layer, "read_wallpaper_component", return_value=STOCK_COMPONENT):
            self.assertEqual(
                STOCK_COMPONENT,
                subject.verify_arm(device, subject.Arm.BASELINE, subject.Baseline.STOCK),
            )


if __name__ == "__main__":
    unittest.main()
