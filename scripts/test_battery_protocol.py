#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for battery protocol metrics, parsing, and statistical models."""

import unittest
from unittest.mock import MagicMock

from battery_protocol import (
    BatterySample,
    MeasurementRun,
    calculate_comparison_statistics,
    count_alarms,
    count_held_wakelocks,
    parse_battery_output,
    parse_proc_stat_cpu_seconds,
    read_batterystats_summary,
    read_process_uid,
)


class BatteryProtocolTest(unittest.TestCase):
    """Verifies parsing of Android dumpsys output and statistical comparisons."""

    def test_parse_battery_output_valid(self) -> None:
        sample = """
Current Battery Service state:
  AC powered: false
  USB powered: true
  Charge counter: 4500000
  level: 95
  voltage: 4120
  temperature: 280
"""
        charge, level, voltage = parse_battery_output(sample)
        self.assertEqual(4500000, charge)
        self.assertEqual(95, level)
        self.assertEqual(4120, voltage)

    def test_parse_battery_output_missing_fields(self) -> None:
        charge, level, voltage = parse_battery_output("Nothing here")
        self.assertEqual(0, charge)
        self.assertEqual(0, level)
        self.assertEqual(0, voltage)

    def test_parse_proc_stat_cpu_seconds(self) -> None:
        # Sample /proc/<pid>/stat line with utime=150 and stime=50 at index 13, 14
        stat_line = "1234 (astro) S 1 1234 1234 0 -1 4194304 100 0 0 0 150 50 0 0 20 0 1 0 1000 0 0"
        cpu_seconds = parse_proc_stat_cpu_seconds(stat_line, clk_tck=100)
        self.assertIsNotNone(cpu_seconds)
        if cpu_seconds is not None:
            self.assertAlmostEqual(2.0, cpu_seconds)

    def test_parse_proc_stat_cpu_seconds_invalid(self) -> None:
        self.assertIsNone(parse_proc_stat_cpu_seconds("short string"))
        self.assertIsNone(parse_proc_stat_cpu_seconds("a b c d e f g h i j k l m n notanumber"))

    def test_calculate_comparison_statistics_identical(self) -> None:
        base = [100.0, 102.0, 99.0, 101.0]
        treat = [100.0, 102.0, 99.0, 101.0]
        rel_delta, ci_upper = calculate_comparison_statistics(base, treat)
        self.assertAlmostEqual(0.0, rel_delta)
        self.assertAlmostEqual(0.0, ci_upper)

    def test_calculate_comparison_statistics_increase(self) -> None:
        base = [100.0, 100.0, 100.0, 100.0]
        treat = [103.0, 103.0, 103.0, 103.0]
        rel_delta, ci_upper = calculate_comparison_statistics(base, treat)
        self.assertAlmostEqual(3.0, rel_delta)
        self.assertAlmostEqual(3.0, ci_upper)

    def test_measurement_run_properties(self) -> None:
        start = BatterySample(
            timestamp=0.0,
            charge_counter_uah=4000000,
            level_pct=90,
            voltage_mv=4000,
            process_cpu_seconds=10.0,
            total_pss_kb=45000,
        )
        end = BatterySample(
            timestamp=600.0,
            charge_counter_uah=3980000,
            level_pct=90,
            voltage_mv=3990,
            process_cpu_seconds=16.0,
            total_pss_kb=45500,
        )
        run = MeasurementRun(
            label="live_wallpaper",
            duration_seconds=600.0,
            start_sample=start,
            end_sample=end,
            wake_lock_count=0,
            frames_rendered=600,
        )
        self.assertEqual(20000, run.discharge_uah)
        self.assertAlmostEqual(120000.0, run.discharge_rate_ua)
        self.assertIsNotNone(run.cpu_usage_pct)
        if run.cpu_usage_pct is not None:
            self.assertAlmostEqual(1.0, run.cpu_usage_pct)

    def test_read_process_uid(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"10026\n"
        self.assertEqual(10026, read_process_uid(device, 4411))

    def test_read_process_uid_none_or_invalid(self) -> None:
        device = MagicMock()
        self.assertIsNone(read_process_uid(device, None))
        self.assertIsNone(read_process_uid(device, 0))
        device.run_adb.return_value = b"not_a_number\n"
        self.assertIsNone(read_process_uid(device, 1234))

    def test_count_held_wakelocks(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = (
            b"PARTIAL_WAKE_LOCK 'test' (uid=10026, pid=4411)\nPARTIAL_WAKE_LOCK 'other' (uid=1000, pid=100)\n"
        )
        self.assertEqual(1, count_held_wakelocks(device, 10026))
        self.assertEqual(0, count_held_wakelocks(device, None))

    def test_count_alarms(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = (
            b"Alarm{123 type 0 when 456 io.github.godaniya.astronomicalclockswallpaper.debug}\n"
            b"Alarm{456 type 1 when 789 com.android.systemui}\n"
        )
        self.assertEqual(1, count_alarms(device))

    def test_read_batterystats_summary(self) -> None:
        device = MagicMock()
        device.run_adb.return_value = b"  Total cpu time: u=1s s=200ms\n  Fg Service for: 40s\n  Unrelated line\n"
        summary = read_batterystats_summary(device)
        self.assertIsNotNone(summary)
        if summary is not None:
            self.assertIn("Total cpu time: u=1s s=200ms", summary)
            self.assertIn("Fg Service for: 40s", summary)


if __name__ == "__main__":
    unittest.main()
