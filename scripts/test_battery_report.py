#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for the report's decision rules: the budgets, the statistics, and the recorded identity.

These are the checks on `scripts/host/battery/report.py`, the implementation behind the
`scripts/battery_report.py` wrapper: the half of the protocol that reads record dicts and decides.
They need no device, no window, and no phase: every input is a record as the state file would carry
it, which is what lets a criterion be tested without running one.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, NotRequired, TypedDict

import pytest
import pytest_mock

from host.battery import cli as battery_cli
from host.battery import report as battery_report
from host.battery import stats as battery_stats
from host.device import probes as device_probes


class VisibleRecord(TypedDict):
    """A visible-window record as the state file writes it."""

    phase: str
    arm: str
    discharge_rate_ua: float
    cpu_pct: float | None
    pilot: NotRequired[bool]
    baseline: NotRequired[str | None]
    duration_seconds: NotRequired[float]
    revision: NotRequired[str]
    api_level: NotRequired[int]


class HiddenRecord(TypedDict):
    """A hidden-window record as the state file writes it."""

    phase: str
    cpu_pct: float
    duration_seconds: NotRequired[float]
    restore_problems: NotRequired[list[str]]
    revision: NotRequired[str]
    api_level: NotRequired[int]


class MemoryRecord(TypedDict):
    """A memory-window record as the state file writes it."""

    phase: str
    pss_growth_kb: int
    duration_seconds: NotRequired[float]
    revision: NotRequired[str]
    api_level: NotRequired[int]


class ResourcesRecord(TypedDict):
    """A resources record carrying the all-type wake-lock coverage stamp."""

    phase: str
    wake_locks: int
    wake_lock_coverage: NotRequired[str]
    alarms: int
    jobs: int
    revision: NotRequired[str]
    api_level: NotRequired[int]


class LegacyResourcesRecord(TypedDict):
    """A resources record from before the coverage stamp existed."""

    phase: str
    wake_locks: int
    alarms: int
    jobs: int


type Record = VisibleRecord | HiddenRecord | MemoryRecord | ResourcesRecord | LegacyResourcesRecord

INTEGER_IDENTITY_FIELDS: Final = ("api_level", "clk_tck")
TEXT_IDENTITY_FIELDS: Final = tuple(
    field for field in battery_report.IDENTITY_FIELDS if field not in INTEGER_IDENTITY_FIELDS
)
WRONG_KIND_IDENTITY_CASES: Final = (
    *(
        (field, value, "not the integer this field is written as")
        for field in INTEGER_IDENTITY_FIELDS
        for value in ("100", "36", "not-an-api-level", 3.0)
    ),
    *(
        (field, value, "not the text this field is written as")
        for field in TEXT_IDENTITY_FIELDS
        for value in (7, 100, 0.5)
    ),
)


def visible_record(  # noqa: PLR0913 - the arm, its readings, and the window shape are all its inputs
    arm: str,
    rate: float,
    *,
    cpu: float | None = None,
    pilot: bool = False,
    baseline: str | None = None,
    duration: float = 900.0,
) -> VisibleRecord:
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


def alternating_records(
    live_rates: Sequence[float],
    base_rates: Sequence[float],
    *,
    cpu: float = 1.0,
) -> list[VisibleRecord]:
    """Build alternating A/B records from per-pair rates."""
    arms = zip(
        (visible_record("A", rate, cpu=cpu) for rate in live_rates),
        (visible_record("B", rate, baseline="stock") for rate in base_rates),
        strict=True,
    )
    return [record for pair in arms for record in pair]


def pairs(count: int, live_rate: float, base_rate: float, *, cpu: float = 1.0) -> list[VisibleRecord]:
    """Build `count` whole A/B pairs in the order an alternation records them."""
    return alternating_records((live_rate,) * count, (base_rate,) * count, cpu=cpu)


def record_with(base: Mapping[str, object], **fields: object) -> dict[str, object]:
    """Copy a record and overlay a field, including values the writer never produces."""
    return {**base, **fields}


def resources_record(
    *,
    wake_locks: int = 0,
    alarms: int = 0,
    jobs: int = 0,
    coverage: str | None = battery_stats.WAKE_LOCK_COVERAGE,
) -> ResourcesRecord:
    """Build one current-shape resources record, carrying the all-type wake-lock coverage stamp."""
    record: ResourcesRecord = {
        "phase": "resources",
        "wake_locks": wake_locks,
        "alarms": alarms,
        "jobs": jobs,
        "revision": "rev",
        "api_level": 36,
    }
    if coverage is not None:
        record["wake_lock_coverage"] = coverage
    return record


def legacy_resources_record(*, wake_locks: int = 0, alarms: int = 0, jobs: int = 0) -> LegacyResourcesRecord:
    """Build one resources record from before the coverage stamp existed."""
    return {"phase": "resources", "wake_locks": wake_locks, "alarms": alarms, "jobs": jobs}


def hidden_record(cpu_pct: float = 0.05, *, duration: float = 604.0) -> HiddenRecord:
    """Build one hidden-window record as the state file would carry it."""
    return {
        "phase": "hidden",
        "cpu_pct": cpu_pct,
        "duration_seconds": duration,
        "revision": "rev",
        "api_level": 36,
    }


def memory_record(pss_growth_kb: int = 992, *, duration: float = 1800.0) -> MemoryRecord:
    """Build one memory-window record as the state file would carry it."""
    return {
        "phase": "memory",
        "pss_growth_kb": pss_growth_kb,
        "duration_seconds": duration,
        "revision": "rev",
        "api_level": 36,
    }


def almost_equal(actual: float, expected: float, places: int = 7) -> bool:
    """Match unittest's assertAlmostEqual rounding at `places`, not `pytest.approx`."""
    return round(abs(actual - expected), places) == 0


def report_lines_text(records: Sequence[Mapping[str, object]]) -> list[str]:
    """Render the report's verdicts to their text lines."""
    return [verdict.line for verdict in battery_report.report_lines(records)]


@pytest.fixture
def matched_pairs() -> list[VisibleRecord]:
    """Return four A/B pairs at the same rate, the ordinary matched-arm case."""
    return pairs(4, 1000.0, 1000.0, cpu=1.0)


@pytest.fixture
def full_pass_records() -> list[Record]:
    """Return a record set in which every criterion is measured and passes."""
    return [resources_record(), hidden_record(), memory_record(), *pairs(4, 1000.0, 1000.0)]


@pytest.fixture(scope="session")
def b2_row() -> str:
    """Return the acceptance matrix's B2 row."""
    budget = Path(__file__).resolve().parent.parent / "docs" / "battery-budget.md"
    return next(line for line in budget.read_text(encoding="utf-8").splitlines() if line.startswith("| **B2:"))


@pytest.mark.parametrize(
    ("degrees_of_freedom", "expected"), [(1, 12.706), (2, 4.303), (3, 3.182), (4, 2.776), (30, 2.042)]
)
def test_table_is_indexed_by_degrees_of_freedom(degrees_of_freedom: int, expected: float) -> None:
    assert almost_equal(battery_stats.t_critical_95(degrees_of_freedom), expected)


def test_the_tail_is_expanded_rather_than_replaced_by_the_normal_value() -> None:
    """Expand the tail: at 31 degrees of freedom it is 2.0395, not the normal 1.960."""
    assert almost_equal(battery_stats.t_critical_95(31), 2.0395, places=3)
    assert almost_equal(battery_stats.t_critical_95(60), 2.0003, places=3)
    assert battery_stats.t_critical_95(31) > battery_stats.NORMAL_T_CRITICAL_95


def test_the_tail_tends_to_the_normal_value() -> None:
    assert almost_equal(battery_stats.t_critical_95(100_000), battery_stats.NORMAL_T_CRITICAL_95, places=3)
    assert battery_stats.t_critical_95(1000) > battery_stats.NORMAL_T_CRITICAL_95


@pytest.mark.parametrize("degrees_of_freedom", [1, 2])
def test_two_and_three_pairs_do_not_borrow_the_four_pair_value(degrees_of_freedom: int) -> None:
    """Refuse to borrow the four-pair value: the old lookup returned 3.182 below five."""
    assert not almost_equal(battery_stats.t_critical_95(degrees_of_freedom), 3.182)


def test_refuses_a_sample_with_no_degrees_of_freedom() -> None:
    with pytest.raises(device_probes.ProbeError, match="0 degrees of freedom"):
        battery_stats.t_critical_95(0)


def test_identical_pairs_have_no_increase() -> None:
    rates = [100.0, 102.0, 99.0, 101.0]
    statistics = battery_stats.paired_statistics(rates, rates)
    assert almost_equal(statistics.relative_delta_pct, 0.0)
    assert almost_equal(statistics.ci_95_upper_pct, 0.0)
    assert statistics.degrees_of_freedom == len(rates) - 1


def test_uniform_increase_has_a_zero_width_interval() -> None:
    statistics = battery_stats.paired_statistics([100.0] * 4, [103.0] * 4)
    assert almost_equal(statistics.relative_delta_pct, 3.0)
    assert almost_equal(statistics.ci_95_upper_pct, 3.0)


def test_the_bound_uses_the_critical_value_for_the_actual_sample_count() -> None:
    baseline = [100.0] * 5
    treatment = [100.0, 100.0, 100.0, 100.0, 120.0]
    statistics = battery_stats.paired_statistics(baseline, treatment)
    assert statistics.degrees_of_freedom == len(baseline) - 1
    assert almost_equal(statistics.t_critical, 2.776)


@pytest.mark.parametrize(
    ("baseline", "treatment", "expected_match"),
    [
        pytest.param([100.0] * 3, [101.0] * 3, "at least 4 pairs", id="fewer_than_four_pairs"),
        pytest.param([100.0] * 4, [101.0] * 5, "equal arms", id="mismatched_arms"),
        pytest.param([0.0] * 4, [1.0] * 4, "not positive", id="non_positive_baseline"),
    ],
)
def test_paired_statistics_refuses_invalid_inputs(
    baseline: list[float], treatment: list[float], expected_match: str
) -> None:
    with pytest.raises(device_probes.ProbeError, match=expected_match):
        battery_stats.paired_statistics(baseline, treatment)


def test_reports_mean_sigma_and_two_sigma() -> None:
    mean, deviation, half_width = battery_stats.two_sigma_noise_floor([100.0, 102.0, 98.0, 100.0])
    assert almost_equal(mean, 100.0)
    assert almost_equal(deviation, 1.632993161855452)
    assert almost_equal(half_width, 2 * deviation)


def test_refuses_too_few_windows() -> None:
    with pytest.raises(device_probes.ProbeError, match="at least 4 windows"):
        battery_stats.two_sigma_noise_floor([100.0, 101.0, 99.0])


BUDGET_LABELS: Final = [
    "B2 wake locks",
    "B2 alarms",
    "B2 jobs",
    "B3 hidden CPU",
    "B4 vs stock",
    "B4 vs black",
    "B5 visible CPU",
    "B6 PSS growth",
]
COMMITTED_B4_THRESHOLD_PCT: Final = 12.0123


@pytest.mark.parametrize("label", BUDGET_LABELS)
def test_every_budget_is_not_measured_without_records(label: str) -> None:
    assert any(line.startswith(label) and "NOT MEASURED" in line for line in report_lines_text([]))


def test_b1_is_declared_out_of_scope() -> None:
    verdict = next(v for v in battery_report.report_lines([]) if v.label == "B1 hidden frames")
    assert verdict.status is battery_stats.VerdictStatus.OUT_OF_SCOPE
    assert "NOT MEASURED HERE" in verdict.line


@pytest.mark.parametrize("label", ["B2 wake locks", "B2 alarms", "B2 jobs"])
def test_zero_wake_locks_alarms_and_jobs_pass(label: str) -> None:
    lines = report_lines_text([resources_record()])
    assert any(line.startswith(label) and "PASS" in line for line in lines)


@pytest.mark.parametrize(
    ("record", "expected_prefix"),
    [
        pytest.param(resources_record(wake_locks=1), "B2 wake locks: FAIL", id="held_wake_lock"),
        pytest.param(resources_record(jobs=2), "B2 jobs: FAIL", id="held_job"),
    ],
)
def test_a_held_b2_resource_fails(record: ResourcesRecord, expected_prefix: str) -> None:
    lines = report_lines_text([record])
    assert any(line.startswith(expected_prefix) for line in lines)


def test_the_b2_census_is_a_maximum_across_every_resources_record() -> None:
    """Guard the append-only maximum: a later clean rerun must not hide an earlier dirty one."""
    lines = report_lines_text([resources_record(wake_locks=3, jobs=1), resources_record()])
    assert any(line.startswith("B2 wake locks") and "FAIL" in line for line in lines)
    assert any(line.startswith("B2 jobs") and "FAIL" in line for line in lines)


def test_a_legacy_partial_only_wake_lock_count_is_not_measured() -> None:
    """Refuse the narrower pre-census count: it cannot bound the all-type budget."""
    records = [legacy_resources_record()]
    lines = report_lines_text(records)
    assert any(line.startswith("B2 wake locks: NOT MEASURED") for line in lines)
    assert any(line.startswith("B2 alarms: PASS") for line in lines)
    assert any(line.startswith("B2 jobs: PASS") for line in lines)


def test_a_legacy_partial_only_count_cannot_hide_behind_a_covered_one() -> None:
    """Bound the mixed set by the census records and ignore the legacy count."""
    records = [legacy_resources_record(wake_locks=5), resources_record()]
    lines = report_lines_text(records)
    assert any(line.startswith("B2 wake locks: PASS") for line in lines)


def test_a_coverage_stamp_that_is_not_a_string_is_refused() -> None:
    """Refuse a wrong-typed stamp as corrupt state rather than a narrower measurement."""
    records = [record_with(resources_record(), wake_lock_coverage=True)]
    with pytest.raises(device_probes.ProbeError, match="wake_lock_coverage"):
        battery_report.report_lines(records)


@pytest.mark.parametrize(("cpu_pct", "expected_status"), [(0.05, "PASS"), (5.4, "FAIL")])
def test_hidden_cpu_is_checked_against_its_budget(cpu_pct: float, expected_status: str) -> None:
    lines = report_lines_text([hidden_record(cpu_pct)])
    assert any(line.startswith("B3 hidden CPU") and expected_status in line for line in lines)


@pytest.mark.parametrize("label", ["B4 vs stock point estimate", "B4 vs stock CI upper bound", "B5 visible CPU"])
def test_matched_arms_within_the_budget_pass(label: str, matched_pairs: list[VisibleRecord]) -> None:
    lines = report_lines_text(matched_pairs)
    assert any(line.startswith(label) and "PASS" in line for line in lines)


def test_the_interval_names_the_difference_sd_that_sizes_a_future_run() -> None:
    """Name the difference sd that sizes a future run's pair count."""
    records = alternating_records((1200.0, 1400.0, 1600.0, 1800.0), (1000.0,) * 4)
    paired = battery_stats.alternation_pairs(records)
    lives = tuple(live for live, _ in paired)
    bases = tuple(base for _, base in paired)
    statistics = battery_stats.paired_statistics(
        [battery_stats.window_rate(record) for record in bases],
        [battery_stats.window_rate(record) for record in lives],
    )
    # Per-pair differences 200, 400, 600 and 800 uA: mean 500, sample sd 258.198 uA.
    assert almost_equal(statistics.standard_deviation, 258.199, places=2)
    line = next(verdict.line for verdict in battery_report.report_lines(records) if verdict.label == "B4 vs stock")
    assert "sigma_d" in line
    assert "of the baseline" in line


@pytest.mark.parametrize("label", ["B4 vs stock point estimate", "B4 vs stock CI upper bound"])
def test_a_large_increase_fails_both_b4_lines(label: str) -> None:
    lines = report_lines_text(pairs(4, 1200.0, 1000.0))
    assert any(line.startswith(label) and "FAIL" in line for line in lines)


def test_visible_cpu_over_its_budget_fails() -> None:
    lines = report_lines_text(pairs(4, 1000.0, 1000.0, cpu=6.4))
    assert any(line.startswith("B5 visible CPU") and "FAIL" in line for line in lines)


def test_pilot_windows_are_excluded_from_the_final_comparison(matched_pairs: list[VisibleRecord]) -> None:
    records = [*matched_pairs, visible_record("A", 99999.0, pilot=True)]
    lines = report_lines_text(records)
    assert any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines)


def test_a_silent_discharge_channel_is_reported_not_crashed() -> None:
    """Report a silent gauge's unmeasurable B4 without dropping the criteria that were measured."""
    lines = report_lines_text(pairs(4, 0.0, 0.0))
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)
    assert any(line.startswith("B5 visible CPU") for line in lines)
    assert not any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines)


def test_too_few_pairs_leaves_b4_unmeasured() -> None:
    lines = report_lines_text(pairs(3, 1000.0, 1000.0))
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)


@pytest.mark.parametrize(
    "pss_growth_kb", [pytest.param(992, id="positive_under_budget"), pytest.param(-512, id="negative_shrinkage")]
)
def test_pss_growth_within_budget_passes(pss_growth_kb: int) -> None:
    """Admit signed growth: a memory record whose PSS fell still meets B6."""
    lines = report_lines_text([memory_record(pss_growth_kb)])
    assert any(line.startswith("B6 PSS growth") and "PASS" in line for line in lines)


def test_a_six_second_window_fails_and_does_not_qualify() -> None:
    """Drop a six-second window; B4 is defined over fifteen minutes."""
    records = [
        visible_record("A", 1000.0, cpu=1.0, duration=6.0),
        visible_record("B", 1000.0, baseline="stock", duration=6.0),
    ] * 4
    lines = report_lines_text(records)
    assert any(line.startswith("WINDOW DURATIONS: FAIL") for line in lines)
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)
    assert not any(line.startswith("B4 vs stock point estimate") for line in lines)


def test_a_record_without_a_recorded_duration_is_not_measurable_either() -> None:
    record = hidden_record()
    del record["duration_seconds"]
    lines = report_lines_text([record])
    assert any(line.startswith("WINDOW DURATIONS: FAIL") for line in lines)
    assert any(line.startswith("B3 hidden CPU") and "NOT MEASURED" in line for line in lines)


def test_a_window_inside_the_length_tolerance_still_qualifies() -> None:
    """Accept jitter around 900s; refusing it would fail every real run."""
    records = [
        visible_record("A", 1000.0, cpu=1.0, duration=902.0),
        visible_record("B", 1000.0, baseline="stock", duration=898.0),
    ] * 4
    lines = report_lines_text(records)
    assert not any(line.startswith("WINDOW DURATIONS") for line in lines)
    assert any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines)


def test_an_increase_between_the_provisional_and_committed_threshold_passes() -> None:
    """Discriminate a regression to the provisional threshold: +8% must pass."""
    assert battery_stats.FINAL_MAX_VISIBLE_DELTA_PCT == COMMITTED_B4_THRESHOLD_PCT
    statuses = {(verdict.label, verdict.status) for verdict in battery_report.report_lines(pairs(4, 1080.0, 1000.0))}
    assert ("B4 vs stock point estimate", battery_stats.VerdictStatus.PASS) in statuses
    assert ("B4 vs stock CI upper bound", battery_stats.VerdictStatus.PASS) in statuses


def test_a_negative_point_estimate_and_interval_still_pass() -> None:
    """Keep a negative verdict apart from the refused negative rate: a decrease passes."""
    statuses = {(verdict.label, verdict.status) for verdict in battery_report.report_lines(pairs(4, 900.0, 1000.0))}
    assert ("B4 vs stock point estimate", battery_stats.VerdictStatus.PASS) in statuses
    assert ("B4 vs stock CI upper bound", battery_stats.VerdictStatus.PASS) in statuses


def test_a_sequence_that_is_not_an_ab_alternation_is_refused() -> None:
    """Refuse a sequence that is not an A/B alternation; file order has no comparison."""
    records = [
        visible_record("A", 1000.0, cpu=1.0),
        visible_record("B", 1000.0, baseline="stock"),
        visible_record("A", 1000.0, cpu=1.0),
        visible_record("A", 1000.0, cpu=1.0),
        visible_record("B", 1000.0, baseline="stock"),
        visible_record("B", 1000.0, baseline="stock"),
    ]
    with pytest.raises(device_probes.ProbeError, match="not an A/B alternation"):
        battery_stats.alternation_pairs(records)
    lines = report_lines_text(records)
    assert any(line.startswith("B4 pairing") and "NOT MEASURED" in line for line in lines)


def test_a_live_window_with_no_cpu_reading_leaves_b5_unmeasured(matched_pairs: list[VisibleRecord]) -> None:
    matched_pairs[0]["cpu_pct"] = None
    lines = report_lines_text(matched_pairs)
    assert any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines)


@pytest.mark.parametrize("cpu", [float("-inf"), True, -1])
def test_an_unreadable_visible_cpu_reading_leaves_b5_unmeasured(cpu: float, matched_pairs: list[VisibleRecord]) -> None:
    """Leave B5 unmeasured for values that are not readings: -inf, a boolean, or a negative."""
    matched_pairs[0]["cpu_pct"] = cpu
    lines = report_lines_text(matched_pairs)
    assert any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines)
    assert not any(line.startswith("B5 visible CPU (mean)") for line in lines)


def test_a_window_with_no_discharge_rate_leaves_b4_unmeasured(matched_pairs: list[VisibleRecord]) -> None:
    """Delete the rate field to model the partial shape the typed builder never writes."""
    records: list[dict[str, object]] = [dict(record) for record in matched_pairs]
    del records[1]["discharge_rate_ua"]
    lines = report_lines_text(records)
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)


def test_an_unmatched_live_window_does_not_discard_b5() -> None:
    """Keep B5 on the live arm's own channel while B4's alternation refusal stands."""
    records = [*pairs(4, 1000.0, 1000.0, cpu=6.4), visible_record("A", 1000.0, cpu=6.4)]
    lines = report_lines_text(records)
    assert any(line.startswith("B4 pairing") and "NOT MEASURED" in line for line in lines)
    assert not any(line.startswith("B4 vs stock point estimate") for line in lines)
    assert any(line.startswith("B5 visible CPU (mean)") and "FAIL" in line for line in lines)


def test_a_live_window_without_a_baseline_still_measures_b5() -> None:
    """Measure B5 from live windows even when the file holds no comparison."""
    records = [visible_record("A", 1000.0, cpu=6.4) for _ in range(2)]
    lines = report_lines_text(records)
    assert not any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines)
    assert any(line.startswith("B5 visible CPU (mean)") and "FAIL" in line for line in lines)


def test_b5_weights_each_window_by_its_length() -> None:
    """Weight each window by its length: the same windows average 5% over time, not 2.75%."""
    records = [
        *[visible_record("A", 1000.0, cpu=1.0, duration=900.0) for _ in range(3)],
        visible_record("A", 1000.0, cpu=8.0, duration=3600.0),
    ]
    mean = next(line for line in report_lines_text(records) if line.startswith("B5 visible CPU (mean)"))
    assert "FAIL" in mean
    assert "5.0000%" in mean


def test_b5_is_unchanged_when_every_window_is_the_same_length() -> None:
    """Keep the equal-length result that the committed threshold was derived from."""
    records = [visible_record("A", 1000.0, cpu=1.0, duration=900.0) for _ in range(4)]
    lines = report_lines_text(records)
    assert any(line.startswith("B5 visible CPU (mean)") and "PASS — 1.0000%" in line for line in lines)


def test_an_unreadable_cpu_reading_is_not_reported_as_no_live_window(matched_pairs: list[VisibleRecord]) -> None:
    """Report the non-finite reading without a second "no live window" verdict."""
    matched_pairs[0]["cpu_pct"] = float("-inf")
    lines = [line for line in report_lines_text(matched_pairs) if line.startswith("B5 visible CPU")]
    assert len(lines) == 1
    assert "not finite" in lines[0]
    assert "no live window was recorded" not in lines[0]


@pytest.mark.parametrize("rate", [True, float("inf"), float("nan")])
def test_an_unreadable_discharge_rate_leaves_b4_unmeasured(rate: float, matched_pairs: list[VisibleRecord]) -> None:
    """Leave B4 unmeasured for inf, nan, or a boolean, none of which is a rate."""
    for record in matched_pairs:
        record["discharge_rate_ua"] = rate
    lines = report_lines_text(matched_pairs)
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)
    assert not any(line.startswith("B4 vs stock point estimate") for line in lines)


@pytest.mark.parametrize(
    ("duration", "expected_reason"),
    [
        (float("inf"), "not a finite length of time"),
        (float("nan"), "not a finite length of time"),
        (float("-inf"), "not a finite length of time"),
        (True, "not a length of time"),
    ],
)
def test_an_invalid_window_length_is_not_a_long_window(duration: float, expected_reason: str) -> None:
    """Refuse infinite, nan, and boolean durations that would pass as long windows."""
    record = visible_record("A", 1000.0, cpu=1.0, duration=duration)
    reason = battery_report.short_window_reason(record)
    assert reason is not None
    assert expected_reason in reason
    assert any(line.startswith("WINDOW DURATIONS: FAIL") for line in report_lines_text([record]))


def test_a_stock_only_run_reports_the_black_arm_as_unmeasured(full_pass_records: list[Record]) -> None:
    """Report the absent black arm as unmeasured; only a declaration exempts it."""
    lines = report_lines_text(full_pass_records)
    assert any(line.startswith("B4 vs black") and "NOT MEASURED" in line for line in lines)
    assert not any("DEVICE-LIMITED" in line for line in lines)
    assert any(line.startswith("B4 vs stock CI upper bound") and "PASS" in line for line in lines)


def test_a_declared_device_limit_exempts_the_black_arm_and_quotes_the_reason(
    full_pass_records: list[Record],
) -> None:
    verdicts = battery_report.report_lines(full_pass_records, black_unavailable="the build has no root")
    line = next(verdict.line for verdict in verdicts if verdict.label == "B4 vs black")
    assert "DEVICE-LIMITED" in line
    assert "the build has no root" in line
    assert battery_report.report_exit_code(verdicts) == 0


def test_an_undeclared_black_baseline_gates_the_report(full_pass_records: list[Record]) -> None:
    verdicts = battery_report.report_lines(full_pass_records)
    assert any(
        verdict.label == "B4 vs black" and verdict.status is battery_stats.VerdictStatus.NOT_MEASURED
        for verdict in verdicts
    )
    assert battery_report.report_exit_code(verdicts) == 1


def test_a_black_only_run_leaves_the_gated_comparison_unmeasured() -> None:
    records = [visible_record("A", 1000.0, cpu=1.0), visible_record("B", 1000.0, baseline="black")] * 4
    lines = report_lines_text(records)
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)
    assert any(line.startswith("B4 vs black CI upper bound") for line in lines)


def test_a_fully_measured_passing_report_exits_zero(full_pass_records: list[Record]) -> None:
    verdicts = battery_report.report_lines(full_pass_records, black_unavailable="the build has no root")
    assert battery_report.report_exit_code(verdicts) == 0


@pytest.mark.parametrize("status", list(battery_stats.VerdictStatus))
def test_is_failure_agrees_with_the_exit_rule_on_every_status(status: battery_stats.VerdictStatus) -> None:
    """Agree with the type's own failure rule so the exit status matches the printed verdict."""
    verdict = battery_stats.Verdict("a criterion", status, f"a criterion: {status.value}")
    expected_exit_code = 1 if verdict.is_failure else 0
    assert battery_report.report_exit_code([verdict]) == expected_exit_code
    unmeasured = battery_stats.Verdict("c", battery_stats.VerdictStatus.NOT_MEASURED, "c")
    assert battery_report.report_exit_code([unmeasured]) == 1


def test_a_fail_exits_non_zero() -> None:
    verdicts = battery_report.report_lines(pairs(4, 1200.0, 1000.0))
    assert battery_report.report_exit_code(verdicts) == 1


def test_an_unmeasured_criterion_exits_non_zero() -> None:
    assert battery_report.report_exit_code(battery_report.report_lines([])) == 1


def test_a_lone_out_of_scope_criterion_is_not_a_failure() -> None:
    verdicts = [
        battery_stats.Verdict("B1 hidden frames", battery_stats.VerdictStatus.OUT_OF_SCOPE, "B1: not measured here")
    ]
    assert battery_report.report_exit_code(verdicts) == 0


def test_a_device_limited_criterion_is_recorded_but_does_not_gate() -> None:
    """Record a device-limited criterion without gating, and never as a pass."""
    verdicts = [
        battery_stats.Verdict("B4 vs black", battery_stats.VerdictStatus.DEVICE_LIMITED, "B4 vs black: NOT MEASURED"),
        battery_stats.Verdict("B4 vs stock", battery_stats.VerdictStatus.PASS, "B4 vs stock: PASS"),
    ]
    assert battery_report.report_exit_code(verdicts) == 0
    assert not verdicts[0].is_failure
    assert verdicts[0].status is not battery_stats.VerdictStatus.PASS
    assert verdicts[0].status is not battery_stats.VerdictStatus.NOT_MEASURED


def test_a_stock_only_full_pass_exits_zero_once_the_black_arm_is_declared(full_pass_records: list[Record]) -> None:
    verdicts = battery_report.report_lines(full_pass_records, black_unavailable="the build has no root")
    assert battery_report.report_exit_code(verdicts) == 0
    assert any(
        verdict.label == "B4 vs black" and verdict.status is battery_stats.VerdictStatus.DEVICE_LIMITED
        for verdict in verdicts
    )


def test_phase_report_returns_the_derived_status(
    capsys: pytest.CaptureFixture[str], full_pass_records: list[Record]
) -> None:
    assert battery_report.phase_report(full_pass_records, black_unavailable="no root") == 0
    assert battery_report.phase_report(pairs(4, 1200.0, 1000.0)) == 1
    assert "B4 vs stock" in capsys.readouterr().out


def test_a_state_file_carrying_a_negative_rate_is_not_a_comparison() -> None:
    records = [visible_record("A", -1000.0, cpu=1.0), visible_record("B", 1000.0, baseline="stock")] * 4
    lines = report_lines_text(records)
    assert any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines)
    assert not any(line.startswith("B4 vs stock point estimate") for line in lines)


@pytest.mark.parametrize(
    ("baseline_label", "expected_match"),
    [pytest.param("unreadable", "unreadable", id="unknown_label"), pytest.param(None, "baseline", id="absent_label")],
)
def test_an_undocumented_or_absent_baseline_label_is_refused(
    baseline_label: str | None, expected_match: str, matched_pairs: list[VisibleRecord]
) -> None:
    """Refuse an unlisted baseline label and refuse an absent one rather than guess."""
    failing = pairs(1, 9000.0, 1000.0, cpu=1.0)
    if baseline_label is None:
        del failing[1]["baseline"]
    else:
        failing[1]["baseline"] = baseline_label
    with pytest.raises(device_probes.ProbeError, match=expected_match):
        battery_report.report_lines(matched_pairs + failing)


@pytest.mark.parametrize("value", ["false", 0, None])
def test_a_non_boolean_pilot_flag_is_refused(value: object) -> None:
    """Refuse a non-boolean pilot flag; "false" would filter as truthy."""
    records = [visible_record("A", 1000.0, cpu=1.0) for _ in range(4)]
    hot = [record_with(visible_record("A", 1000.0, cpu=10.0), pilot=value) for _ in range(4)]
    with pytest.raises(device_probes.ProbeError, match="pilot"):
        battery_report.report_lines([*records, *hot])


def test_a_non_boolean_pilot_flag_hides_no_b4_pair(matched_pairs: list[VisibleRecord]) -> None:
    failing = [record_with(record, pilot="false") for record in pairs(1, 9000.0, 1000.0, cpu=1.0)]
    with pytest.raises(device_probes.ProbeError, match="pilot"):
        battery_report.report_lines([*matched_pairs, *failing])


def test_an_absent_pilot_flag_is_not_a_pilot(matched_pairs: list[VisibleRecord]) -> None:
    """Treat an absent pilot flag as an ordinary measurement; only the pilot phase writes it."""
    for record in matched_pairs:
        del record["pilot"]
    lines = report_lines_text(matched_pairs)
    assert any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines)
    assert any(line.startswith("B5 visible CPU (mean)") and "PASS" in line for line in lines)


def test_the_matrix_budget_matches_what_the_probe_counts(b2_row: str) -> None:
    """Match the probe's count of every pending alarm, not a narrower budget."""
    assert "0 pending alarms of any kind" in b2_row
    assert "repeating alarms" not in b2_row


def test_the_row_names_every_resource_the_report_judges(b2_row: str) -> None:
    """Name both the B2 jobs verdict and the `dumpsys jobscheduler` probe."""
    assert "0 JobScheduler jobs" in b2_row
    assert "dumpsys jobscheduler" in b2_row


@pytest.mark.parametrize("wake_locks", [pytest.param("corrupt", id="non_numeric"), pytest.param(True, id="boolean")])
def test_a_non_numeric_or_boolean_resource_field_is_refused(wake_locks: object) -> None:
    """Refuse an unreadable count instead of passing B2 on the records that parsed."""
    records = [legacy_resources_record(), record_with(legacy_resources_record(), wake_locks=wake_locks)]
    with pytest.raises(device_probes.ProbeError, match="wake_locks"):
        battery_report.report_lines(records)


@pytest.mark.parametrize("key", ["wake_locks", "alarms", "jobs"])
def test_a_negative_resource_count_is_refused(key: str) -> None:
    """Refuse a negative count; it would sort under the zero budget and exit zero."""
    records = [record_with(resources_record(), **{key: -1})]
    with pytest.raises(device_probes.ProbeError, match=key):
        battery_report.report_lines(records)


@pytest.mark.parametrize("cpu_pct", [pytest.param(float("-inf"), id="non_finite"), pytest.param(-1, id="negative")])
def test_an_invalid_hidden_cpu_is_refused(cpu_pct: float) -> None:
    """Refuse an unreadable hidden CPU; keep a valid duration so it reaches the reader."""
    records = [record_with(hidden_record(cpu_pct, duration=600.0), apk_sha256="a")]
    with pytest.raises(device_probes.ProbeError, match="cpu_pct"):
        battery_report.report_lines(records)


def test_a_field_no_record_carries_is_still_not_bounded() -> None:
    """Leave an absent field unbounded; the wake-lock row is the one this record measures."""
    records = [record_with({"phase": "resources"}, wake_locks=0, wake_lock_coverage=battery_stats.WAKE_LOCK_COVERAGE)]
    lines = report_lines_text(records)
    assert any(line.startswith("B2 wake locks: PASS") for line in lines)
    assert any(line.startswith("B2 alarms: NOT MEASURED") for line in lines)
    assert any(line.startswith("B2 jobs: NOT MEASURED") for line in lines)


def test_a_wholly_unknown_identity_is_refused() -> None:
    """Refuse an unknown build; aggregating it would name no subject."""
    records = [record_with({"phase": "resources"}, apk_sha256=None, api_level=36) for _ in range(2)]
    with pytest.raises(device_probes.ProbeError, match="apk_sha256"):
        battery_report.check_measurement_identity(records)


def test_an_identity_field_no_record_carries_is_still_not_bounded() -> None:
    """Leave an absent identity field unbounded or the resources census stops being reportable."""
    records = [record_with({"phase": "resources"}, api_level=36) for _ in range(2)]
    battery_report.check_measurement_identity(records)


@pytest.mark.parametrize("identity_field", battery_report.IDENTITY_FIELDS)
@pytest.mark.parametrize("value", [[], {}, set(), True])
def test_a_non_scalar_identity_value_is_refused(identity_field: str, value: object) -> None:
    """Refuse a non-scalar identity value; a list raises and a boolean reads as an int."""
    records = [record_with({"phase": "resources"}, **{identity_field: value})]
    with pytest.raises(device_probes.ProbeError, match=identity_field):
        battery_report.check_measurement_identity(records)


@pytest.mark.parametrize("identity_field", battery_report.IDENTITY_FIELDS)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_identity_value_is_refused(identity_field: str, value: float) -> None:
    """Refuse a non-finite identity; json.loads admits NaN and Infinity from a corrupt file."""
    records = [record_with({"phase": "resources"}, **{identity_field: value})]
    with pytest.raises(device_probes.ProbeError, match=identity_field):
        battery_report.check_measurement_identity(records)
    battery_report.check_measurement_identity([record_with({"phase": "resources"}, clk_tck=100, api_level=36)])


@pytest.mark.parametrize(("identity_field", "value", "expected_message"), WRONG_KIND_IDENTITY_CASES)
def test_an_identity_value_of_the_wrong_kind_is_refused(
    identity_field: str, value: object, expected_message: str
) -> None:
    """Refuse the other scalar kind; the phase writers never produce it as an identity."""
    assert set(battery_report.IDENTITY_FIELDS) == set(INTEGER_IDENTITY_FIELDS) | set(TEXT_IDENTITY_FIELDS)
    assert set(INTEGER_IDENTITY_FIELDS) == set(battery_report.INTEGER_IDENTITY_FIELDS)
    records = [record_with({"phase": "resources"}, **{identity_field: value})]
    with pytest.raises(device_probes.ProbeError, match=expected_message):
        battery_report.check_measurement_identity(records)


def test_the_kinds_the_phase_writers_use_are_still_accepted() -> None:
    """Accept the identity shapes the phases write, taken from the 2026-10-16 device pass."""
    record = record_with(
        {"phase": "resources"},
        device_id="2ec5a940ef5f",
        apk_sha256="a" * 64,
        api_level=36,
        power_source="simulated",
        clk_tck=100,
    )
    battery_report.check_measurement_identity([record, dict(record)])
    assert battery_report.identity_value(record_with({"phase": "resources"}, apk_sha256=None), "apk_sha256") is None


@pytest.mark.parametrize(
    "restore_problems",
    [pytest.param("airplane mode was not restored", id="string"), pytest.param(None, id="explicit_null")],
)
def test_a_non_list_restore_problems_is_refused(restore_problems: object) -> None:
    """Refuse a present non-list restore_problems value; only absence means no problems."""
    records = [record_with({"phase": "hidden"}, restore_problems=restore_problems)]
    with pytest.raises(device_probes.ProbeError, match="not a list"):
        battery_report.collect_restore_problems(records)


def test_an_absent_restore_problems_is_no_problem() -> None:
    assert battery_report.collect_restore_problems([record_with({"phase": "hidden"})]) == []
    assert battery_report.collect_restore_problems([record_with({"phase": "hidden"}, restore_problems=["x"])]) == ["x"]


def test_dash_prefixed_values_survive_forwarding(mocker: pytest_mock.MockerFixture) -> None:
    """Forward dash-prefixed values as `--option=value` tokens."""
    main = mocker.patch.object(battery_cli, "main", return_value=0)
    assert battery_cli.main_report(["--state=-reports", "--black-unavailable=--reason"]) == 0
    main.assert_called_once_with(["--state=-reports", "report", "--black-unavailable=--reason"])
