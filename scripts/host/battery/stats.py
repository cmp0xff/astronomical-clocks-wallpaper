# SPDX-License-Identifier: Apache-2.0
"""Statistical comparisons, noise floors, and budget thresholds; nothing here touches a device."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from host.device import probes

# The acceptance matrix's minimums: a shorter window is a different measurement, so both `--minutes`
# and `report` refuse one.
MINIMUM_HIDDEN_MINUTES: Final = 10.0

MINIMUM_WINDOW_MINUTES: Final = 15.0

MINIMUM_MEMORY_MINUTES: Final = 30.0

# Scheduling jitter is seconds and an interrupted window is minutes, so a 5% shortfall is the widest
# gap that still means the window ran for its whole length.
MINIMUM_DURATION_TOLERANCE: Final = 0.95

# Each windowed phase's documented minimum, keyed by the phase name its records carry, so `report`
# can check a recorded duration against it without knowing how the window was requested.
MINIMUM_SECONDS_BY_PHASE: Final = {
    "hidden": MINIMUM_HIDDEN_MINUTES * 60.0,
    "visible": MINIMUM_WINDOW_MINUTES * 60.0,
    "memory": MINIMUM_MEMORY_MINUTES * 60.0,
}

# Budgets from docs/battery-budget.md. B1 is #125 criterion 1 and is deliberately not measured here:
# the frame console is that issue's, and it lands its own evidence under docs/testing/reports/
# (2026-10-12-test-125-hidden-frames-and-reboot.md) rather than through this protocol.
MAX_HIDDEN_CPU_PCT: Final = 0.1

PROVISIONAL_MAX_VISIBLE_DELTA_PCT: Final = 5.0

PROVISIONAL_MAX_VISIBLE_CPU_PCT: Final = 3.0

MAX_PSS_GROWTH_KB: Final = 6144

# The budget is zero wake locks of *any* type, so only a count whose probe reconciled every
# `<TYPE>_WAKE_LOCK` against the census bounds it. An older partial-only count is a real measurement
# of a narrower criterion, not of this row; an unstamped record is simply not measured here.
WAKE_LOCK_COVERAGE: Final = "all-types"

# Committed from the 2026-10-13 pilot per the document's anti-tuning rule: four 15-minute arm-A
# windows measured a 2-sigma floor of 43997 uA on a 366265 uA mean, 12.0123%. The wider threshold is
# more permissive but honest: below it this device cannot resolve a real difference. The floor is a
# property of this gauge and load, so it must be re-measured per device.
FINAL_MAX_VISIBLE_DELTA_PCT: Final = 12.0123  # greater of the provisional 5% and the measured floor

FINAL_MAX_VISIBLE_CPU_PCT: Final = PROVISIONAL_MAX_VISIBLE_CPU_PCT  # the floor is below the provisional

# Pilot floors are part of the commitment, not the working state: the state file is reset between
# pilot and final run so calibration cannot enter the result.
PILOT_DISCHARGE_NOISE_FLOOR_UA: Final = 43997.06

PILOT_CPU_NOISE_FLOOR_PCT: Final = 0.0894

PILOT_REPORT: Final = "docs/testing/reports/2026-10-13-test-126-drain-comparison-on-battery.md"

# Two-sided 95% Student t values, NIST/SEMATECH e-Handbook 1.3.6.7.3 (retrieved 2026-10-12). Past 30
# degrees of freedom the Cornish-Fisher expansion at `T_TAIL_TERMS` takes over, not the normal value.
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

# The normal value would understate the interval by 4% at 31 degrees of freedom (2.040, not 1.960);
# this expansion reproduces the table to three decimals and tends to the normal value as nu grows.
T_TAIL_TERMS: Final = (4.0, 96.0)

MIN_PAIRED_SAMPLES: Final = 4


class Arm(StrEnum):
    """Which wallpaper a window is recorded against."""

    LIVE = "A"
    BASELINE = "B"


class Baseline(StrEnum):
    """Which static baseline an arm B window is recorded against."""

    STOCK = "stock"
    BLACK = "black"


class VerdictStatus(StrEnum):
    """How one criterion, or one observation, came out."""

    PASS = "PASS"  # noqa: S105 — a verdict label, not a credential
    FAIL = "FAIL"
    NOT_MEASURED = "NOT MEASURED"
    # A criterion belonging to another issue (B1 is #125's frame counter); absence is not a failure.
    OUT_OF_SCOPE = "OUT OF SCOPE"
    # A criterion this device cannot measure, such as the black baseline when no static wallpaper can
    # be set. It is granted only by the run's own `--black-unavailable` declaration, never inferred
    # from absent windows, and the report must say so rather than read as a complete verdict.
    DEVICE_LIMITED = "NOT MEASURED (DEVICE-LIMITED)"
    # A line the report prints beside the verdicts but that is not itself a pass/fail criterion.
    INFO = "INFO"


# An allowlist, not a list of gating statuses: an unestablished criterion gates until listed here.
NON_GATING_STATUSES: Final = frozenset(
    {
        VerdictStatus.PASS,
        VerdictStatus.INFO,
        VerdictStatus.OUT_OF_SCOPE,
        VerdictStatus.DEVICE_LIMITED,
    }
)


@dataclass(frozen=True)
class Verdict:
    """
    One line of the report, as data rather than text.

    Exit status derives from these, so a printed FAIL can never still exit zero.
    """

    label: str
    status: VerdictStatus
    line: str

    @property
    def is_failure(self) -> bool:
        """Whether this verdict should make the report exit non-zero."""
        return self.status not in NON_GATING_STATUSES


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
    # The sd of the per-pair differences: with the interval's `t * sd / sqrt(n)` width, it says how
    # many pairs a future run needs.
    standard_deviation: float


def t_critical_95(degrees_of_freedom: int) -> float:
    """
    Return the two-sided 95% Student t critical value for `degrees_of_freedom`.

    Tabulated values are returned as tabulated; past the table the Cornish-Fisher expansion is used,
    not the normal 1.960. The pair minimum is enforced by `paired_statistics`.
    """
    if degrees_of_freedom < 1:
        detail = f"{degrees_of_freedom} degrees of freedom admits no interval at all"
        raise probes.ProbeError(detail)
    tabulated = T_CRITICAL_95_BY_DF.get(degrees_of_freedom)
    if tabulated is not None:
        return tabulated
    z = NORMAL_T_CRITICAL_95
    first, second = T_TAIL_TERMS
    return (
        z
        + (z**3 + z) / (first * degrees_of_freedom)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (second * degrees_of_freedom**2)
    )


def paired_statistics(baseline_rates: Sequence[float], treatment_rates: Sequence[float]) -> PairedStatistics:
    """
    Compare paired baseline and treatment window rates, with a 95% interval on the increase.

    The per-pair differences remove drift shared by both arms, so the bound measures the comparison.
    """
    if len(baseline_rates) != len(treatment_rates):
        detail = f"pairing needs equal arms but got {len(baseline_rates)} and {len(treatment_rates)} windows"
        raise probes.ProbeError(detail)
    if len(baseline_rates) < MIN_PAIRED_SAMPLES:
        detail = (
            f"{len(baseline_rates)} alternations were recorded but the budget mandates at least "
            f"{MIN_PAIRED_SAMPLES} pairs"
        )
        raise probes.ProbeError(detail)
    diffs = [treatment - baseline for baseline, treatment in zip(baseline_rates, treatment_rates, strict=True)]
    mean_baseline = sum(baseline_rates) / len(baseline_rates)
    mean_treatment = sum(treatment_rates) / len(treatment_rates)
    if mean_baseline <= 0:
        detail = "the baseline mean discharge rate is not positive, so no relative increase exists"
        raise probes.ProbeError(detail)
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
        standard_deviation=math.sqrt(variance),
    )


def two_sigma_noise_floor(values: Sequence[float]) -> tuple[float, float, float]:
    """Return the mean, the sample standard deviation, and the 2-sigma half-width of a series."""
    if len(values) < MIN_PAIRED_SAMPLES:
        detail = f"a noise floor needs at least {MIN_PAIRED_SAMPLES} windows but got {len(values)}"
        raise probes.ProbeError(detail)
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    deviation = math.sqrt(variance)
    return mean, deviation, 2.0 * deviation


def finite_number(value: object, *, what: str, non_negative: bool = False) -> float:
    """
    Return one recorded field as a float, refusing a bool, a non-number, or a non-finite value.

    `bool` is an `int` in Python, so `true` would otherwise read as one lock or a 1 uA rate. A
    non-finite value is the dangerous kind of wrong: `-inf` sorts below every budget and `nan` is
    above nothing. `non_negative` is asked for per field, since B6's growth and B4's delta may
    legitimately be negative.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        detail = f"{what} is {value!r}, which is not a number, so it cannot be read as a measurement"
        raise probes.ProbeError(detail)
    if not math.isfinite(value):
        detail = f"{what} is {value!r}, which is not finite, so it cannot be read as a measurement"
        raise probes.ProbeError(detail)
    if non_negative and value < 0:
        detail = f"{what} is {value!r}, which is negative, and this field cannot be below zero"
        raise probes.ProbeError(detail)
    return float(value)


def numeric_values(
    records: Sequence[Mapping[str, object]], phase: str, key: str, *, non_negative: bool = False
) -> list[float]:
    """
    Collect one numeric field across a phase's records, refusing a value that is not a number.

    A record of another phase, or one that does not carry the key, does not bound the field. A
    present-but-unreadable value is refused: skipping it would hide corruption behind the readable
    records. "Not a number" is `finite_number`'s rule, and `non_negative` describes the field.
    """
    return [
        finite_number(record[key], what=f"a {phase} record's `{key}`", non_negative=non_negative)
        for record in records
        if record.get("phase") == phase and key in record
    ]


def wake_lock_values(records: Sequence[Mapping[str, object]]) -> list[float]:
    """
    Return the wake-lock counts that can bound the all-type budget.

    Every present count is read through `finite_number`, so corruption is refused even without a
    coverage stamp. Only counts stamped all-types are returned: a partial-only census measures a
    narrower criterion. A set of only those records reports NOT MEASURED; a mixed set uses the
    stamped ones and ignores the rest.
    """
    values: list[float] = []
    for record in records:
        if record.get("phase") != "resources" or "wake_locks" not in record:
            continue
        count = finite_number(record["wake_locks"], what="a resources record's `wake_locks`", non_negative=True)
        if "wake_lock_coverage" not in record:
            continue
        coverage = record["wake_lock_coverage"]
        if not isinstance(coverage, str):
            detail = f"a resources record carries wake_lock_coverage={coverage!r}, which is not a string"
            raise probes.ProbeError(detail)
        if coverage == WAKE_LOCK_COVERAGE:
            values.append(count)
    return values


def is_pilot(record: Mapping[str, object]) -> bool:
    """
    Return whether a visible record is a pilot window, refusing a `pilot` flag that is not a boolean.

    An absent flag is an ordinary measurement. A non-boolean is refused because both selectors filter
    on truthiness and `"false"` is truthy, so a hot window would be filed as calibration and dropped.
    """
    if "pilot" not in record:
        return False
    pilot = record["pilot"]
    if not isinstance(pilot, bool):
        detail = f"a {record.get('phase')!r} record carries pilot={pilot!r}, which is not a boolean"
        raise probes.ProbeError(detail)
    return pilot


def alternation_pairs(
    records: Sequence[Mapping[str, object]],
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    """
    Pair live and baseline windows from their recorded order, refusing a malformed sequence.

    File order only means something when the arms alternate exactly. Pilots are calibration, not
    comparison, and `is_pilot` owns that decision.
    """
    sequence = [record for record in records if record.get("phase") == "visible" and not is_pilot(record)]
    if not sequence:
        return []
    arms = [str(record.get("arm")) for record in sequence]
    expected = [Arm.LIVE.value, Arm.BASELINE.value] * (len(sequence) // 2)
    if len(sequence) % 2 != 0 or arms != expected:
        detail = f"the visible windows are not an A/B alternation: recorded arms {arms}"
        raise probes.ProbeError(detail)
    return list(zip(sequence[::2], sequence[1::2], strict=True))


def window_rate(record: Mapping[str, object]) -> float:
    """
    Read one window's discharge rate, refusing a record that has none or a negative one.

    Absent and unreadable are separate refusals with separate messages. A negative rate means the pack
    gained charge, so the record is not a discharge comparison — a different statement from "not a
    number".
    """
    rate = record.get("discharge_rate_ua")
    if rate is None:
        detail = "a visible window recorded no discharge rate, so no comparison can be computed"
        raise probes.ProbeError(detail)
    value = finite_number(rate, what="a visible window's `discharge_rate_ua`")
    if value < 0:
        detail = (
            f"a visible window recorded a negative discharge rate ({value:.0f} uA), which means the pack "
            "gained charge across it; a state file carrying one is not a discharge comparison"
        )
        raise probes.ProbeError(detail)
    return value


def live_windows(records: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """
    Return the visible live windows that are B5 measurements, in recorded order.

    B5 names the live arm alone, so its input is the live windows, not the A/B pairs: a non-alternating
    file must not discard every live reading. Pilots are calibration, not measurement.
    """
    return [
        record
        for record in records
        if record.get("phase") == "visible" and not is_pilot(record) and record.get("arm") == Arm.LIVE.value
    ]


@dataclass(frozen=True)
class CpuSample:
    """One live window's CPU reading, with the length of time that reading was averaged over."""

    cpu_pct: float
    duration_seconds: float


def live_cpu_values(windows: Sequence[Mapping[str, object]]) -> list[CpuSample]:
    """
    Collect each qualifying live window's CPU reading with the window it was measured over.

    B5 is a rate and windows need not be the same length, so the duration travels with the reading for
    `duration_weighted_mean`. Both fields are non-negative: `-inf` would sort below the budget.
    """
    samples: list[CpuSample] = []
    for window in windows:
        cpu = window.get("cpu_pct")
        if cpu is None:
            detail = "a live window recorded no CPU reading, so B5 has no input"
            raise probes.ProbeError(detail)
        duration = window.get("duration_seconds")
        if duration is None:
            detail = "a live window recorded no `duration_seconds`, so its CPU reading cannot be weighed"
            raise probes.ProbeError(detail)
        samples.append(
            CpuSample(
                cpu_pct=finite_number(cpu, what="a live window's `cpu_pct`", non_negative=True),
                duration_seconds=finite_number(duration, what="a live window's `duration_seconds`", non_negative=True),
            )
        )
    return samples


def duration_weighted_mean(samples: Sequence[CpuSample]) -> float:
    """
    Return the share of one core the live windows were used at, each reading weighted by its window.

    The criterion is a rate over time: three 15-minute windows at 1% beside one 60-minute window at 8%
    is 2.75% per window and 5% over time, and only the weighted mean states the latter.
    """
    total_seconds = sum(sample.duration_seconds for sample in samples)
    if total_seconds <= 0.0:
        detail = "the live windows add up to no measured time, so B5 has no rate to average"
        raise probes.ProbeError(detail)
    return sum(sample.cpu_pct * sample.duration_seconds for sample in samples) / total_seconds
