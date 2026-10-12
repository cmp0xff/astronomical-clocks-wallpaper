#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Battery and resource acceptance protocol for Astronomical Clock Wallpaper.

Each subcommand runs one bounded phase of the budget in `docs/battery-budget.md` against a connected
physical device, appends one JSON record to a state directory, and prints its own evidence. The
phases are split so that no invocation blocks for a whole qualification, which is what the shared
device policy requires: `resources`, `hidden`, `visible`, `pilot`, `memory`, and `report`.

No phase performs the wallpaper swap it measures. The arm is verified before a window starts and the
window is refused when the device does not match, because a mis-swapped wallpaper must not be
recorded as a valid measurement.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

import device_layer

CHARGE_COUNTER_PATTERN: Final = re.compile(r"^[ \t]*Charge counter:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
BATTERY_LEVEL_PATTERN: Final = re.compile(r"^[ \t]*level:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
# Anchored to the field name so that `Max charging voltage:` can never be read as the pack voltage.
BATTERY_VOLTAGE_PATTERN: Final = re.compile(r"^[ \t]*voltage:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
TOTAL_PSS_PATTERN: Final = re.compile(r"^\s*TOTAL\s+(\d+)", re.MULTILINE)
SDK_PATTERN: Final = re.compile(r"^(\d+)$")

DEFAULT_STATE_DIR: Final = Path("build/reports/battery")
RECORD_FILE_NAME: Final = "records.jsonl"

DEFAULT_WINDOW_MINUTES: Final = 15.0
DEFAULT_HIDDEN_MINUTES: Final = 10.0
DEFAULT_MEMORY_MINUTES: Final = 30.0
SAMPLE_INTERVAL_SECONDS: Final = 30.0
COMMAND_TIMEOUT_SECONDS: Final = 60

PROC_STAT_UTIME_INDEX: Final = 11
PROC_STAT_INDEX_STIME: Final = 12
MIN_PROC_STAT_FIELDS: Final = 13
CPU_TICKS_PER_SECOND: Final = 100

# Budgets from docs/battery-budget.md. B1 is #125 criterion 1 and is deliberately not measured here.
MAX_HIDDEN_CPU_PCT: Final = 0.1
PROVISIONAL_MAX_VISIBLE_DELTA_PCT: Final = 5.0
PROVISIONAL_MAX_VISIBLE_CPU_PCT: Final = 3.0
MAX_PSS_GROWTH_KB: Final = 6144

# Committed by the pilot before the final run, per the document's own anti-tuning rule. `report`
# reads these two constants, so changing them is what "committing the threshold" means.
FINAL_MAX_VISIBLE_DELTA_PCT: Final = PROVISIONAL_MAX_VISIBLE_DELTA_PCT
FINAL_MAX_VISIBLE_CPU_PCT: Final = PROVISIONAL_MAX_VISIBLE_CPU_PCT

# Two-sided 95% Student t critical values by degrees of freedom, from the table in NIST/SEMATECH
# e-Handbook of Statistical Methods, section 1.3.6.7.3 (retrieved 2026-10-12), which is the free
# redistillation of the standard tables. Beyond 30 degrees of freedom the normal value 1.960 is used,
# where the two agree to three decimals.
T_CRITICAL_95_BY_DF: Final = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
NORMAL_T_CRITICAL_95: Final = 1.960
MIN_PAIRED_SAMPLES: Final = 4

# The arm guard's two provable facts. The live component is a deterministic read; which static image
# a baseline carries is not, so the baseline's identity is bounded by the median luminance of the
# home screen, which refuses rather than guesses when it lands between the two.
LIVE_COMPONENT_MARKER: Final = "AstronomicalClocksWallpaperService"
LUMA_STRIDE: Final = 8
LUMA_WEIGHTS: Final = (0.2126, 0.7152, 0.0722)
LUMA_MAX: Final = 255.0
MEDIAN_LUMA_NOTE: Final = "recorded, not gated: the OEM stock wallpaper on this device is a dark image"

PINNED_BRIGHTNESS: Final = 128
# A whole hour, so it comfortably exceeds the longest documented window (30 minutes) rather
# than expiring at the same instant the window closes.
LONG_SCREEN_TIMEOUT_MS: Final = 3_600_000
RESTORE_SETTLE_SECONDS: Final = 2.0


class Arm(StrEnum):
    """Which wallpaper a window is recorded against."""

    LIVE = "A"
    BASELINE = "B"


class Baseline(StrEnum):
    """Which static baseline an arm B window is recorded against."""

    STOCK = "stock"
    BLACK = "black"


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
class Window:
    """One bounded measurement period under constant display and workload conditions."""

    start_sample: BatterySample
    end_sample: BatterySample
    duration_seconds: float

    @property
    def discharge_uah(self) -> int:
        """Net microampere-hours consumed during the window."""
        return self.start_sample.charge_counter_uah - self.end_sample.charge_counter_uah

    @property
    def discharge_rate_ua(self) -> float:
        """Average current draw over the window, in microamperes."""
        if self.duration_seconds <= 0:
            detail = "a window with no elapsed time has no discharge rate"
            raise device_layer.ProbeError(detail)
        return (self.discharge_uah / self.duration_seconds) * 3600.0

    @property
    def cpu_pct(self) -> float | None:
        """Average CPU across one core during the window, or None when the process was not read."""
        start = self.start_sample.process_cpu_seconds
        end = self.end_sample.process_cpu_seconds
        if start is None or end is None or self.duration_seconds <= 0:
            return None
        return (max(0.0, end - start) / self.duration_seconds) * 100.0


@dataclass(frozen=True)
class PairedStatistics:
    """A paired comparison of two equal-length series of window rates."""

    sample_count: int
    degrees_of_freedom: int
    t_critical: float
    mean_baseline: float
    mean_treatment: float
    relative_delta_pct: float
    ci_95_upper_pct: float


@dataclass(frozen=True)
class Record:
    """One phase invocation's result, as written to the state file."""

    phase: str
    recorded_at: str
    revision: str
    apk_sha256: str | None
    api_level: int | None
    duration_seconds: float
    values: dict[str, object] = field(default_factory=dict)


class DeviceStateSnapshot:
    """The device settings a phase changes, captured so they can be put back and checked."""

    def __init__(
        self,
        device: device_layer.AdbDevice,
        *,
        brightness: int | None,
        brightness_mode: int | None,
        screen_timeout: int | None,
        airplane_mode: int | None,
    ) -> None:
        """Hold the values read before a phase changed anything."""
        self.device = device
        self.brightness = brightness
        self.brightness_mode = brightness_mode
        self.screen_timeout = screen_timeout
        self.airplane_mode = airplane_mode

    @classmethod
    def capture(cls, device: device_layer.AdbDevice) -> DeviceStateSnapshot:
        """Read the settings a measurement phase pins."""
        return cls(
            device,
            brightness=read_int_setting(device, "system", "screen_brightness"),
            brightness_mode=read_int_setting(device, "system", "screen_brightness_mode"),
            screen_timeout=read_int_setting(device, "system", "screen_off_timeout"),
            airplane_mode=read_int_setting(device, "global", "airplane_mode_on"),
        )

    def pin(self) -> None:
        """Pin brightness, the screen timeout, and airplane mode for a measurement."""
        self.device.run_adb(["shell", "settings", "put", "system", "screen_brightness_mode", "0"])
        self.device.run_adb(["shell", "settings", "put", "system", "screen_brightness", str(PINNED_BRIGHTNESS)])
        self.device.run_adb(["shell", "settings", "put", "system", "screen_off_timeout", str(LONG_SCREEN_TIMEOUT_MS)])
        set_airplane_mode(self.device, enabled=True)

    def restore(self) -> list[str]:
        """Put every pinned setting back and report each one that could not be verified."""
        problems: list[str] = []
        set_airplane_mode(self.device, enabled=self.airplane_mode == 1)
        # Auto-brightness is restored before the stored level, because turning it back on makes the
        # framework recompute the level and write over whatever was there. Writing the level second
        # and then re-reading it after a settle is what makes the check below meaningful.
        for namespace, key, value in (
            ("system", "screen_brightness_mode", self.brightness_mode),
            ("system", "screen_brightness", self.brightness),
            ("system", "screen_off_timeout", self.screen_timeout),
        ):
            if value is not None:
                self.device.run_adb(["shell", "settings", "put", namespace, key, str(value)])
        time.sleep(RESTORE_SETTLE_SECONDS)
        for namespace, key, expected in (
            ("system", "screen_brightness", self.brightness),
            ("system", "screen_brightness_mode", self.brightness_mode),
            ("system", "screen_off_timeout", self.screen_timeout),
            ("global", "airplane_mode_on", self.airplane_mode),
        ):
            if expected is not None and read_int_setting(self.device, namespace, key) != expected:
                problems.append(f"{namespace}/{key} did not read back as {expected}")
        if read_battery_override_active(self.device):
            problems.append("battery simulation is still active after cmd battery reset")
        return problems


def undo_phase_changes(device: device_layer.AdbDevice, snapshot: DeviceStateSnapshot) -> list[str]:
    """Undo a phase's device changes, isolating each step so none can skip the others."""
    problems: list[str] = []
    for description, action in (
        ("battery simulation reset", lambda: reset_battery_simulation(device)),
        ("device settings restore", lambda: problems.extend(snapshot.restore())),
    ):
        try:
            action()
        except (subprocess.SubprocessError, OSError) as error:
            problems.append(f"{description} failed: {device_layer.error_detail(error)}")
    return problems


def read_int_setting(device: device_layer.AdbDevice, namespace: str, key: str) -> int | None:
    """Read one integer device setting, or None when it is unset or not an integer."""
    raw = device.run_adb(["shell", "settings", "get", namespace, key]).decode(errors="replace").strip()
    return int(raw) if raw.lstrip("-").isdigit() else None


def set_airplane_mode(device: device_layer.AdbDevice, *, enabled: bool) -> None:
    """Turn airplane mode on or off through the connectivity service."""
    device.run_adb(["shell", "cmd", "connectivity", "airplane-mode", "enable" if enabled else "disable"])


def read_battery_override_active(device: device_layer.AdbDevice) -> bool:
    """Report whether a `cmd battery` override is still in force."""
    output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    return not any("powered: true" in line for line in output.splitlines() if "powered:" in line)


def parse_battery_output(output: str) -> tuple[int, int, int]:
    """
    Extract charge counter (uAh), level (%), and voltage (mV) from `dumpsys battery`.

    Every field is required. A missing field is raised rather than defaulted, because a battery
    probe that stopped reporting would otherwise look like a device drawing nothing.
    """
    values: list[int] = []
    for pattern, name in (
        (CHARGE_COUNTER_PATTERN, "Charge counter"),
        (BATTERY_LEVEL_PATTERN, "level"),
        (BATTERY_VOLTAGE_PATTERN, "voltage"),
    ):
        match = pattern.search(output)
        if match is None:
            detail = f"dumpsys battery output carried no {name} field; refusing to read it as zero"
            raise device_layer.ProbeError(detail)
        values.append(int(match.group(1)))
    return values[0], values[1], values[2]


def parse_proc_stat_cpu_seconds(stat_content: str, clk_tck: int = CPU_TICKS_PER_SECOND) -> float | None:
    """Parse utime + stime from a `/proc/<pid>/stat` line and return total CPU seconds."""
    close = stat_content.rfind(")")
    if close == -1:
        return None
    parts = stat_content[close + 1 :].split()
    # After the closing parenthesis the first field is `state`, so utime (field 14) and stime
    # (field 15) are at indices 11 and 12. Splitting on whitespace instead would misparse whenever
    # the parenthesised comm field contains a space.
    if len(parts) < MIN_PROC_STAT_FIELDS:
        return None
    try:
        utime = int(parts[PROC_STAT_UTIME_INDEX])
        stime = int(parts[PROC_STAT_INDEX_STIME])
    except (ValueError, IndexError):
        return None
    return (utime + stime) / float(clk_tck)


def parse_total_pss_kb(mem_output: str) -> int | None:
    """Return the `TOTAL` PSS line of a `dumpsys meminfo` dump, in kB."""
    match = TOTAL_PSS_PATTERN.search(mem_output)
    return int(match.group(1)) if match else None


def parse_api_level(output: str) -> int:
    """Read the SDK level from `getprop ro.build.version.sdk`, refusing anything unexpected."""
    stripped = output.strip()
    if not SDK_PATTERN.match(stripped):
        detail = f"ro.build.version.sdk read back as {stripped!r}, which is not an API level"
        raise device_layer.ProbeError(detail)
    return int(stripped)


def t_critical_95(degrees_of_freedom: int) -> float:
    """
    Return the two-sided 95% Student t critical value for `degrees_of_freedom`.

    The table is tabulated to 30 degrees of freedom; beyond that the normal value is used, where
    the two agree to three decimals. Every tabulated value is returned as tabulated, so a caller
    with two or three pairs reads 4.303 or 3.182 rather than the four-pair 3.182 the previous
    two-branch lookup collapsed them to. How many pairs a comparison needs is enforced by
    `paired_statistics`, which is where the sample count is known.
    """
    if degrees_of_freedom < 1:
        detail = f"{degrees_of_freedom} degrees of freedom admits no interval at all"
        raise device_layer.ProbeError(detail)
    return T_CRITICAL_95_BY_DF.get(degrees_of_freedom, NORMAL_T_CRITICAL_95)


def paired_statistics(baseline_rates: Sequence[float], treatment_rates: Sequence[float]) -> PairedStatistics:
    """
    Compare paired baseline and treatment window rates, with a 95% interval on the increase.

    The interval is the paired one: the per-pair differences remove the drift both arms share, so
    the bound reflects the measurement actually being asked about.
    """
    if len(baseline_rates) != len(treatment_rates):
        detail = f"pairing needs equal arms but got {len(baseline_rates)} and {len(treatment_rates)} windows"
        raise device_layer.ProbeError(detail)
    if len(baseline_rates) < MIN_PAIRED_SAMPLES:
        detail = (
            f"{len(baseline_rates)} alternations were recorded but the budget mandates at least "
            f"{MIN_PAIRED_SAMPLES} pairs"
        )
        raise device_layer.ProbeError(detail)
    diffs = [treatment - baseline for baseline, treatment in zip(baseline_rates, treatment_rates, strict=True)]
    mean_baseline = sum(baseline_rates) / len(baseline_rates)
    mean_treatment = sum(treatment_rates) / len(treatment_rates)
    if mean_baseline <= 0:
        detail = "the baseline mean discharge rate is not positive, so no relative increase exists"
        raise device_layer.ProbeError(detail)
    degrees_of_freedom = len(diffs) - 1
    t_value = t_critical_95(degrees_of_freedom)
    mean_diff = sum(diffs) / len(diffs)
    variance = sum((diff - mean_diff) ** 2 for diff in diffs) / degrees_of_freedom
    standard_error = math.sqrt(variance / len(diffs))
    relative_delta_pct = (mean_diff / mean_baseline) * 100.0
    margin_pct = (t_value * standard_error / mean_baseline) * 100.0
    return PairedStatistics(
        sample_count=len(diffs),
        degrees_of_freedom=degrees_of_freedom,
        t_critical=t_value,
        mean_baseline=mean_baseline,
        mean_treatment=mean_treatment,
        relative_delta_pct=relative_delta_pct,
        ci_95_upper_pct=relative_delta_pct + margin_pct,
    )


def two_sigma_noise_floor(values: Sequence[float]) -> tuple[float, float, float]:
    """Return the mean, the sample standard deviation, and the 2-sigma half-width of a series."""
    if len(values) < MIN_PAIRED_SAMPLES:
        detail = f"a noise floor needs at least {MIN_PAIRED_SAMPLES} windows but got {len(values)}"
        raise device_layer.ProbeError(detail)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    deviation = math.sqrt(variance)
    return mean, deviation, 2.0 * deviation


def read_process_uid(device: device_layer.AdbDevice, pid: int | None) -> int | None:
    """Read the owning UID of a process from `/proc/<pid>`."""
    if pid is None or pid <= 0:
        return None
    output = device.run_adb(["shell", "stat", "-c", "%u", f"/proc/{pid}"]).decode(errors="replace").strip()
    return int(output) if output.isdigit() else None


def take_battery_sample(device: device_layer.AdbDevice, pid: int | None) -> BatterySample:
    """Read the battery fuel gauge plus the process CPU and PSS counters."""
    battery_output = device.run_adb(["shell", "dumpsys", "battery"]).decode(errors="replace")
    charge_uah, level_pct, voltage_mv = parse_battery_output(battery_output)

    cpu_seconds: float | None = None
    if pid is not None and pid > 0:
        stat_output = device.run_adb(["shell", "cat", f"/proc/{pid}/stat"]).decode(errors="replace")
        cpu_seconds = parse_proc_stat_cpu_seconds(stat_output)

    mem_output = device.run_adb(["shell", "dumpsys", "meminfo", device_layer.PACKAGE_NAME]).decode(errors="replace")
    return BatterySample(
        timestamp=time.time(),
        charge_counter_uah=charge_uah,
        level_pct=level_pct,
        voltage_mv=voltage_mv,
        process_cpu_seconds=cpu_seconds,
        total_pss_kb=parse_total_pss_kb(mem_output),
    )


def count_held_wakelocks(device: device_layer.AdbDevice, app_uid: int | None) -> int:
    """Count the partial wake locks `dumpsys power` attributes to the app's UID."""
    if app_uid is None:
        detail = "no UID was resolved for the wallpaper process, so wake locks cannot be attributed"
        raise device_layer.ProbeError(detail)
    output = device.run_adb(["shell", "dumpsys", "power"]).decode(errors="replace")
    return sum(1 for line in output.splitlines() if "PARTIAL_WAKE_LOCK" in line and f"uid={app_uid}" in line)


def count_alarms(device: device_layer.AdbDevice) -> int:
    """Count the pending alarms `dumpsys alarm` attributes to the wallpaper package."""
    output = device.run_adb(["shell", "dumpsys", "alarm"]).decode(errors="replace")
    return sum(1 for line in output.splitlines() if device_layer.PACKAGE_NAME in line and "Alarm{" in line)


def read_batterystats_summary(device: device_layer.AdbDevice) -> str | None:
    """Read the historical per-app CPU lines from `dumpsys batterystats` as corroborating evidence."""
    output = device.run_adb(["shell", "dumpsys", "batterystats", device_layer.PACKAGE_NAME]).decode(errors="replace")
    keys = ("Total cpu time:", "Fg Service for:", "Total running:")
    lines = [line.strip() for line in output.splitlines() if any(key in line for key in keys)]
    return ", ".join(lines) if lines else None


def simulate_unplug(device: device_layer.AdbDevice) -> None:
    """Make the device report itself as running on battery while it stays on USB for telemetry."""
    device.run_adb(["shell", "cmd", "battery", "unplug"])


def reset_battery_simulation(device: device_layer.AdbDevice) -> None:
    """Clear any `cmd battery` override so the device reports its real state again."""
    device.run_adb(["shell", "cmd", "battery", "reset"])


def read_source_revision() -> str:
    """Read the source revision of this working tree."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.strip()


def read_apk_sha256() -> str | None:
    """Hash the debug APK under test, or None when it has not been built."""
    apk = Path("app/build/outputs/apk/debug/app-debug.apk")
    if not apk.is_file():
        return None
    return hashlib.sha256(apk.read_bytes()).hexdigest()


def record_path(state_dir: Path) -> Path:
    """Return the JSON Lines file the phase records accumulate in."""
    return state_dir / RECORD_FILE_NAME


def append_record(state_dir: Path, record: Record) -> Path:
    """Append one record to the state file, creating the directory when it is missing."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = record_path(state_dir)
    payload = {"phase": record.phase, "recorded_at": record.recorded_at, "revision": record.revision}
    payload["apk_sha256"] = record.apk_sha256
    payload["api_level"] = record.api_level
    payload["duration_seconds"] = record.duration_seconds
    payload.update(record.values)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    return path


def load_records(state_dir: Path) -> list[dict[str, object]]:
    """Read back every record written so far, refusing to guess at a malformed line."""
    path = record_path(state_dir)
    if not path.is_file():
        detail = f"no records at {path}; run the measurement phases before the report"
        raise device_layer.ProbeError(detail)
    records: list[dict[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            detail = f"{path}:{number} is not a JSON object"
            raise device_layer.ProbeError(detail)
        records.append(parsed)
    return records


def build_record(device: device_layer.AdbDevice, phase: str, duration_seconds: float, **values: object) -> Record:
    """Assemble one record, stamping the build identity every report row has to carry."""
    api_level = parse_api_level(device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace"))
    return Record(
        phase=phase,
        recorded_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        revision=read_source_revision(),
        apk_sha256=read_apk_sha256(),
        api_level=api_level,
        duration_seconds=duration_seconds,
        values=dict(values),
    )


def median_luma(device: device_layer.AdbDevice) -> float:
    """
    Return the median luminance of a freshly captured screen, a bounded proxy for what is shown.

    This reads the raw screencap rather than the dial-validated capture, because a baseline arm has
    no dial to validate: the validated read raises when the live wallpaper is not the one on screen,
    which is exactly the state the baseline arms are in.
    """
    _, _, pixels = device_layer.decode_frame(device.run_adb(["exec-out", "screencap"]))
    luma = sorted(
        sum(weight * pixel for weight, pixel in zip(LUMA_WEIGHTS, pixels[index : index + 3], strict=True))
        for index in range(0, len(pixels) - 2, device_layer.PIXEL_STRIDE * LUMA_STRIDE)
    )
    if not luma:
        detail = "the captured frame carried no pixels to measure"
        raise device_layer.ProbeError(detail)
    middle = len(luma) // 2
    return luma[middle] if len(luma) % 2 else (luma[middle - 1] + luma[middle]) / 2.0


def verify_arm(device: device_layer.AdbDevice, arm: Arm, baseline: Baseline | None) -> str:
    """
    Refuse a window whose device does not match the arm it is about to be recorded against.

    The component is the fact that decides the arm, and it is decidable: the home slot names the live
    service when the app is bound and a static component when it is not. Which *static* image a
    baseline carries is not decidable this way, and the screen's own luminance does not decide it
    either — this device's OEM stock wallpaper is a dark starfield, so a brightness threshold would
    refuse valid stock windows while still not separating the two baselines. The luminance is
    recorded with every window as an observation and is not used to admit one.
    """
    component = device_layer.read_wallpaper_component(device.serial)
    if arm is Arm.LIVE:
        if LIVE_COMPONENT_MARKER not in component:
            detail = f"arm A was requested but the home wallpaper is {component}, not the live service"
            raise device_layer.ProbeError(detail)
        return component
    if baseline is None:
        detail = "arm B needs --baseline stock or --baseline black to say which static wallpaper is bound"
        raise device_layer.ProbeError(detail)
    if LIVE_COMPONENT_MARKER in component:
        detail = f"arm B was requested but the live service is still bound as {component}"
        raise device_layer.ProbeError(detail)
    return component


def arm_conditions(device: device_layer.AdbDevice, arm: Arm, baseline: Baseline | None) -> dict[str, object]:
    """Describe the verified arm for the record, including the luminance the guard admitted."""
    return {
        "arm": arm.value,
        "baseline": baseline.value if baseline else None,
        "wallpaper_component": device_layer.read_wallpaper_component(device.serial),
        "median_luma": round(median_luma(device), 2),
        "median_luma_note": MEDIAN_LUMA_NOTE,
    }


def sample_display_states(device: device_layer.AdbDevice, seconds: float) -> list[bool | None]:
    """Sample the display state across a window and return every reading."""
    states: list[bool | None] = []
    remaining = seconds
    while remaining > 0:
        interval = min(SAMPLE_INTERVAL_SECONDS, remaining)
        time.sleep(interval)
        remaining -= interval
        states.append(device.read_screen_on())
        print(f"  {seconds - remaining:6.0f}s: screen_on={states[-1]}", flush=True)
    return states


def run_window(
    device: device_layer.AdbDevice,
    pid: int | None,
    minutes: float,
    *,
    screen_on: bool,
) -> tuple[Window, list[bool | None]]:
    """Sample a bounded window, holding the requested display state for its whole duration."""
    if not screen_on:
        device.run_adb(["shell", "input", "keyevent", "KEYCODE_SLEEP"])
        time.sleep(2.0)
    start = take_battery_sample(device, pid)
    seconds = minutes * 60.0
    states = sample_display_states(device, seconds)
    end = take_battery_sample(device, pid)
    return Window(start_sample=start, end_sample=end, duration_seconds=end.timestamp - start.timestamp), states


def phase_resources(device: device_layer.AdbDevice) -> Record:
    """Record B2: the partial wake locks and pending alarms the app holds, with their commands."""
    pid = device.get_wallpaper_pid()
    uid = read_process_uid(device, pid)
    print(f"$ adb -s {device.serial} shell pidof {device_layer.PACKAGE_NAME} -> {pid}")
    print(f"$ adb -s {device.serial} shell stat -c %u /proc/{pid} -> {uid}")
    if uid is None:
        detail = "the wallpaper has no live process, so its wake locks cannot be attributed"
        raise device_layer.ProbeError(detail)
    wake_locks = count_held_wakelocks(device, uid)
    alarms = count_alarms(device)
    print(f"$ adb -s {device.serial} shell dumpsys power   (PARTIAL_WAKE_LOCK uid={uid}) -> {wake_locks}")
    print(f"$ adb -s {device.serial} shell dumpsys alarm   ({device_layer.PACKAGE_NAME}) -> {alarms}")
    summary = read_batterystats_summary(device)
    print(f"  batterystats corroboration: {summary or 'no matching lines'}")
    record = build_record(
        device,
        "resources",
        0.0,
        wake_locks=wake_locks,
        alarms=alarms,
        wallpaper_pid=pid,
        wallpaper_uid=uid,
        batterystats_summary=summary,
    )
    print(f"wake locks={wake_locks} (budget 0), alarms={alarms} (budget 0)")
    return record


def phase_hidden(device: device_layer.AdbDevice, minutes: float) -> Record:
    """Record B3: hidden-state CPU over a screen-off window, with the visibility sampled."""
    snapshot = DeviceStateSnapshot.capture(device)
    pid = device.get_wallpaper_pid()
    problems: list[str] = []
    try:
        snapshot.pin()
        simulate_unplug(device)
        window, states = run_window(device, pid, minutes, screen_on=False)
    finally:
        problems = undo_phase_changes(device, snapshot)
    hidden_all = states and all(state is False for state in states)
    if not hidden_all:
        detail = f"the display was not off for the whole window: {states}"
        raise device_layer.ProbeError(detail)
    cpu_pct = window.cpu_pct
    if cpu_pct is None:
        detail = "the wallpaper process's CPU counters could not be read over the hidden window"
        raise device_layer.ProbeError(detail)
    print(f"hidden window {window.duration_seconds:.1f}s: screen_on readings={states}")
    print(f"CPU {cpu_pct:.4f}% of one core against the {MAX_HIDDEN_CPU_PCT}% budget")
    return build_record(
        device,
        "hidden",
        window.duration_seconds,
        window_minutes=minutes,
        cpu_pct=cpu_pct,
        hidden_cpu_budget_pct=MAX_HIDDEN_CPU_PCT,
        screen_on_samples=states,
        wall_clock_seconds=round(window.duration_seconds, 1),
        restore_problems=problems,
    )


def phase_visible(
    device: device_layer.AdbDevice,
    *,
    arm: Arm,
    baseline: Baseline | None,
    minutes: float,
) -> Record:
    """Record one B4/B5 window on the home screen after the arm guard admits it."""
    device.wake_screen()
    verify_arm(device, arm, baseline)
    snapshot = DeviceStateSnapshot.capture(device)
    pid = device.get_wallpaper_pid() if arm is Arm.LIVE else None
    conditions = arm_conditions(device, arm, baseline)
    problems: list[str] = []
    print(f"arm guard admitted {conditions['wallpaper_component']} (median luma {conditions['median_luma']})")
    try:
        snapshot.pin()
        simulate_unplug(device)
        window, states = run_window(device, pid, minutes, screen_on=True)
    finally:
        problems = undo_phase_changes(device, snapshot)
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole visible window: {states}"
        raise device_layer.ProbeError(detail)
    if arm is Arm.LIVE and window.cpu_pct is None:
        detail = "the live wallpaper's CPU counters could not be read over a visible window"
        raise device_layer.ProbeError(detail)
    print(f"window {window.duration_seconds:.1f}s: discharge {window.discharge_uah} uAh")
    print(f"discharge rate {window.discharge_rate_ua:.0f} uA, CPU {window.cpu_pct}% of one core")
    return build_record(
        device,
        "visible",
        window.duration_seconds,
        window_minutes=minutes,
        discharge_uah=window.discharge_uah,
        discharge_rate_ua=window.discharge_rate_ua,
        cpu_pct=window.cpu_pct,
        charge_counter_start_uah=window.start_sample.charge_counter_uah,
        charge_counter_end_uah=window.end_sample.charge_counter_uah,
        screen_on_samples=states,
        restore_problems=problems,
        **conditions,
    )


def phase_memory(device: device_layer.AdbDevice, minutes: float) -> Record:
    """Record B6: the total-PSS growth over a long window on the live wallpaper."""
    device.wake_screen()
    verify_arm(device, Arm.LIVE, None)
    snapshot = DeviceStateSnapshot.capture(device)
    pid = device.get_wallpaper_pid()
    problems: list[str] = []
    states: list[bool | None] = []
    try:
        snapshot.pin()
        before = take_battery_sample(device, pid)
        seconds = minutes * 60.0
        states = sample_display_states(device, seconds)
        after = take_battery_sample(device, pid)
    finally:
        problems = undo_phase_changes(device, snapshot)
    if states and not all(state is True for state in states):
        detail = f"the display was not on for the whole memory window: {states}"
        raise device_layer.ProbeError(detail)
    if before.total_pss_kb is None or after.total_pss_kb is None:
        detail = "a total-PSS sample could not be parsed, so growth has no value"
        raise device_layer.ProbeError(detail)
    growth_kb = after.total_pss_kb - before.total_pss_kb
    print(f"total PSS {before.total_pss_kb} -> {after.total_pss_kb} kB over {minutes:.1f} min")
    print(f"growth {growth_kb} kB against the {MAX_PSS_GROWTH_KB} kB budget")
    return build_record(
        device,
        "memory",
        seconds,
        window_minutes=minutes,
        pss_start_kb=before.total_pss_kb,
        pss_end_kb=after.total_pss_kb,
        pss_growth_kb=growth_kb,
        pss_budget_kb=MAX_PSS_GROWTH_KB,
        screen_on_samples=states,
        restore_problems=problems,
    )


def phase_pilot(
    device: device_layer.AdbDevice,
    *,
    arm: Arm,
    baseline: Baseline | None,
    runs: int,
    minutes: float,
) -> list[Record]:
    """Record the repeated windows whose spread is the device's noise floor."""
    records: list[Record] = []
    rates: list[float] = []
    cpu_values: list[float] = []
    for run in range(1, runs + 1):
        print(f"\n--- pilot window {run}/{runs} ---")
        record = phase_visible(device, arm=arm, baseline=baseline, minutes=minutes)
        record.values["run"] = run
        record.values["pilot"] = True
        record.values["runs"] = runs
        records.append(record)
        rate = record.values.get("discharge_rate_ua")
        if not isinstance(rate, int | float):
            detail = "a visible window recorded no discharge rate, so the noise floor has no input"
            raise device_layer.ProbeError(detail)
        rates.append(float(rate))
        cpu_pct = record.values["cpu_pct"]
        if isinstance(cpu_pct, int | float):
            cpu_values.append(float(cpu_pct))
    mean, deviation, half_width = two_sigma_noise_floor(rates)
    floor_pct = (half_width / mean) * 100.0 if mean > 0 else 0.0
    print(f"\nbaseline discharge rate: mean {mean:.0f} uA, sigma {deviation:.0f} uA, 2 sigma {half_width:.0f} uA")
    print(f"noise floor as a share of the mean: {floor_pct:.2f}%")
    records[0].values["noise_floor_pct"] = floor_pct
    records[0].values["noise_floor_rate_ua"] = half_width
    records[0].values["baseline_mean_rate_ua"] = mean
    if cpu_values:
        cpu_mean, cpu_deviation, cpu_half_width = two_sigma_noise_floor(cpu_values)
        records[0].values["cpu_mean_pct"] = cpu_mean
        records[0].values["cpu_noise_floor_pct"] = cpu_half_width
        print(f"CPU: mean {cpu_mean:.4f}%, sigma {cpu_deviation:.4f}, 2 sigma {cpu_half_width:.4f}")
    return records


def numeric_values(records: Sequence[Mapping[str, object]], phase: str, key: str) -> list[float]:
    """Collect one numeric field across a phase's records, skipping records that never carried it."""
    values: list[float] = []
    for record in records:
        if record.get("phase") != phase:
            continue
        value = record.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values


def window_rates(records: Sequence[Mapping[str, object]], arm: str) -> list[float]:
    """Collect the discharge rates of one arm's visible windows, in record order."""
    rates: list[float] = []
    for record in records:
        if record.get("phase") != "visible" or record.get("arm") != arm or record.get("pilot"):
            continue
        rate = record.get("discharge_rate_ua")
        if not isinstance(rate, int | float):
            detail = f"a visible record for arm {arm} carried no discharge rate"
            raise device_layer.ProbeError(detail)
        rates.append(float(rate))
    return rates


def window_cpu(records: Sequence[Mapping[str, object]]) -> list[float]:
    """Collect the live-arm CPU readings from the visible windows."""
    values: list[float] = []
    for record in records:
        if record.get("phase") == "visible" and record.get("arm") == Arm.LIVE.value and not record.get("pilot"):
            value = record.get("cpu_pct")
            if isinstance(value, int | float):
                values.append(float(value))
    return values


def check_budget(label: str, value: float | None, budget: float, unit: str) -> str:
    """Render one pass/fail line, refusing to pass a budget that was never measured."""
    if value is None:
        return f"{label}: NOT MEASURED — no value was recorded, so the budget is neither passed nor failed"
    verdict = "PASS" if value <= budget else "FAIL"
    return f"{label}: {verdict} — {value:.4f}{unit} against {budget}{unit}"


def report_lines(records: Sequence[Mapping[str, object]]) -> list[str]:
    """Build the pass/fail report against the committed thresholds."""
    lines: list[str] = []
    resource_lines = [line for line in records if line.get("phase") == "resources"]
    if resource_lines:
        latest = resource_lines[-1]
        locks = latest.get("wake_locks")
        alarms = latest.get("alarms")
        lines.append(check_budget("B2 wake locks", float(locks) if isinstance(locks, int | float) else None, 0.0, ""))
        lines.append(check_budget("B2 alarms", float(alarms) if isinstance(alarms, int | float) else None, 0.0, ""))
    else:
        lines.append("B2 wake locks and alarms: NOT MEASURED — run the resources phase")

    hidden = numeric_values(records, "hidden", "cpu_pct")
    lines.append(check_budget("B3 hidden CPU", max(hidden) if hidden else None, MAX_HIDDEN_CPU_PCT, "%"))

    baseline_rates = window_rates(records, Arm.BASELINE.value)
    treatment_rates = window_rates(records, Arm.LIVE.value)
    try:
        statistics = paired_statistics(baseline_rates, treatment_rates)
        lines.append(
            f"B4 relative increase: {statistics.relative_delta_pct:.3f}% "
            f"(95% CI upper {statistics.ci_95_upper_pct:.3f}%, {statistics.sample_count} pairs, "
            f"df {statistics.degrees_of_freedom}, t {statistics.t_critical})"
        )
        lines.append(check_budget("B4 point estimate", statistics.relative_delta_pct, FINAL_MAX_VISIBLE_DELTA_PCT, "%"))
        lines.append(check_budget("B4 CI upper bound", statistics.ci_95_upper_pct, FINAL_MAX_VISIBLE_DELTA_PCT, "%"))
    except device_layer.ProbeError as error:
        # A comparison that cannot be computed is reported as not measured, with its reason. It is
        # never a pass, and it must not stop the criteria that *were* measured from being reported.
        lines.append(
            f"B4 relative increase: NOT MEASURED — {error} "
            f"({len(baseline_rates)} baseline and {len(treatment_rates)} live windows recorded)"
        )

    cpu_values = window_cpu(records)
    lines.append(
        check_budget("B5 visible CPU", max(cpu_values) if cpu_values else None, FINAL_MAX_VISIBLE_CPU_PCT, "%")
    )

    growth_values = numeric_values(records, "memory", "pss_growth_kb")
    growth: float | None = growth_values[-1] if growth_values else None
    lines.append(check_budget("B6 PSS growth", growth, MAX_PSS_GROWTH_KB, " kB"))
    lines.append(
        "B1 hidden frames: NOT MEASURED HERE — the frame counter is #125 criterion 1, "
        "and a zero-frame claim needs a console that this protocol does not own"
    )
    return lines


def phase_report(records: Sequence[Mapping[str, object]]) -> None:
    """Print the aggregate report and every unrun check it can see."""
    revisions = {str(record.get("revision")) for record in records}
    api_levels = {record.get("api_level") for record in records}
    print(f"records: {len(records)}")
    print(f"source revisions: {', '.join(sorted(revisions))}")
    print(f"API levels: {', '.join(str(level) for level in sorted(api_levels, key=str))}")
    print(f"committed B4 threshold: {FINAL_MAX_VISIBLE_DELTA_PCT}% relative increase")
    print(f"committed B5 threshold: {FINAL_MAX_VISIBLE_CPU_PCT}% of one core")
    print()
    for line in report_lines(records):
        print(line)


def add_window_arguments(parser: argparse.ArgumentParser, default_minutes: float) -> None:
    """Add the duration option shared by the windowed phases."""
    parser.add_argument(
        "--minutes",
        type=positive_float,
        default=default_minutes,
        help="Length of each measurement window in minutes",
    )


def add_arm_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arm and baseline options shared by the visible and pilot phases."""
    parser.add_argument("--arm", choices=[arm.value for arm in Arm], required=True, help="A live, B baseline")
    parser.add_argument(
        "--baseline",
        choices=[baseline.value for baseline in Baseline],
        default=None,
        help="Which static wallpaper arm B is bound to; required for arm B",
    )


def positive_float(raw: str) -> float:
    """Parse a strictly positive number for the command line."""
    value = float(raw)
    if value <= 0:
        message = f"{raw} is not a positive duration"
        raise argparse.ArgumentTypeError(message)
    return value


def positive_int(raw: str) -> int:
    """Parse a strictly positive integer for the command line."""
    value = int(raw)
    if value <= 0:
        message = f"{raw} is not a positive count"
        raise argparse.ArgumentTypeError(message)
    return value


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command line: one bounded phase per subcommand."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--serial", help="Target ADB serial", default=None)
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Directory the phase records are written to",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("resources", help="B2: wake locks and alarms the app holds")

    hidden = subcommands.add_parser("hidden", help="B3: hidden-state CPU over a screen-off window")
    add_window_arguments(hidden, DEFAULT_HIDDEN_MINUTES)

    visible = subcommands.add_parser("visible", help="B4/B5: one visible window")
    add_arm_arguments(visible)
    add_window_arguments(visible, DEFAULT_WINDOW_MINUTES)

    pilot = subcommands.add_parser("pilot", help="Repeated windows whose spread is the noise floor")
    add_arm_arguments(pilot)
    add_window_arguments(pilot, DEFAULT_WINDOW_MINUTES)
    pilot.add_argument("--runs", type=positive_int, default=MIN_PAIRED_SAMPLES, help="Windows to run")

    memory = subcommands.add_parser("memory", help="B6: total-PSS growth over a long window")
    add_window_arguments(memory, DEFAULT_MEMORY_MINUTES)

    subcommands.add_parser("report", help="Aggregate the records the phases wrote")
    return parser


def dispatch(args: argparse.Namespace, device: device_layer.AdbDevice) -> list[Record]:
    """Run the requested phase and return the records it produced."""
    if args.command == "resources":
        return [phase_resources(device)]
    if args.command == "hidden":
        return [phase_hidden(device, args.minutes)]
    if args.command == "visible":
        return [phase_visible(device, arm=Arm(args.arm), baseline=baseline_of(args), minutes=args.minutes)]
    if args.command == "pilot":
        return phase_pilot(device, arm=Arm(args.arm), baseline=baseline_of(args), runs=args.runs, minutes=args.minutes)
    if args.command == "memory":
        return [phase_memory(device, args.minutes)]
    return []


def baseline_of(args: argparse.Namespace) -> Baseline | None:
    """Read the requested baseline, as `None` when the arm does not need one."""
    return Baseline(args.baseline) if args.baseline else None


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line, run one bounded phase, and write its record."""
    args = build_argument_parser().parse_args(argv)
    state_dir = Path(args.state)
    if args.command == "report":
        try:
            records = load_records(state_dir)
        except device_layer.ProbeError as error:
            print(f"ERROR: {device_layer.error_detail(error)}", file=sys.stderr)
            return 1
        phase_report(records)
        return 0
    serial = device_layer.select_target_serial(args.serial)
    device = device_layer.AdbDevice(serial)
    try:
        sdk = device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace")
        print(f"$ adb -s {serial}  (API {parse_api_level(sdk)})")
        records = dispatch(args, device)
    except (subprocess.SubprocessError, OSError, device_layer.ProbeError) as error:
        print(f"ERROR: {device_layer.error_detail(error)}", file=sys.stderr)
        return 1
    for record in records:
        path = append_record(state_dir, record)
        print(f"recorded {record.phase} -> {path}")
    restore_failures: list[str] = []
    for record in records:
        problems = record.values.get("restore_problems")
        if isinstance(problems, list):
            restore_failures.extend(str(problem) for problem in problems)
    if restore_failures:
        print("DEVICE RESTORE FAILURES:", file=sys.stderr)
        for failure in restore_failures:
            print(f"  - FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
