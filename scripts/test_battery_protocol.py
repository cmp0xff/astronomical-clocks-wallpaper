#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the battery protocol's parsing, statistics, arm guard, and report."""

import contextlib
import io
import subprocess
import unittest
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


def visible_record(
    arm: str,
    rate: float,
    *,
    cpu: float | None = None,
    pilot: bool = False,
    revision: str = "rev",
) -> dict[str, object]:
    """Build one visible-window record as the state file would carry it."""
    return {
        "phase": "visible",
        "arm": arm,
        "discharge_rate_ua": rate,
        "cpu_pct": cpu,
        "pilot": pilot,
        "revision": revision,
        "api_level": 36,
    }


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
        cpu_seconds = subject.parse_proc_stat_cpu_seconds(line)
        if cpu_seconds is None:
            self.fail("a well-formed stat line carried no CPU time")
        self.assertAlmostEqual(2.0, cpu_seconds)

    def test_a_comm_field_containing_a_space_still_parses(self) -> None:
        # The comm field is parenthesised and may contain spaces, so the fields must be counted from
        # the closing parenthesis rather than from a whitespace split.
        line = "1234 (my astro clock) S 1 1234 1234 0 -1 4194304 100 0 0 0 150 50 0 0 20 0 1 0 1000 0 0"
        cpu_seconds = subject.parse_proc_stat_cpu_seconds(line)
        if cpu_seconds is None:
            self.fail("a stat line whose comm contains a space carried no CPU time")
        self.assertAlmostEqual(2.0, cpu_seconds)

    def test_lines_without_a_comm_or_with_unparsable_fields_have_no_value(self) -> None:
        self.assertIsNone(subject.parse_proc_stat_cpu_seconds("short string"))
        self.assertIsNone(subject.parse_proc_stat_cpu_seconds("1234 (astro) S 1 2 3 notanumber 50 0 0 0 1 2"))


class TCriticalTest(unittest.TestCase):
    """Checks the degrees-of-freedom-indexed t table and its tail."""

    def test_table_is_indexed_by_degrees_of_freedom(self) -> None:
        self.assertAlmostEqual(12.706, subject.t_critical_95(1))
        self.assertAlmostEqual(4.303, subject.t_critical_95(2))
        self.assertAlmostEqual(3.182, subject.t_critical_95(3))
        self.assertAlmostEqual(2.776, subject.t_critical_95(4))

    def test_table_reaches_thirty_degrees_of_freedom(self) -> None:
        self.assertAlmostEqual(2.042, subject.t_critical_95(30))

    def test_normal_approximation_beyond_the_table(self) -> None:
        self.assertAlmostEqual(subject.NORMAL_T_CRITICAL_95, subject.t_critical_95(31))
        self.assertAlmostEqual(subject.NORMAL_T_CRITICAL_95, subject.t_critical_95(500))

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

    def test_a_mismatched_arm_is_refused_before_the_window_starts(self) -> None:
        device = MagicMock()
        with (
            patch.object(subject, "verify_arm", side_effect=device_layer.ProbeError("arm A was requested")) as guard,
            patch.object(subject, "run_window") as window,
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_visible(device, arm=subject.Arm.LIVE, baseline=None, minutes=1.0)
        guard.assert_called_once()
        window.assert_not_called()

    def test_the_guard_is_consulted_for_a_baseline_arm_too(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"36\n"
        with (
            patch.object(subject, "verify_arm") as guard,
            patch.object(subject, "DeviceStateSnapshot"),
            patch.object(subject, "arm_conditions", return_value={"wallpaper_component": "static", "median_luma": 0.0}),
            patch.object(subject, "run_window", return_value=(_window(cpu=1.0), (True,))),
            patch.object(subject, "undo_phase_changes", return_value=[]),
        ):
            subject.phase_visible(device, arm=subject.Arm.BASELINE, baseline=subject.Baseline.STOCK, minutes=1.0)
        guard.assert_called_once()
        self.assertIs(subject.Arm.BASELINE, guard.call_args.args[1])


class MemoryPhaseTest(unittest.TestCase):
    """Checks that the B6 window verifies its arm and its display state."""

    def test_a_display_that_came_on_fails_the_window(self) -> None:
        device = MagicMock()
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
            patch.object(subject, "take_battery_sample", side_effect=[sample, sample]),
            patch.object(subject, "sample_display_states", return_value=[True, False]),
            patch.object(subject, "undo_phase_changes", return_value=[]),
            self.assertRaises(device_layer.ProbeError),
        ):
            subject.phase_memory(device, minutes=1.0)
        guard.assert_called_once()


class RestoreTest(unittest.TestCase):
    """Checks that a failed reset cannot skip putting the user's settings back."""

    def test_a_failed_battery_reset_still_runs_the_settings_restore(self) -> None:
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = ["system/screen_brightness did not read back as 35"]
        with patch.object(subject, "reset_battery_simulation", side_effect=subprocess.CalledProcessError(1, "reset")):
            problems = subject.undo_phase_changes(device, snapshot)
        snapshot.restore.assert_called_once()
        self.assertTrue(any("battery simulation reset failed" in problem for problem in problems))
        self.assertIn("system/screen_brightness did not read back as 35", problems)

    def test_the_settings_restore_is_not_lost_when_only_the_reset_fails(self) -> None:
        device = MagicMock()
        snapshot = MagicMock()
        snapshot.restore.return_value = []
        self.assertEqual([], subject.undo_phase_changes(device, snapshot))

    def test_the_screen_timeout_is_pinned_under_the_key_the_platform_reads(self) -> None:
        device = MagicMock()
        snapshot = subject.DeviceStateSnapshot(
            device, brightness=35, brightness_mode=1, screen_timeout=300_000, airplane_mode=0
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
        with (
            patch.object(device_layer, "select_target_serial", return_value="device"),
            patch.object(device_layer, "AdbDevice", return_value=device),
            patch.object(subject, "dispatch", return_value=[record]),
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


class ReportTest(unittest.TestCase):
    """Checks the aggregate pass/fail lines against the committed thresholds."""

    def test_every_budget_is_not_measured_without_records(self) -> None:
        lines = subject.report_lines([])
        for label in ("B2 wake locks", "B3 hidden CPU", "B4 relative increase", "B5 visible CPU", "B6 PSS growth"):
            self.assertTrue(any(line.startswith(label) and "NOT MEASURED" in line for line in lines))

    def test_b1_is_declared_out_of_scope(self) -> None:
        lines = subject.report_lines([])
        self.assertTrue(any("B1 hidden frames" in line and "NOT MEASURED HERE" in line for line in lines))

    def test_zero_wake_locks_and_alarms_pass(self) -> None:
        records = [{"phase": "resources", "wake_locks": 0, "alarms": 0, "revision": "rev", "api_level": 36}]
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks") and "PASS" in line for line in lines))
        self.assertTrue(any(line.startswith("B2 alarms") and "PASS" in line for line in lines))

    def test_a_held_wake_lock_fails(self) -> None:
        records = [{"phase": "resources", "wake_locks": 1, "alarms": 0, "revision": "rev", "api_level": 36}]
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks") and "FAIL" in line for line in lines))

    def test_hidden_cpu_is_checked_against_its_budget(self) -> None:
        under = [{"phase": "hidden", "cpu_pct": 0.05, "revision": "rev", "api_level": 36}]
        over = [{"phase": "hidden", "cpu_pct": 5.4, "revision": "rev", "api_level": 36}]
        self.assertTrue(any("PASS" in line for line in subject.report_lines(under)))
        self.assertTrue(any("FAIL" in line for line in subject.report_lines(over)))

    def test_matched_arms_within_the_budget_pass(self) -> None:
        records = [
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0),
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0),
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0),
            visible_record("A", 1000.0, cpu=1.0),
            visible_record("B", 1000.0),
        ]
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B4 point estimate") and "PASS" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 CI upper bound") and "PASS" in line for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU") and "PASS" in line for line in lines))

    def test_a_large_increase_fails_both_b4_lines(self) -> None:
        records = [visible_record("A", 1200.0), visible_record("B", 1000.0)] * 4
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B4 point estimate") and "FAIL" in line for line in lines))
        self.assertTrue(any(line.startswith("B4 CI upper bound") and "FAIL" in line for line in lines))

    def test_visible_cpu_over_its_budget_fails(self) -> None:
        records = [visible_record("A", 1000.0, cpu=6.4), visible_record("B", 1000.0)] * 4
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B5 visible CPU") and "FAIL" in line for line in lines))

    def test_pilot_windows_are_excluded_from_the_final_comparison(self) -> None:
        records = [visible_record("A", 1000.0), visible_record("B", 1000.0)] * 4
        records.append(visible_record("A", 99999.0, pilot=True))
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B4 point estimate") and "PASS" in line for line in lines))

    def test_a_silent_discharge_channel_is_reported_not_crashed(self) -> None:
        # The target device's fuel gauge reports a constant counter, so every window's rate is zero.
        # B4 is then unmeasurable, and the criteria that *were* measured must still be reported.
        records = [visible_record("A", 0.0, cpu=1.0), visible_record("B", 0.0)] * 4
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B4 relative increase") and "NOT MEASURED" in line for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU") for line in lines))
        self.assertFalse(any(line.startswith("B4 point estimate") and "PASS" in line for line in lines))

    def test_too_few_pairs_leaves_b4_unmeasured(self) -> None:
        records = [visible_record("A", 1000.0), visible_record("B", 1000.0)] * 3
        lines = subject.report_lines(records)
        self.assertTrue(any(line.startswith("B4 relative increase") and "NOT MEASURED" in line for line in lines))

    def test_pss_growth_is_checked_against_its_budget(self) -> None:
        records = [{"phase": "memory", "pss_growth_kb": 992, "revision": "rev", "api_level": 36}]
        self.assertTrue(
            any(line.startswith("B6 PSS growth") and "PASS" in line for line in subject.report_lines(records))
        )


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


if __name__ == "__main__":
    unittest.main()
