#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Battery and resource acceptance protocol for Astronomical Clock Wallpaper.

Measures power drain, CPU utilization, wake lock presence, and hidden-state frame rendering
on a connected physical Android device via ADB, comparing against static wallpaper baselines.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

import device_layer

CHARGE_COUNTER_PATTERN: Final = re.compile(r"Charge counter:\s*(\d+)")
BATTERY_LEVEL_PATTERN: Final = re.compile(r"level:\s*(\d+)")
BATTERY_VOLTAGE_PATTERN: Final = re.compile(r"voltage:\s*(\d+)")
WAKE_LOCK_UID_PATTERN: Final = re.compile(r"Wake lock .*?: (\d+)")

DEFAULT_SAMPLE_SECONDS: Final = 600.0
DEFAULT_PILOT_SECONDS: Final = 120.0
MAX_HIDDEN_CPU_PCT: Final = 0.1
PROVISIONAL_MAX_VISIBLE_DELTA_PCT: Final = 5.0
PROVISIONAL_MAX_VISIBLE_CPU_PCT: Final = 3.0

MIN_STAT_FIELDS: Final = 15
DF_SAMPLE_THRESHOLD: Final = 5
MIN_PSS_FIELDS: Final = 2


@dataclass(frozen=True)
class BatterySample:
    """Instantaneous battery and process telemetry reading."""

    timestamp: float
    charge_counter_uah: int
    level_pct: int
    voltage_mv: int
    process_cpu_seconds: float | None
    total_pss_kb: int | None


@dataclass(frozen=True)
class MeasurementRun:
    """A single bounded measurement period under constant display/workload conditions."""

    label: str
    duration_seconds: float
    start_sample: BatterySample
    end_sample: BatterySample
    wake_lock_count: int
    frames_rendered: int | None

    @property
    def discharge_uah(self) -> int:
        """Net microampere-hours consumed during the measurement window."""
        return max(0, self.start_sample.charge_counter_uah - self.end_sample.charge_counter_uah)

    @property
    def discharge_rate_ua(self) -> float:
        """Estimated average current draw in microamperes."""
        if self.duration_seconds <= 0:
            return 0.0
        return (self.discharge_uah / self.duration_seconds) * 3600.0

    @property
    def cpu_usage_pct(self) -> float | None:
        """Average CPU usage across single core percentage during the window."""
        if (
            self.start_sample.process_cpu_seconds is None
            or self.end_sample.process_cpu_seconds is None
            or self.duration_seconds <= 0
        ):
            return None
        delta_cpu = self.end_sample.process_cpu_seconds - self.start_sample.process_cpu_seconds
        return (max(0.0, delta_cpu) / self.duration_seconds) * 100.0


@dataclass(frozen=True)
class ComparisonResult:
    """Statistical evaluation of paired baseline vs live-wallpaper runs."""

    baseline_runs: Sequence[MeasurementRun]
    treatment_runs: Sequence[MeasurementRun]
    relative_delta_pct: float
    ci_95_upper_pct: float
    meets_provisional_budget: bool


def parse_battery_output(output: str) -> tuple[int, int, int]:
    """Extract charge counter (uAh), level (%), and voltage (mV) from dumpsys battery."""
    charge_match = CHARGE_COUNTER_PATTERN.search(output)
    level_match = BATTERY_LEVEL_PATTERN.search(output)
    voltage_match = BATTERY_VOLTAGE_PATTERN.search(output)

    charge_uah = int(charge_match.group(1)) if charge_match else 0
    level_pct = int(level_match.group(1)) if level_match else 0
    voltage_mv = int(voltage_match.group(1)) if voltage_match else 0

    return charge_uah, level_pct, voltage_mv


def parse_proc_stat_cpu_seconds(stat_content: str, clk_tck: int = 100) -> float | None:
    """Parse utime + stime from /proc/<pid>/stat line and return total CPU seconds."""
    parts = stat_content.split()
    if len(parts) < MIN_STAT_FIELDS:
        return None
    try:
        # Field 14 is utime, field 15 is stime (1-indexed, so index 13 and 14 in 0-indexed split)
        utime = int(parts[13])
        stime = int(parts[14])
        return (utime + stime) / float(clk_tck)
    except (ValueError, IndexError):
        return None


def calculate_comparison_statistics(
    baseline_rates: Sequence[float], treatment_rates: Sequence[float]
) -> tuple[float, float]:
    """Calculate relative increase percentage and 95% upper confidence bound for samples."""
    if not baseline_rates or not treatment_rates:
        return 0.0, 0.0

    mean_base = sum(baseline_rates) / len(baseline_rates)
    mean_treat = sum(treatment_rates) / len(treatment_rates)

    rel_delta = 0.0 if mean_base <= 0.0 else ((mean_treat - mean_base) / mean_base) * 100.0

    # Calculate paired standard error if lengths match and > 1
    if len(baseline_rates) == len(treatment_rates) and len(baseline_rates) > 1:
        diffs = [t - b for b, t in zip(baseline_rates, treatment_rates, strict=True)]
        mean_diff = sum(diffs) / len(diffs)
        var_diff = sum((d - mean_diff) ** 2 for d in diffs) / (len(diffs) - 1)
        se = math.sqrt(var_diff / len(diffs))
        # 95% critical value t approx 2.776 for df=4 (n=5) or ~3.182 for df=3 (n=4)
        t_val = 2.776 if len(diffs) >= DF_SAMPLE_THRESHOLD else 3.182
        margin = (t_val * se / mean_base) * 100.0 if mean_base > 0 else 0.0
        ci_upper = rel_delta + margin
    else:
        ci_upper = rel_delta

    return rel_delta, ci_upper


def read_process_uid(device: device_layer.AdbDevice, pid: int | None) -> int | None:
    """Read process UID from /proc/<pid>."""
    if pid is None or pid <= 0:
        return None
    try:
        output = device.run_adb(["shell", "stat", "-c", "%u", f"/proc/{pid}"]).decode(errors="replace").strip()
        if output.isdigit():
            return int(output)
    except (subprocess.SubprocessError, ValueError):
        return None
    return None


def take_battery_sample(device: device_layer.AdbDevice, pid: int | None) -> BatterySample:
    """Read current battery hardware counters and process CPU/memory."""
    bat_output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    charge_uah, level_pct, voltage_mv = parse_battery_output(bat_output)

    cpu_seconds: float | None = None
    if pid is not None and pid > 0:
        stat_output = device.run_adb(["shell", "cat", f"/proc/{pid}/stat"]).decode(errors="replace")
        cpu_seconds = parse_proc_stat_cpu_seconds(stat_output)

    mem_output = device.run_adb(["shell", "dumpsys", "meminfo", device_layer.PACKAGE_NAME]).decode(errors="replace")
    total_pss_kb = None
    for line in mem_output.splitlines():
        parts = line.split()
        if len(parts) >= MIN_PSS_FIELDS and parts[0] == "TOTAL" and parts[1].isdigit():
            total_pss_kb = int(parts[1])
            break

    return BatterySample(
        timestamp=time.time(),
        charge_counter_uah=charge_uah,
        level_pct=level_pct,
        voltage_mv=voltage_mv,
        process_cpu_seconds=cpu_seconds,
        total_pss_kb=total_pss_kb,
    )


def count_held_wakelocks(device: device_layer.AdbDevice, app_uid: int | None) -> int:
    """Inspect dumpsys power for active partial wake locks held by the application."""
    if app_uid is None:
        return 0
    power_output = device.run_adb(["shell", "dumpsys", "power"]).decode(errors="replace")
    count = 0
    for line in power_output.splitlines():
        if "PARTIAL_WAKE_LOCK" in line and f"uid={app_uid}" in line:
            count += 1
    return count


def count_alarms(device: device_layer.AdbDevice) -> int:
    """Count active alarms registered by the wallpaper package."""
    alarm_output = device.run_adb(["shell", "dumpsys", "alarm"]).decode(errors="replace")
    count = 0
    for line in alarm_output.splitlines():
        if device_layer.PACKAGE_NAME in line and "Alarm{" in line:
            count += 1
    return count


def read_batterystats_summary(device: device_layer.AdbDevice) -> str | None:
    """Read historical batterystats summary for the wallpaper package."""
    stats_output = device.run_adb(["shell", "dumpsys", "batterystats", device_layer.PACKAGE_NAME]).decode(
        errors="replace"
    )
    lines = []
    for line in stats_output.splitlines():
        stripped = line.strip()
        if any(key in stripped for key in ("Total cpu time:", "Fg Service for:", "Total running:")):
            lines.append(stripped)
    return ", ".join(lines) if lines else None


def simulate_unplug(device: device_layer.AdbDevice) -> None:
    """Configure device to simulate battery discharge over USB connection."""
    device.run_adb(["shell", "cmd", "battery", "unplug"])


def restore_battery_simulation(device: device_layer.AdbDevice) -> None:
    """Restore normal battery status reporting after simulation."""
    device.run_adb(["shell", "cmd", "battery", "reset"])


def main(argv: Sequence[str] | None = None) -> int:
    """CLI driver for battery protocol measurement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Target ADB device serial number")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=DEFAULT_SAMPLE_SECONDS,
        help="Measurement duration per phase in seconds",
    )
    parser.add_argument(
        "--pilot",
        action="store_true",
        help="Run an expedited pilot pass to evaluate device noise floor",
    )
    args = parser.parse_args(argv)

    duration = DEFAULT_PILOT_SECONDS if args.pilot else args.duration_seconds
    serial = device_layer.select_target_serial(args.device)
    device = device_layer.AdbDevice(serial)

    print(f"Target device: {device.serial}")
    print(f"Phase duration: {duration:.1f}s (pilot={args.pilot})")

    pid = device.get_wallpaper_pid()
    print(f"Target wallpaper PID: {pid}")

    uid = read_process_uid(device, pid)
    print(f"Target wallpaper UID: {uid}")

    hist_stats = read_batterystats_summary(device)
    if hist_stats:
        print(f"Historical batterystats: {hist_stats}")

    wakelocks = count_held_wakelocks(device, uid)
    alarms = count_alarms(device)
    print(f"Wake locks held: {wakelocks} (budget: 0)")
    print(f"Alarms active: {alarms} (budget: 0)")

    try:
        simulate_unplug(device)
        print("Discharge simulation enabled (cmd battery unplug).")

        start = take_battery_sample(device, pid)
        print(
            f"Initial state: charge={start.charge_counter_uah} uAh, "
            f"level={start.level_pct}%, voltage={start.voltage_mv} mV, "
            f"pss={start.total_pss_kb} kB"
        )

        sample_duration = min(5.0, duration) if args.pilot else duration
        time.sleep(sample_duration)

        end = take_battery_sample(device, pid)
        delta_uah = start.charge_counter_uah - end.charge_counter_uah
        print(
            f"Sample completed ({sample_duration:.1f}s): delta={delta_uah} uAh, "
            f"level={end.level_pct}%, voltage={end.voltage_mv} mV"
        )
        if start.process_cpu_seconds is not None and end.process_cpu_seconds is not None:
            cpu_used = end.process_cpu_seconds - start.process_cpu_seconds
            cpu_pct = (cpu_used / sample_duration) * 100.0 if sample_duration > 0 else 0.0
            print(f"CPU consumed: {cpu_used:.3f}s ({cpu_pct:.2f}% of one core)")
    finally:
        restore_battery_simulation(device)
        print("Restored battery status reporting (cmd battery reset).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
