# SPDX-License-Identifier: Apache-2.0
"""
Judgement half of the battery protocol: records in, verdicts out.

`scripts/battery_protocol.py` measures; this module decides. Everything here takes the record dicts
a phase wrote and turns them into the lines `docs/battery-budget.md` is read against — the committed
budgets and their arithmetic, the pairing rule, the measurement identity a set has to share, and the
report's own exit status. None of it touches a device, and it imports nothing from the measuring
half, so the decision rules can be read and tested without one.

The dependency runs one way. `battery_protocol.py` imports this module; this module imports only the
standard library and `device_layer`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

import device_layer

# The window lengths the acceptance matrix documents. They are minimums rather than defaults: a
# shorter window is a different measurement, so `--minutes` refuses one and `report` refuses a record
# that carries one. The default is the minimum because that is the documented cost of the criterion,
# not because a longer window is worse.
MINIMUM_HIDDEN_MINUTES: Final = 10.0
MINIMUM_WINDOW_MINUTES: Final = 15.0
MINIMUM_MEMORY_MINUTES: Final = 30.0
DEFAULT_WINDOW_MINUTES: Final = MINIMUM_WINDOW_MINUTES
DEFAULT_HIDDEN_MINUTES: Final = MINIMUM_HIDDEN_MINUTES
DEFAULT_MEMORY_MINUTES: Final = MINIMUM_MEMORY_MINUTES
# The recorded wall-clock of a window that ran as documented lands within a few seconds of its
# requested length on either side; scheduling jitter is seconds and an interrupted window is minutes,
# so a 5% shortfall is the widest gap that still means "this window ran for its whole length".
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
# The coverage a `resources` record's wake-lock count was measured with. The budget is zero wake
# locks *of any type*, so a count bounds it only when the probe reconciled every `<TYPE>_WAKE_LOCK`
# entry against the dump's census. An earlier revision enumerated `PARTIAL_WAKE_LOCK` alone and
# recorded that count under the same `wake_locks` key; those records are real measurements of the
# narrower criterion, but they are not measurements of this budget, so the record carries the
# coverage it actually had and `report` can tell the two apart. A record written before the stamp
# existed is not refused — it is simply not measured for this row.
WAKE_LOCK_COVERAGE: Final = "all-types"

# Committed before the final run from the pilot's observed noise floors, per the document's own
# anti-tuning rule; `report` reads these two constants, so a change here is what "committing the
# threshold" means. The 2026-10-13 pilot measured the live discharge channel on a device that was
# genuinely discharging: four 15-minute arm-A windows gave a mean of 366265 uA with a 2 sigma of
# 43997 uA, which is 12.0123% of the mean. That floor is wider than the provisional 5%, so the B4
# threshold rises to it, which is the document's rule working as intended: a larger tolerated
# increase makes the criterion **more permissive**, not harder, because a live-versus-baseline
# increase up to 12.0123% can now pass where 5% would have failed it. What the wider floor buys is
# honesty rather than leniency: below it this device cannot tell a real difference from its own
# variation, so the alternative to a permissive threshold is a criterion that reports a difference it
# has not resolved. The floor is a property of this device's gauge and load rather than evidence
# about the wallpaper: a worn cell and a gauge whose own step is 4.5 mAh both widen it, and it
# has to be re-measured per device. The CPU channel's floor is 0.0894% of one core, below the
# provisional 3%, so the B5 threshold stays as stated.
FINAL_MAX_VISIBLE_DELTA_PCT: Final = 12.0123  # greater of the provisional 5% and the measured floor
FINAL_MAX_VISIBLE_CPU_PCT: Final = PROVISIONAL_MAX_VISIBLE_CPU_PCT  # the floor is below the provisional
# The pilot's floors are part of the commitment, not of the working state: a state file is reset
# between the pilot and the final run precisely so the calibration cannot enter the result, so the
# floors are carried here where the thresholds they justify are.
PILOT_DISCHARGE_NOISE_FLOOR_UA: Final = 43997.06
PILOT_CPU_NOISE_FLOOR_PCT: Final = 0.0894
PILOT_REPORT: Final = "docs/testing/reports/2026-10-13-test-126-drain-comparison-on-battery.md"

# Two-sided 95% Student t critical values by degrees of freedom, from the table in NIST/SEMATECH
# e-Handbook of Statistical Methods, section 1.3.6.7.3 (retrieved 2026-10-12), which is the free
# redistillation of the standard tables. Past 30 degrees of freedom the table stops and the expansion
# at `T_TAIL_TERMS` takes over; the normal value is deliberately *not* used there.
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
# Above the table the normal value is not good enough: the two-sided 95% t at 31 degrees of freedom
# is 2.040, and returning 1.960 there would understate the interval by 4%. The Cornish-Fisher
# expansion of the t quantile in powers of 1/nu is used instead, which reproduces the table to three
# decimals at its top and tends to the normal value as nu grows.
T_TAIL_TERMS: Final = (4.0, 96.0)
MIN_PAIRED_SAMPLES: Final = 4
# The fields that say a record was taken from the same device, build, configuration, and channel, so
# aggregating a set that mixes them would combine measurements that are not comparable. Both the
# threshold and the noise floor behind it are per device, so the device itself is part of the
# identity: two devices at the same build and API level are still two measurements. `revision` is
# deliberately not one of them: a harness revision that changes only documentation or the report's
# own aggregation does not change how a window was measured, and the reports say when that happened.
IDENTITY_FIELDS: Final = ("device_id", "apk_sha256", "api_level", "power_source", "clk_tck")
# The identity fields the phases write as integers; the other three are text. The reader below checks
# the kind against this, because the report is fed a state file it did not write: a scalar of the
# *other* kind — `"clk_tck": "100"`, `"power_source": 7` — is a value no phase produces, and reading
# it as an identity puts a value the run never measured at the head of the report's own verdicts. The
# kinds are the writers' own, checked against the records of the 2026-10-16 device pass.
INTEGER_IDENTITY_FIELDS: Final = ("api_level", "clk_tck")


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
    # A criterion that belongs to another issue and is deliberately not measured here, so its absence
    # is not a failure of this run. B1 is #125's frame counter.
    OUT_OF_SCOPE = "OUT OF SCOPE"
    # A criterion the device cannot measure at all, so its absence is recorded rather than gated and
    # rather than left to look measured. The black static baseline is the one case: the harness never
    # swaps a wallpaper, setting one rewrites the home image, and on a production build without root
    # there is no way to put the stock image back — so the document requires that secondary baseline
    # only where the device permits setting one, and its absence is not a failure of the run while the
    # comparison the criterion is gated on stays the stock one. The report has to say so out loud,
    # which is what this status is for: silence here would read as a complete verdict. It is granted
    # only on the run's own declaration — `report --black-unavailable <reason>` — never inferred from
    # the windows being absent, because a run that simply skipped that arm leaves the same state file
    # as one on a device that cannot set a static wallpaper.
    DEVICE_LIMITED = "NOT MEASURED (DEVICE-LIMITED)"
    # A line the report prints beside the verdicts but that is not itself a pass/fail criterion.
    INFO = "INFO"


# The statuses a report may still exit zero on. This is an allowlist rather than a list of the ones
# that gate: a member added later describes something this protocol has not established, and the safe
# default for an unestablished criterion is to gate until it is deliberately listed here.
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
    One line of the report, as data rather than as text.

    The report's exit status is derived from these, so a FAIL can never be printed and still leave
    the command exiting zero: automation reads the status, not the printed string.
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
    # The sd of the per-pair differences, in the same unit as the means. The interval's width is
    # `t * sd / sqrt(n)`, so this is the one number that says how many pairs a future run needs —
    # a report that carries it lets the next run's n be computed instead of guessed.
    standard_deviation: float


def t_critical_95(degrees_of_freedom: int) -> float:
    """
    Return the two-sided 95% Student t critical value for `degrees_of_freedom`.

    The table is tabulated to 30 degrees of freedom. Past it the value is *not* the normal 1.960, which
    would understate the interval: the Cornish-Fisher expansion of the t quantile in powers of 1/nu is
    used instead, as the terms above it describe. Every tabulated value is returned as tabulated, so a
    caller with two or three pairs reads 4.303 or 3.182 rather than the four-pair 3.182 the previous
    two-branch lookup collapsed them to. How many pairs a comparison needs is enforced by
    `paired_statistics`, which is where the sample count is known.
    """
    if degrees_of_freedom < 1:
        detail = f"{degrees_of_freedom} degrees of freedom admits no interval at all"
        raise device_layer.ProbeError(detail)
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
        standard_deviation=math.sqrt(variance),
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


def finite_number(value: object, *, what: str, non_negative: bool = False) -> float:
    """
    Return one recorded field as a float, refusing a value that cannot be a measurement of it.

    Every reader in this module reads numbers out of a state file it did not write, and every one of
    them has to refuse the same things. Four of them used to hand-roll a partially overlapping guard
    of their own, and three of those were weaker than the others, so the rule lives here instead —
    one place that can be read, tested, and cannot drift from itself.

    `bool` is refused first because it is an `int` in Python: `"wake_locks": true` would otherwise
    read as one lock, and `"discharge_rate_ua": true` as a 1 uA rate. A non-number is refused because
    there is no measurement in it. A non-finite value is refused because it is the dangerous kind of
    wrong rather than merely absent: `-inf` sorts below every budget, so it passes a maximum-is-zero
    check it never measured, and `nan` is not above anything.

    `non_negative` is off by default and has to be asked for at each call site, because a signed
    quantity is the commoner case here: B6's PSS growth is signed because memory can legitimately
    shrink, and B4's verdict is a relative increase that may legitimately be negative. Only a field
    that cannot go below zero — a wake-lock count, a percentage of one core — asks for the refusal,
    so leaving it off is the default an unsigned caller gets by saying nothing.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        detail = f"{what} is {value!r}, which is not a number, so it cannot be read as a measurement"
        raise device_layer.ProbeError(detail)
    if not math.isfinite(value):
        detail = f"{what} is {value!r}, which is not finite, so it cannot be read as a measurement"
        raise device_layer.ProbeError(detail)
    if non_negative and value < 0:
        detail = f"{what} is {value!r}, which is negative, and this field cannot be below zero"
        raise device_layer.ProbeError(detail)
    return float(value)


def numeric_values(
    records: Sequence[Mapping[str, object]], phase: str, key: str, *, non_negative: bool = False
) -> list[float]:
    """
    Collect one numeric field across a phase's records, refusing a value that is not a number.

    A record of another phase is not asked for the field, and a record that does not carry it at all
    does not bound it — an older revision that never recorded the field has nothing to say. A record
    that *does* carry the field with something else is a different thing: corrupt or hand-edited
    state. Skipping it would judge the budget on whichever records happened to parse, so a set
    holding one valid zero and one unreadable value would report a pass, and the unreadable record
    would be hidden behind the readable one rather than named.

    What "not a number" means is `finite_number`'s rule, so this reader, the visible-window readers,
    and the duration reader refuse the same values for the same reasons. `non_negative` is passed
    through rather than decided here, because it is a property of the field and not of the phase: B2's
    counts and B3's percentage ask for it, and B6's signed growth does not.
    """
    values: list[float] = []
    for record in records:
        if record.get("phase") != phase:
            continue
        if key not in record:
            continue
        values.append(finite_number(record[key], what=f"a {phase} record's `{key}`", non_negative=non_negative))
    return values


def wake_lock_values(records: Sequence[Mapping[str, object]]) -> list[float]:
    """
    Return the wake-lock counts that can bound the all-type budget.

    Every `resources` record's count is still read through `finite_number`, so a count that is
    present and unreadable is refused wherever it appears — including on a record that does not carry
    a coverage stamp, which is the same corrupt-state refusal `numeric_values` makes for the alarms
    and jobs rows. Only the counts whose record says it enumerated every lock type are *returned*:
    a partial-only census measures a narrower criterion, and its number cannot stand for the all-type
    count in either direction. A set holding only those records reports the row as NOT MEASURED
    rather than passing a budget nothing measured; a set that mixes them bounds the budget with the
    records that did measure it and ignores the ones that did not.
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
            raise device_layer.ProbeError(detail)
        if coverage == WAKE_LOCK_COVERAGE:
            values.append(count)
    return values


def is_pilot(record: Mapping[str, object]) -> bool:
    """
    Return whether a visible record is a pilot window, refusing a `pilot` flag that is not a boolean.

    A record that does not carry the flag is a measurement. The flag is written by the pilot phase and
    not by the visible phase, so an absent key is the ordinary shape of a visible record rather than a
    revision that had nothing to record, and it means "not a pilot". A record that carries the flag
    with anything but a boolean is refused, because both selectors below filter on truthiness and
    `"false"` is a truthy string in Python: a hot live window carrying one would be filed as
    calibration and dropped from B4 and B5 alike, and a failing pair carrying one would leave the
    report exiting zero over measurements it never judged.
    """
    if "pilot" not in record:
        return False
    pilot = record["pilot"]
    if not isinstance(pilot, bool):
        detail = f"a {record.get('phase')!r} record carries pilot={pilot!r}, which is not a boolean"
        raise device_layer.ProbeError(detail)
    return pilot


def alternation_pairs(
    records: Sequence[Mapping[str, object]],
) -> list[tuple[Mapping[str, object], Mapping[str, object]]]:
    """
    Pair live and baseline windows from their recorded order, refusing a malformed sequence.

    Pairing the nth baseline window with the nth live window by file order would silently pair
    windows that were not taken under the same conditions; the sequence has to be an alternation
    before that pairing means anything. Pilot windows are excluded: they are the calibration, not
    the comparison, and `is_pilot` is what decides that — a flag that is not a boolean is refused
    there rather than read as one.
    """
    sequence = [record for record in records if record.get("phase") == "visible" and not is_pilot(record)]
    if not sequence:
        return []
    arms = [str(record.get("arm")) for record in sequence]
    expected = [Arm.LIVE.value, Arm.BASELINE.value] * (len(sequence) // 2)
    if len(sequence) % 2 != 0 or arms != expected:
        detail = f"the visible windows are not an A/B alternation: recorded arms {arms}"
        raise device_layer.ProbeError(detail)
    return [(sequence[index], sequence[index + 1]) for index in range(0, len(sequence), 2)]


def window_rate(record: Mapping[str, object]) -> float:
    """
    Read one window's discharge rate, refusing a record that has none or a negative one.

    An absent field and an unreadable one are separate cases because they are separate things in the
    state file: a window that recorded no rate at all — the field deleted, or written null — is one
    an older revision or a silent gauge wrote, while `true`, `-inf`, or a string is a corrupt field.
    Both refuse, and each says which it saw rather than sharing one message. The
    negative case keeps a branch of its own rather than taking `finite_number`'s, because the reason
    it is refused is this reader's: a rate below zero means the pack gained charge across the window,
    so the record is not a discharge comparison at all. That is a different statement from "this is
    not a number", and `NegativeDischargeRecordTest` pins it.
    """
    rate = record.get("discharge_rate_ua")
    if rate is None:
        detail = "a visible window recorded no discharge rate, so no comparison can be computed"
        raise device_layer.ProbeError(detail)
    value = finite_number(rate, what="a visible window's `discharge_rate_ua`")
    if value < 0:
        detail = (
            f"a visible window recorded a negative discharge rate ({value:.0f} uA), which means the pack "
            "gained charge across it; a state file carrying one is not a discharge comparison"
        )
        raise device_layer.ProbeError(detail)
    return value


def live_windows(records: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """
    Return the visible live windows that are B5 measurements, in recorded order.

    B5 is the live arm's own CPU channel — the document's B5 row names the process ticks during a
    visible home-screen window and no baseline — so its input is the live windows and not the A/B
    pairs. A live window's `cpu_pct` is the same reading whether or not the window it follows recorded
    a baseline, and B4's pairing rule is a rule about what can be *compared*: reading B5 from the pairs
    made a criterion that needs no comparison depend on one, so an unmatched final live window — which
    makes the sequence a non-alternation — discarded every live reading on the file and left B5 saying
    no live window had been recorded. Pilots are excluded for the same reason they are excluded from
    the comparison: they are the calibration, not the measurement.
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
    Collect each qualifying live window's CPU reading together with the window it was measured over.

    The window's length travels with the reading because B5 is a rate and the windows need not be the
    same length: the pilot's own four recorded windows ran 918.7 s, 919.8 s, 917.8 s and 914.7 s, and
    a run may ask for a different `--minutes` per window. `duration_weighted_mean` is what turns the
    samples back into the share of one core the budget is stated in.

    A percentage of one core cannot be negative, so this reader asks `finite_number` for that refusal
    too. The case that matters most here is the non-finite one: `-inf` satisfies an `isinstance`
    check and sorts below the budget, so three real readings beside one `-inf` used to average to a
    pass over a reading nothing measured.
    """
    samples: list[CpuSample] = []
    for window in windows:
        cpu = window.get("cpu_pct")
        if cpu is None:
            detail = "a live window recorded no CPU reading, so B5 has no input"
            raise device_layer.ProbeError(detail)
        duration = window.get("duration_seconds")
        if duration is None:
            detail = "a live window recorded no `duration_seconds`, so its CPU reading cannot be weighed"
            raise device_layer.ProbeError(detail)
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

    A mean over the windows counts a 60-minute window the same as a 15-minute one, which is not the
    quantity the budget is stated in: three 15-minute windows at 1% beside one 60-minute window at 8%
    is 2.75% counted per window and 5% over the time the readings came from. The criterion is a rate —
    visible CPU over at least 15 minutes — so the aggregate has to be the one that estimates a rate
    from windows of different lengths, and the unweighted mean does not.
    """
    total_seconds = sum(sample.duration_seconds for sample in samples)
    if total_seconds <= 0.0:
        detail = "the live windows add up to no measured time, so B5 has no rate to average"
        raise device_layer.ProbeError(detail)
    return sum(sample.cpu_pct * sample.duration_seconds for sample in samples) / total_seconds


def identity_value(record: Mapping[str, object], identity_field: str) -> object:
    """
    Return one record's value for an identity field, refusing a value that is not a scalar.

    The identity readers below all compare their field through a set, so the set *is* the
    deduplication: a value it cannot hold cannot be compared with any other, and one it can hold but
    no record writes — a boolean — would be compared as though it named a build or a tick rate.
    Either way the field cannot be shown to agree, which is the only thing the checks are for, so
    both are refused here rather than read.

    It is refused here rather than at each set, because the refusal is not one site's business:
    `check_measurement_identity` builds a set that raises `TypeError: unhashable type` out of a
    traceback for a list, dict or set — not the named `ERROR` this module's readers promise for
    state it did not write — and `phase_report` builds two more from `IDENTITY_FIELDS` whose
    validity today rests only on that first check happening to run before them. Stating the rule
    once means no site depends on call ordering to be correct.

    What it admits is exactly what the phase writers put in these fields, because the report is fed a
    state file it did not write. Each field is written as one kind of thing — `api_level` and
    `clk_tck` are integers, `device_id`, `apk_sha256` and `power_source` are text, and any of them may
    be `null` — so a scalar of the *other* kind is refused for the reason a `bool` is: it is a value
    no record writes. `"clk_tck": "100"` and `"power_source": 7` are the cases, and admitting them
    would let a corrupt state file pass the gate while the report printed the value as the identity
    the run was measured under — the complaint the identity check already makes about a set whose
    every value is `null`.

    That check settles the non-finite case without a rule of its own: `NaN` and `inf` are floats, and
    no identity field is written as one. It is not a hypothetical — the state file is JSON Lines read
    with `json.loads`, which accepts `NaN` and `Infinity` by default, so a hand-edited file reaches
    here with a float in an identity field.

    `bool` is refused for the reason `finite_number` gives: it is an `int` in Python, so
    `"clk_tck": true` would read as one tick and `"api_level": false` as API 0. It is, strictly, a
    scalar, and that is exactly why it has to be named: nothing a record writes for an identity
    field is a truth value. A null is admitted, because `"apk_sha256": null` is the legitimate
    "unknown build" every pre-build record carries — refusing an unknown identity is the identity
    check's decision, not this reader's.
    """
    value = record.get(identity_field)
    if isinstance(value, bool) or (value is not None and not isinstance(value, str | int | float)):
        detail = (
            f"a {record.get('phase')!r} record's {identity_field} is {value!r}, which is not a scalar, "
            "so it cannot be read as a measurement identity"
        )
        raise device_layer.ProbeError(detail)
    expected = int if identity_field in INTEGER_IDENTITY_FIELDS else str
    if value is not None and not isinstance(value, expected):
        written_as = "integer" if expected is int else "text"
        detail = (
            f"a {record.get('phase')!r} record's {identity_field} is {value!r}, which is not the "
            f"{written_as} this field is written as, so it cannot be read as a measurement identity"
        )
        raise device_layer.ProbeError(detail)
    return value


def check_measurement_identity(records: Sequence[Mapping[str, object]]) -> None:
    """
    Refuse a record set that mixes builds, API levels, power modes, or tick rates.

    Every record names the identity it was measured under. Aggregating records that disagree on any of
    it would combine measurements that are not comparable, so the report refuses and names the
    distinct values instead of producing one verdict from two builds. A field a record does not carry
    at all is not a disagreement: an older phase record that never recorded the field simply does not
    bound it. A field the record *does* carry with a null value is a value, not an absence — a record
    written before the APK was built carries `"apk_sha256": null`, meaning "unknown build", and that
    must not aggregate with a named one.

    A value that is not a scalar of the kind the field is written as is neither compared nor skipped:
    `identity_value` refuses it, because the set is the comparison, and a value the set cannot hold,
    or holds without that value being an identity of that field, cannot be shown to agree with the
    others.
    """
    for identity_field in IDENTITY_FIELDS:
        values = {identity_value(record, identity_field) for record in records if identity_field in record}
        if not values:
            continue
        if values == {None}:
            # Every record that carries the field says "unknown", which is not an identity any more
            # than two disagreements are: the set is homogeneous and still cannot say which build it
            # measured, so aggregating it would produce a verdict whose subject is unnamed.
            detail = (
                f"every record that carries {identity_field} records no value for it, so the set cannot "
                "say which measurement identity it was taken under; re-run the phases against one build "
                "and configuration"
            )
            raise device_layer.ProbeError(detail)
        if len(values) > 1:
            named = ", ".join("unknown" if value is None else str(value) for value in sorted(values, key=str))
            detail = (
                f"the record set mixes {identity_field} values ({named}), so it combines measurements "
                "from different measurement identities; re-run the phases against one build and configuration"
            )
            raise device_layer.ProbeError(detail)


def check_budget(label: str, value: float | None, budget: float, unit: str) -> Verdict:
    """Build one pass/fail verdict, refusing to pass a budget that was never measured."""
    if value is None:
        return Verdict(
            label,
            VerdictStatus.NOT_MEASURED,
            f"{label}: NOT MEASURED — no value was recorded, so the budget is neither passed nor failed",
        )
    status = VerdictStatus.PASS if value <= budget else VerdictStatus.FAIL
    return Verdict(label, status, f"{label}: {status.value} — {value:.4f}{unit} against {budget}{unit}")


def collect_restore_problems(records: Sequence[Mapping[str, object]]) -> list[str]:
    """
    Collect every restore problem the records carry, so a failed restore is never silent.

    A record that does not carry the field is a record whose phase collected no problems, which is
    what the field being absent means. That absence is a membership test and not `record.get(...) is
    None`, because `dict.get` conflates the two cases: it answers `None` for a record that never had
    the key and for an explicit `"restore_problems": null` alike, so a null would have been skipped as
    though the phase had written a list of no problems. The key being present is what makes its value
    readable, and a present value has to be a list. A record that carries it with something other than
    a list is refused rather than ignored: a failed restore is the one thing on a record that says the
    device was left changed, and reading `"airplane mode was not restored"` — a string where the
    phases write a list — as no problem would let the report exit zero over an explicitly recorded
    one.
    """
    problems: list[str] = []
    for record in records:
        if "restore_problems" not in record:
            continue
        found = record["restore_problems"]
        if not isinstance(found, list):
            detail = (
                f"a {record.get('phase')!r} record carries restore_problems={found!r}, which is not a "
                "list, so whether its restore succeeded cannot be read from this state file"
            )
            raise device_layer.ProbeError(detail)
        problems.extend(str(problem) for problem in found)
    return problems


def short_window_reason(record: Mapping[str, object]) -> str | None:
    """
    Return why a record's window is not the length its criterion documents, or None when it is.

    The CLI refuses a `--minutes` below the documented minimum, but the report is fed a state file it
    did not write: it may have been recorded by an older harness, by a hand-edit, or by a phase whose
    clock was not the one asked for. A record that cannot be shown to have run for its documented
    length is not the measurement the criterion is defined on, so it is named and excluded from the
    aggregation rather than averaged in as though a six-second window were a fifteen-minute one.
    """
    phase = str(record.get("phase"))
    minimum = MINIMUM_SECONDS_BY_PHASE.get(phase)
    if minimum is None:
        return None
    duration = record.get("duration_seconds")
    threshold = minimum * MINIMUM_DURATION_TOLERANCE
    # This reader is deliberately not routed through `finite_number`: its refusal is a returned reason
    # rather than a raised `ProbeError`, and a negative duration is a legitimate input that the
    # below-threshold branch already names. The `bool` check is still `finite_number`'s first rule, for
    # the same reason — `true` is an `int`, so it would reach the threshold comparison below as a
    # 1.0-second window and be reported as a window that ran one second, a length the record does not
    # record. The guard covers absence and a value that is not a length of time alike, so its message
    # names the value it saw rather than asserting the field is missing.
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        return (
            f"a {phase} record carries {duration!r} for `duration_seconds`, which is not a length of "
            f"time, so its window cannot be shown to be the documented {minimum / 60.0:.0f} minutes"
        )
    # `inf` and `nan` are written by a hand-edit or a corrupt record and neither is below the
    # threshold — `nan` is not below anything — so a comparison alone would admit them as windows of
    # the documented length. The CLI refuses them by name for the same reason; so does this.
    if not math.isfinite(duration):
        return (
            f"a {phase} record's window ran {duration!r}s, which is not a finite length of time, so it "
            f"cannot be shown to be the documented {minimum / 60.0:.0f} minutes"
        )
    if duration < threshold:
        return (
            f"a {phase} record's window ran {duration:.1f}s, below the documented "
            f"{minimum / 60.0:.0f} minutes ({threshold:.0f}s at the {MINIMUM_DURATION_TOLERANCE:.0%} tolerance)"
        )
    return None


def black_baseline_verdict(declared_reason: str | None) -> Verdict:
    """
    Build B4's secondary-baseline verdict from the run's declaration, or from its absence.

    The black static baseline is required only where the device permits setting one, and the harness
    cannot tell "this device cannot set a static wallpaper" from "this run did not try": both leave the
    state file without those windows. Inferring the exemption from that absence is what let a run skip
    the document's secondary baseline and still exit zero, so the exemption is a declaration instead \u2014
    `report --black-unavailable <reason>`. A run without one carries the criterion as unmeasured, which
    gates like any other unmeasured criterion; a run with one has the reason quoted into the line, so
    the declaration is part of the output the report document carries.
    """
    if declared_reason:
        return Verdict(
            "B4 vs black",
            VerdictStatus.DEVICE_LIMITED,
            "B4 vs black: NOT MEASURED (DEVICE-LIMITED) \u2014 the run declared that this device cannot "
            f"set a static wallpaper: {declared_reason}. That baseline is required only where the device "
            "permits setting one, so the dark-image comparison is left uncalibrated rather than assumed "
            "equal to stock",
        )
    return Verdict(
        "B4 vs black",
        VerdictStatus.NOT_MEASURED,
        "B4 vs black: NOT MEASURED - no black baseline windows were recorded and the run did not "
        "declare the device unable to set one, so the document's secondary baseline is unmeasured "
        "rather than exempt; pass --black-unavailable <reason> to record that the device is the reason",
    )


def b4_baseline_verdicts(
    pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    *,
    black_unavailable: str | None = None,
) -> list[Verdict]:
    """
    Build B4's verdicts, one block per documented baseline rather than one per recorded baseline.

    A block per *observed* baseline is what let a stock-only state file carry a complete-looking
    verdict: the secondary comparison was simply absent, and nothing in the report said so. The stock
    comparison is the one B4 is gated on. The black static baseline is the document's secondary one, so
    a run without those windows says so explicitly instead of leaving the reader to notice a missing
    block \u2014 and says whether the device or the run is the reason (see `black_baseline_verdict`).

    That one-block-per-documented-baseline shape is also why a label the document does not name is
    refused rather than skipped: an arm B window labeled something else is selected by no block, so the
    pair and every measurement in it would be absent from the whole report. The label is the one field
    here whose *absence* is refused too. Everywhere else a missing field means an older revision had
    nothing to record, but an arm B window's baseline is what places the pair in a comparison at all,
    and inferring the primary baseline from a missing label would be guessing what the file meant.
    """
    verdicts: list[Verdict] = []
    baseline_labels = (Baseline.STOCK.value, Baseline.BLACK.value)
    for _, base in pairs:
        label = base.get("baseline")
        if label not in baseline_labels:
            detail = (
                f"a visible arm B window carries baseline={label!r}, which is not one of the document's "
                f"baselines ({' and '.join(baseline_labels)}), so the pair it belongs to stands outside "
                "every comparison this report prints"
            )
            raise device_layer.ProbeError(detail)
    for baseline in baseline_labels:
        selected = [(live, base) for live, base in pairs if base.get("baseline") == baseline]
        if not selected:
            if baseline == Baseline.BLACK.value:
                verdicts.append(black_baseline_verdict(black_unavailable))
            else:
                verdicts.append(
                    Verdict(
                        f"B4 vs {baseline}",
                        VerdictStatus.NOT_MEASURED,
                        f"B4 vs {baseline}: NOT MEASURED - no {baseline} baseline windows were "
                        "recorded, so the comparison the criterion is gated on was not measured",
                    )
                )
            continue
        try:
            statistics = paired_statistics(
                [window_rate(base) for _, base in selected], [window_rate(live) for live, _ in selected]
            )
        except device_layer.ProbeError as error:
            verdicts.append(
                Verdict(
                    f"B4 vs {baseline}",
                    VerdictStatus.NOT_MEASURED,
                    f"B4 vs {baseline}: NOT MEASURED - {error} ({len(selected)} pairs recorded)",
                )
            )
            continue
        verdicts.append(
            Verdict(
                f"B4 vs {baseline}",
                VerdictStatus.INFO,
                f"B4 vs {baseline} relative increase: {statistics.relative_delta_pct:.3f}% "
                f"(95% CI upper {statistics.ci_95_upper_pct:.3f}%, {statistics.sample_count} pairs, "
                f"df {statistics.degrees_of_freedom}, t {statistics.t_critical}, "
                f"sigma_d {statistics.standard_deviation:.0f} uA = "
                f"{statistics.standard_deviation / statistics.mean_baseline * 100:.3f}% of the baseline)",
            )
        )
        for label, value in (
            ("point estimate", statistics.relative_delta_pct),
            ("CI upper bound", statistics.ci_95_upper_pct),
        ):
            verdicts.append(check_budget(f"B4 vs {baseline} {label}", value, FINAL_MAX_VISIBLE_DELTA_PCT, "%"))
    return verdicts


def report_lines(records: Sequence[Mapping[str, object]], *, black_unavailable: str | None = None) -> list[Verdict]:
    """Build the pass/fail verdicts against the committed thresholds."""
    verdicts: list[Verdict] = []
    problems = collect_restore_problems(records)
    if problems:
        verdicts.append(
            Verdict(
                "device restore",
                VerdictStatus.FAIL,
                f"DEVICE RESTORE: FAIL - {len(problems)} problem(s) were recorded",
            )
        )
        verdicts.extend(Verdict("device restore detail", VerdictStatus.INFO, f"  - {p}") for p in problems)

    # A window below its documented length is not the measurement its criterion is defined on, so it
    # is failed and dropped before anything is aggregated from it.
    measured = [record for record in records if short_window_reason(record) is None]
    short = [str(reason) for record in records if (reason := short_window_reason(record)) is not None]
    if short:
        verdicts.append(
            Verdict(
                "window durations",
                VerdictStatus.FAIL,
                f"WINDOW DURATIONS: FAIL - {len(short)} record(s) below their documented minimum",
            )
        )
        verdicts.extend(Verdict("window duration detail", VerdictStatus.INFO, f"  - {problem}") for problem in short)

    # B2 is a maximum across every resources record, not the last one: the state file is append-only,
    # so a rerun after a nonzero census must not hide the earlier failure behind a later clean run.
    # A count and a percentage of one core are non-negative, so `-1` is a corrupt reading rather than a
    # comfortably passing one: it sorts under B2's zero budget and under B3's 0.1% alike, so an
    # otherwise clean set carrying one would exit zero over a value the device cannot have produced.
    # The wake-lock row is bounded only by records that measured the all-type census; see
    # `wake_lock_values` for why a partial-only count is not evidence for this budget.
    wake_locks = wake_lock_values(measured)
    verdicts.append(check_budget("B2 wake locks", max(wake_locks) if wake_locks else None, 0.0, ""))
    for label, key in (("B2 alarms", "alarms"), ("B2 jobs", "jobs")):
        values = numeric_values(measured, "resources", key, non_negative=True)
        verdicts.append(check_budget(label, max(values) if values else None, 0.0, ""))

    hidden = numeric_values(measured, "hidden", "cpu_pct", non_negative=True)
    verdicts.append(check_budget("B3 hidden CPU", max(hidden) if hidden else None, MAX_HIDDEN_CPU_PCT, "%"))

    try:
        pairs = alternation_pairs(measured)
    except device_layer.ProbeError as error:
        # Only B4 needs the pairing. The refusal is reported under B4's own label rather than a joint
        # one, because B5 is measured from the live windows themselves and is reported either way.
        verdicts.append(Verdict("B4 pairing", VerdictStatus.NOT_MEASURED, f"B4 pairing: NOT MEASURED - {error}"))
        pairs = []

    verdicts.extend(b4_baseline_verdicts(pairs, black_unavailable=black_unavailable))

    # B5 is the average visible CPU of the document's matrix, so the average is what is checked — one
    # line per outcome, so an unreadable reading and an absent window cannot both be reported as the
    # reason B5 has no verdict. The worst window is reported beside the mean as the observation it is.
    windows = live_windows(measured)
    if not windows:
        verdicts.append(
            Verdict(
                "B5 visible CPU",
                VerdictStatus.NOT_MEASURED,
                "B5 visible CPU: NOT MEASURED - no live window was recorded",
            )
        )
    else:
        try:
            samples = live_cpu_values(windows)
            mean_cpu_pct = duration_weighted_mean(samples)
        except device_layer.ProbeError as error:
            verdicts.append(
                Verdict("B5 visible CPU", VerdictStatus.NOT_MEASURED, f"B5 visible CPU: NOT MEASURED - {error}")
            )
        else:
            verdicts.append(check_budget("B5 visible CPU (mean)", mean_cpu_pct, FINAL_MAX_VISIBLE_CPU_PCT, "%"))
            verdicts.append(
                Verdict(
                    "B5 worst window",
                    VerdictStatus.INFO,
                    f"B5 worst window observed: {max(sample.cpu_pct for sample in samples):.4f}% "
                    f"over {len(samples)} windows",
                )
            )

    # B6's growth is signed on purpose — memory can shrink over a window, and a negative growth is the
    # best result the criterion can have — so this reader is the one that does not ask for the
    # non-negative refusal.
    growth_values = numeric_values(measured, "memory", "pss_growth_kb")
    verdicts.append(
        check_budget(
            "B6 PSS growth (worst run)", max(growth_values) if growth_values else None, MAX_PSS_GROWTH_KB, " kB"
        )
    )
    verdicts.append(
        Verdict(
            "B1 hidden frames",
            VerdictStatus.OUT_OF_SCOPE,
            "B1 hidden frames: NOT MEASURED HERE - the frame counter is #125 criterion 1, which "
            "records its own producer-trace evidence under docs/testing/reports/; this protocol "
            "does not own a console that could make a zero-frame claim",
        )
    )
    return verdicts


def report_exit_code(verdicts: Sequence[Verdict]) -> int:
    """
    Derive the report's exit status from its verdicts rather than from its text.

    Every verdict that is not one of `NON_GATING_STATUSES` is non-zero: a criterion that was never
    measured has not established the acceptance. The exceptions are the two statuses that name *why*
    the absence is not a failure of this run — a criterion explicitly out of scope for it (B1, which is
    #125's), and a criterion the device cannot measure at all (the black static baseline, and only
    where the run has declared the device the reason). Both are printed by the report rather than
    dropped, so the absence is recorded even though it does not gate. The rule lives on `Verdict` so
    that this function and `Verdict.is_failure` cannot disagree about it.
    """
    return 1 if any(verdict.is_failure for verdict in verdicts) else 0


def phase_report(records: Sequence[Mapping[str, object]], *, black_unavailable: str | None = None) -> int:
    """Print the aggregate report and every unrun check it can see, returning its exit status."""
    check_measurement_identity(records)
    revisions = {str(record.get("revision")) for record in records}
    api_levels = {identity_value(record, "api_level") for record in records}
    print(f"records: {len(records)}")
    print(f"source revisions: {', '.join(sorted(revisions))}")
    print(f"API levels: {', '.join(str(level) for level in sorted(api_levels, key=str))}")
    for identity_field in IDENTITY_FIELDS:
        if identity_field == "api_level":
            continue
        values = sorted(
            {identity_value(record, identity_field) for record in records if record.get(identity_field) is not None},
            key=str,
        )
        if values:
            print(f"{identity_field}: {', '.join(str(value) for value in values)}")
    print(f"committed B4 threshold: {FINAL_MAX_VISIBLE_DELTA_PCT}% relative increase")
    print(f"committed B5 threshold: {FINAL_MAX_VISIBLE_CPU_PCT}% of one core")
    floors = [record for record in records if record.get("phase") == "pilot_floor"]
    if floors:
        latest_floor = floors[-1]
        if latest_floor.get("noise_floor_rate_ua") is None:
            print("pilot floors in this state file: the discharge channel did not move, so it is unmeasured")
        else:
            print(
                f"pilot floors in this state file: discharge 2 sigma {latest_floor.get('noise_floor_rate_ua')} uA, "
                f"CPU 2 sigma {latest_floor.get('cpu_noise_floor_pct')}% of one core"
            )
    else:
        print(
            f"pilot floors committed with the thresholds: discharge 2 sigma {PILOT_DISCHARGE_NOISE_FLOOR_UA} uA, "
            f"CPU 2 sigma {PILOT_CPU_NOISE_FLOOR_PCT}% of one core ({PILOT_REPORT})"
        )
    print()
    verdicts = report_lines(records, black_unavailable=black_unavailable)
    for verdict in verdicts:
        print(verdict.line)
    return report_exit_code(verdicts)
