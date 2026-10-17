#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for the report's decision rules: the budgets, the statistics, and the recorded identity.

These are the checks on `scripts/battery_report.py`, the half of the protocol that reads record dicts
and decides. They need no device, no window, and no phase: every input is a record as the state file
would carry it, which is what lets a criterion be tested without running one.
"""

import contextlib
import io
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

import battery_report as report
import device_layer


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


class TCriticalTest(unittest.TestCase):
    """Checks the degrees-of-freedom-indexed t table and its tail."""

    def test_table_is_indexed_by_degrees_of_freedom(self) -> None:
        self.assertAlmostEqual(12.706, report.t_critical_95(1))
        self.assertAlmostEqual(4.303, report.t_critical_95(2))
        self.assertAlmostEqual(3.182, report.t_critical_95(3))
        self.assertAlmostEqual(2.776, report.t_critical_95(4))

    def test_table_reaches_thirty_degrees_of_freedom(self) -> None:
        self.assertAlmostEqual(2.042, report.t_critical_95(30))

    def test_the_tail_is_expanded_rather_than_replaced_by_the_normal_value(self) -> None:
        # The two-sided 95% t at 31 degrees of freedom is 2.0395; returning the normal 1.960 there
        # would understate the interval by about 4%.
        self.assertAlmostEqual(2.0395, report.t_critical_95(31), places=3)
        self.assertAlmostEqual(2.0003, report.t_critical_95(60), places=3)
        self.assertGreater(report.t_critical_95(31), report.NORMAL_T_CRITICAL_95)

    def test_the_tail_tends_to_the_normal_value(self) -> None:
        self.assertAlmostEqual(report.NORMAL_T_CRITICAL_95, report.t_critical_95(100_000), places=3)
        self.assertGreater(report.t_critical_95(1000), report.NORMAL_T_CRITICAL_95)

    def test_two_and_three_pairs_do_not_borrow_the_four_pair_value(self) -> None:
        # The old two-branch lookup returned 3.182 for every n below 5, which understates the
        # interval for two or three pairs.
        self.assertNotAlmostEqual(3.182, report.t_critical_95(2))
        self.assertNotAlmostEqual(3.182, report.t_critical_95(1))

    def test_refuses_a_sample_with_no_degrees_of_freedom(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.t_critical_95(0)


class PairedStatisticsTest(unittest.TestCase):
    """Checks the paired relative increase and its 95% interval."""

    def test_identical_pairs_have_no_increase(self) -> None:
        rates = [100.0, 102.0, 99.0, 101.0]
        statistics = report.paired_statistics(rates, rates)
        self.assertAlmostEqual(0.0, statistics.relative_delta_pct)
        self.assertAlmostEqual(0.0, statistics.ci_95_upper_pct)
        self.assertEqual(3, statistics.degrees_of_freedom)

    def test_uniform_increase_has_a_zero_width_interval(self) -> None:
        statistics = report.paired_statistics([100.0] * 4, [103.0] * 4)
        self.assertAlmostEqual(3.0, statistics.relative_delta_pct)
        self.assertAlmostEqual(3.0, statistics.ci_95_upper_pct)

    def test_the_bound_uses_the_critical_value_for_the_actual_sample_count(self) -> None:
        baseline = [100.0] * 5
        treatment = [100.0, 100.0, 100.0, 100.0, 120.0]
        statistics = report.paired_statistics(baseline, treatment)
        self.assertEqual(4, statistics.degrees_of_freedom)
        self.assertAlmostEqual(2.776, statistics.t_critical)

    def test_refuses_fewer_than_four_pairs(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.paired_statistics([100.0] * 3, [101.0] * 3)

    def test_refuses_mismatched_arms(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.paired_statistics([100.0] * 4, [101.0] * 5)

    def test_refuses_a_non_positive_baseline(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.paired_statistics([0.0] * 4, [1.0] * 4)


class NoiseFloorTest(unittest.TestCase):
    """Checks the pilot's mean, sigma, and two-sigma half-width."""

    def test_reports_mean_sigma_and_two_sigma(self) -> None:
        mean, deviation, half_width = report.two_sigma_noise_floor([100.0, 102.0, 98.0, 100.0])
        self.assertAlmostEqual(100.0, mean)
        self.assertAlmostEqual(1.632993161855452, deviation)
        self.assertAlmostEqual(2 * deviation, half_width)

    def test_refuses_too_few_windows(self) -> None:
        with self.assertRaises(device_layer.ProbeError):
            report.two_sigma_noise_floor([100.0, 101.0, 99.0])


def resources_record(**overrides: object) -> dict[str, object]:
    """Build one current-shape resources record, carrying the all-type wake-lock coverage stamp."""
    record: dict[str, object] = {
        "phase": "resources",
        "wake_locks": 0,
        "wake_lock_coverage": report.WAKE_LOCK_COVERAGE,
        "alarms": 0,
        "jobs": 0,
        "revision": "rev",
        "api_level": 36,
    }
    record.update(overrides)
    return record


def full_pass_records() -> list[dict[str, object]]:
    """Build a record set in which every criterion is measured and passes."""
    records: list[dict[str, object]] = [
        resources_record(),
        {"phase": "hidden", "cpu_pct": 0.05, "duration_seconds": 604.0, "revision": "rev", "api_level": 36},
        {"phase": "memory", "pss_growth_kb": 992, "duration_seconds": 1800.0, "revision": "rev", "api_level": 36},
    ]
    records.extend(pairs(4, 1000.0, 1000.0))
    return records


class ReportTest(unittest.TestCase):
    """Checks the aggregate pass/fail lines against the committed thresholds."""

    def lines(self, records: Sequence[Mapping[str, object]]) -> list[str]:
        """Render the report's verdicts to their text lines."""
        return [verdict.line for verdict in report.report_lines(records)]

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
        verdicts = report.report_lines([])
        self.assertTrue(
            any(
                verdict.label == "B1 hidden frames"
                and verdict.status is report.VerdictStatus.OUT_OF_SCOPE
                and "NOT MEASURED HERE" in verdict.line
                for verdict in verdicts
            )
        )

    def test_zero_wake_locks_alarms_and_jobs_pass(self) -> None:
        records = [resources_record()]
        lines = self.lines(records)
        for label in ("B2 wake locks", "B2 alarms", "B2 jobs"):
            self.assertTrue(any(line.startswith(label) and "PASS" in line for line in lines))

    def test_a_held_wake_lock_fails(self) -> None:
        records = [resources_record(wake_locks=1)]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks") and "FAIL" in line for line in lines))

    def test_a_held_job_fails(self) -> None:
        records = [resources_record(jobs=2)]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 jobs") and "FAIL" in line for line in lines))

    def test_the_b2_census_is_a_maximum_across_every_resources_record(self) -> None:
        # The state file is append-only, so a later clean rerun must not hide an earlier dirty one.
        records = [resources_record(wake_locks=3, jobs=1), resources_record()]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks") and "FAIL" in line for line in lines))
        self.assertTrue(any(line.startswith("B2 jobs") and "FAIL" in line for line in lines))

    def test_a_legacy_partial_only_wake_lock_count_is_not_measured(self) -> None:
        # `wake_locks` meant `PARTIAL_WAKE_LOCK` alone before the probe widened to the census. The
        # count is a real measurement of the narrower criterion, so the report does not refuse it —
        # but it cannot bound a zero-wake-locks-of-any-type budget in either direction, and passing
        # it would report a budget nothing measured.
        records = [{"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 0, "revision": "rev", "api_level": 36}]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks: NOT MEASURED") for line in lines))
        self.assertTrue(any(line.startswith("B2 alarms: PASS") for line in lines))
        self.assertTrue(any(line.startswith("B2 jobs: PASS") for line in lines))

    def test_a_legacy_partial_only_count_cannot_hide_behind_a_covered_one(self) -> None:
        # A mixed set is bounded by the records that measured the all-type census. The legacy count
        # is ignored rather than treated as a violation: it is not this budget's measurement, and
        # the covered record is the evidence the row rests on.
        records = [
            {"phase": "resources", "wake_locks": 5, "alarms": 0, "jobs": 0, "revision": "rev", "api_level": 36},
            resources_record(),
        ]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks: PASS") for line in lines))

    def test_a_covered_wake_lock_count_still_fails(self) -> None:
        records = [resources_record(wake_locks=1)]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B2 wake locks: FAIL") for line in lines))

    def test_a_coverage_stamp_that_is_not_a_string_is_refused(self) -> None:
        # The stamp is a classifier the report compares against one value, and a value of the wrong
        # type is corrupt state rather than a narrower measurement: reading it as "not measured"
        # would silently drop the record behind a readable one.
        records = [{"phase": "resources", "wake_locks": 0, "wake_lock_coverage": True, "alarms": 0, "jobs": 0}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.report_lines(records)
        self.assertIn("wake_lock_coverage", str(raised.exception))

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
        paired = report.alternation_pairs(records)
        statistics = report.paired_statistics(
            [report.window_rate(base) for _, base in paired],
            [report.window_rate(live) for live, _ in paired],
        )
        # Per-pair differences 200, 400, 600 and 800 uA: mean 500, sample sd 258.198 uA.
        self.assertAlmostEqual(258.199, statistics.standard_deviation, places=2)
        line = next(verdict.line for verdict in report.report_lines(records) if verdict.label == "B4 vs stock")
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

    def test_a_negative_pss_growth_still_passes(self) -> None:
        # Memory can shrink over a window, so B6's growth is signed, and the non-negative refusal the
        # other numeric readers now take must not reach it: a memory record whose PSS fell says the
        # criterion is met, not that the file is corrupt. This passes before and after the refusal,
        # which is the point of it — it is the guard against the refusal overshooting.
        records = [
            {
                "phase": "memory",
                "pss_growth_kb": -512,
                "duration_seconds": 1800.0,
                "revision": "rev",
                "api_level": 36,
            }
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
        self.assertEqual(12.0123, report.FINAL_MAX_VISIBLE_DELTA_PCT)
        statuses = {(verdict.label, verdict.status) for verdict in report.report_lines(pairs(4, 1080.0, 1000.0))}
        self.assertIn(("B4 vs stock point estimate", report.VerdictStatus.PASS), statuses)
        self.assertIn(("B4 vs stock CI upper bound", report.VerdictStatus.PASS), statuses)

    def test_a_negative_point_estimate_and_interval_still_pass(self) -> None:
        # B4's verdict is a relative *increase*, and a decrease is the best result the criterion can
        # have: the recorded evidence in docs/battery-budget.md carries a -30.775% point estimate with
        # a -11.615% upper bound against the 12.0123% threshold. The rate reader's refusal of a
        # negative *rate* is a different statement from a refusal of a negative *verdict*, and this is
        # the guard that keeps the two apart: live 900 uA against a stock 1000 uA is a -10% point
        # estimate and a -10% interval upper bound, and both must pass. It passes before and after the
        # refusals, which is the point of it.
        statuses = {(verdict.label, verdict.status) for verdict in report.report_lines(pairs(4, 900.0, 1000.0))}
        self.assertIn(("B4 vs stock point estimate", report.VerdictStatus.PASS), statuses)
        self.assertIn(("B4 vs stock CI upper bound", report.VerdictStatus.PASS), statuses)

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
            report.alternation_pairs(records)
        self.assertIn("not an A/B alternation", str(raised.exception))
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 pairing") and "NOT MEASURED" in line for line in lines))

    def test_a_live_window_with_no_cpu_reading_leaves_b5_unmeasured(self) -> None:
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        records[0]["cpu_pct"] = None
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines))

    def test_an_unreadable_visible_cpu_reading_leaves_b5_unmeasured(self) -> None:
        # `-inf` is the case the finding names: it satisfies the `isinstance` the reader checked and is
        # below every budget, so three real readings beside one `-inf` averaged to "PASS — -inf%". A
        # boolean reads as 1.0% and a negative percentage as a value below the budget, so all three
        # used to leave B5 reported as measured. None of them is a reading, so B5 has no input.
        for cpu in (float("-inf"), True, -1):
            with self.subTest(cpu=cpu):
                records = pairs(4, 1000.0, 1000.0, cpu=1.0)
                records[0]["cpu_pct"] = cpu
                lines = self.lines(records)
                self.assertTrue(any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines))
                self.assertFalse(any(line.startswith("B5 visible CPU (mean)") for line in lines))

    def test_a_window_with_no_discharge_rate_leaves_b4_unmeasured(self) -> None:
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        del records[1]["discharge_rate_ua"]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))

    def test_an_unmatched_live_window_does_not_discard_b5(self) -> None:
        # B5 is the live arm's own channel — the document's B5 row names the process ticks during a
        # visible window and no baseline — so it is read from the live windows and not from the A/B
        # pairs. An unmatched final live window makes the sequence a non-alternation, and B5 used to
        # lose every reading on the file with it: four pairs at 6.4% failed B5, and one more completed
        # live window replaced that failure with "no live window was recorded". B4's refusal stands.
        records = [*pairs(4, 1000.0, 1000.0, cpu=6.4), visible_record("A", 1000.0, cpu=6.4)]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 pairing") and "NOT MEASURED" in line for line in lines))
        self.assertFalse(any(line.startswith("B4 vs stock point estimate") for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU (mean)") and "FAIL" in line for line in lines))

    def test_a_live_window_without_a_baseline_still_measures_b5(self) -> None:
        # The other end of the same rule: a run that recorded live windows and no baseline has taken
        # B5 measurements, so B5 is measured from them even though the file holds no comparison.
        records = [visible_record("A", 1000.0, cpu=6.4) for _ in range(2)]
        lines = self.lines(records)
        self.assertFalse(any(line.startswith("B5 visible CPU") and "NOT MEASURED" in line for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU (mean)") and "FAIL" in line for line in lines))

    def test_b5_weights_each_window_by_its_length(self) -> None:
        # B5 is a rate — CPU at 1 Hz over at least 15 minutes — so the aggregate has to estimate one.
        # Three 15-minute windows at 1% of one core beside one 60-minute window at 8% is 2.75% counted
        # per window, which passed the 3% budget, and 5% over the time the readings came from, which is
        # what the device was doing across the run.
        records = [visible_record("A", 1000.0, cpu=1.0, duration=900.0) for _ in range(3)]
        records.append(visible_record("A", 1000.0, cpu=8.0, duration=3600.0))
        mean = next(line for line in self.lines(records) if line.startswith("B5 visible CPU (mean)"))
        self.assertIn("FAIL", mean)
        self.assertIn("5.0000%", mean)

    def test_b5_is_unchanged_when_every_window_is_the_same_length(self) -> None:
        # The weighting exists for the windows whose lengths differ, not to move the ordinary case: a
        # run whose windows all ran their documented length must report exactly what it reported
        # before, since that equal-length shape is the one the committed threshold was derived from.
        records = [visible_record("A", 1000.0, cpu=1.0, duration=900.0) for _ in range(4)]
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B5 visible CPU (mean)") and "PASS — 1.0000%" in line for line in lines))

    def test_an_unreadable_cpu_reading_is_not_reported_as_no_live_window(self) -> None:
        # Two verdicts for one criterion used to be emitted: the refusal naming the reading, and then
        # the absence message, which says no live window was recorded over a file that recorded one.
        # The second contradicts the input, and it is the line a reader would take as the reason.
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        records[0]["cpu_pct"] = float("-inf")
        lines = [line for line in self.lines(records) if line.startswith("B5 visible CPU")]
        self.assertEqual(1, len(lines))
        self.assertIn("not finite", lines[0])
        self.assertNotIn("no live window was recorded", lines[0])

    def test_a_boolean_discharge_rate_leaves_b4_unmeasured(self) -> None:
        # `True` is an `int`, so it passed the reader's `isinstance` and, being neither negative nor
        # non-finite to a comparison, produced a numeric comparison out of `true` in both arms: four
        # pairs of `discharge_rate_ua: true` reported passing B4 point-estimate and interval lines.
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        for record in records:
            record["discharge_rate_ua"] = True
        lines = self.lines(records)
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
        self.assertFalse(any(line.startswith("B4 vs stock point estimate") for line in lines))

    def test_a_non_finite_discharge_rate_leaves_b4_unmeasured(self) -> None:
        # Neither `inf` nor `nan` is a discharge measurement, so neither can be compared, and neither
        # is below a threshold, so both reached the arithmetic: an infinite rate made the baseline mean
        # infinite and every delta `nan`, and B4 reported `FAIL — nan%` on both lines. That is a failed
        # comparison over rates no window recorded, rather than the unmeasurable one this is, so the
        # verdict was wrong in the other direction from the boolean's.
        for rate in (float("inf"), float("nan")):
            with self.subTest(rate=rate):
                records = pairs(4, 1000.0, 1000.0, cpu=1.0)
                for record in records:
                    record["discharge_rate_ua"] = rate
                lines = self.lines(records)
                self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
                self.assertFalse(any(line.startswith("B4 vs stock point estimate") for line in lines))

    def test_a_non_finite_window_length_is_not_a_long_window(self) -> None:
        # `inf` and `nan` satisfy the `isinstance` the report checks and neither is below the minimum,
        # so a hand-edited or corrupt record used to be averaged in as a window of the documented
        # length. The CLI refuses both by name; the report is fed a file it did not write, so it does
        # too.
        for duration in (float("inf"), float("nan"), float("-inf")):
            with self.subTest(duration=duration):
                record = visible_record("A", 1000.0, cpu=1.0, duration=duration)
                self.assertIsNotNone(report.short_window_reason(record))
                lines = self.lines([record])
                self.assertTrue(any(line.startswith("WINDOW DURATIONS: FAIL") for line in lines))

    def test_a_boolean_window_length_is_not_a_long_window(self) -> None:
        # `true` is an `int` in Python, so the threshold comparison read it as a 1.0-second window and
        # reported the record as a window that ran one second — a length the record never recorded.
        # It is refused as a value that is not a length of time instead, the same way `finite_number`
        # refuses it for the readings.
        record = visible_record("A", 1000.0, cpu=1.0, duration=True)
        reason = report.short_window_reason(record)
        self.assertIsNotNone(reason)
        self.assertIn("not a length of time", str(reason))
        self.assertTrue(any(line.startswith("WINDOW DURATIONS: FAIL") for line in self.lines([record])))

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
        verdicts = report.report_lines(records, black_unavailable="the build has no root")
        line = next(verdict.line for verdict in verdicts if verdict.label == "B4 vs black")
        self.assertIn("DEVICE-LIMITED", line)
        self.assertIn("the build has no root", line)
        self.assertEqual(0, report.report_exit_code(verdicts))

    def test_an_undeclared_black_baseline_gates_the_report(self) -> None:
        verdicts = report.report_lines(full_pass_records())
        self.assertTrue(
            any(
                verdict.label == "B4 vs black" and verdict.status is report.VerdictStatus.NOT_MEASURED
                for verdict in verdicts
            )
        )
        self.assertEqual(1, report.report_exit_code(verdicts))

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
        verdicts = report.report_lines(full_pass_records(), black_unavailable="the build has no root")
        self.assertEqual(0, report.report_exit_code(verdicts))

    def test_is_failure_agrees_with_the_exit_rule_on_every_status(self) -> None:
        # `is_failure` is the type's own answer to "should the report fail", and the exit code is built
        # from it: a status the two disagreed about would be a printed verdict whose text and exit
        # status parted company.
        for status in report.VerdictStatus:
            verdict = report.Verdict("a criterion", status, f"a criterion: {status.value}")
            self.assertEqual(
                report.report_exit_code([verdict]),
                1 if verdict.is_failure else 0,
                msg=status,
            )
        self.assertEqual(1, report.report_exit_code([report.Verdict("c", report.VerdictStatus.NOT_MEASURED, "c")]))

    def test_a_fail_exits_non_zero(self) -> None:
        verdicts = report.report_lines(pairs(4, 1200.0, 1000.0))
        self.assertEqual(1, report.report_exit_code(verdicts))

    def test_an_unmeasured_criterion_exits_non_zero(self) -> None:
        self.assertEqual(1, report.report_exit_code(report.report_lines([])))

    def test_a_lone_out_of_scope_criterion_is_not_a_failure(self) -> None:
        verdicts = [report.Verdict("B1 hidden frames", report.VerdictStatus.OUT_OF_SCOPE, "B1: not measured here")]
        self.assertEqual(0, report.report_exit_code(verdicts))

    def test_a_device_limited_criterion_is_recorded_but_does_not_gate(self) -> None:
        # The black baseline is required only where the device permits setting one, so its absence is
        # printed and carried by the verdict list without turning every run on this device into a
        # failure. It is emphatically not reported as a pass, which is what a silent omission did.
        verdicts = [
            report.Verdict("B4 vs black", report.VerdictStatus.DEVICE_LIMITED, "B4 vs black: NOT MEASURED"),
            report.Verdict("B4 vs stock", report.VerdictStatus.PASS, "B4 vs stock: PASS"),
        ]
        self.assertEqual(0, report.report_exit_code(verdicts))
        self.assertFalse(verdicts[0].is_failure)
        self.assertIsNot(report.VerdictStatus.PASS, verdicts[0].status)
        self.assertIsNot(report.VerdictStatus.NOT_MEASURED, verdicts[0].status)

    def test_a_stock_only_full_pass_exits_zero_once_the_black_arm_is_declared(self) -> None:
        verdicts = report.report_lines(full_pass_records(), black_unavailable="the build has no root")
        self.assertEqual(0, report.report_exit_code(verdicts))
        self.assertTrue(
            any(
                verdict.label == "B4 vs black" and verdict.status is report.VerdictStatus.DEVICE_LIMITED
                for verdict in verdicts
            )
        )

    def test_phase_report_returns_the_derived_status(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, report.phase_report(full_pass_records(), black_unavailable="no root"))
            self.assertEqual(1, report.phase_report(pairs(4, 1200.0, 1000.0)))


class NegativeDischargeRecordTest(unittest.TestCase):
    """
    Finding 1: a state file carrying a negative rate is not a discharge comparison.

    The measuring half's own refusal — a `Window` whose pack gained charge — is
    `WindowDischargeRateTest` in `test_battery_protocol.py`.
    """

    def test_a_state_file_carrying_a_negative_rate_is_not_a_comparison(self) -> None:
        records = [visible_record("A", -1000.0, cpu=1.0), visible_record("B", 1000.0, baseline="stock")] * 4
        lines = [verdict.line for verdict in report.report_lines(records)]
        self.assertTrue(any(line.startswith("B4 vs stock") and "NOT MEASURED" in line for line in lines))
        self.assertFalse(any(line.startswith("B4 vs stock point estimate") for line in lines))


class BaselineLabelTest(unittest.TestCase):
    """
    Finding: a pair whose arm B window names a baseline the document does not is dropped, not judged.

    The report prints one block per *documented* baseline, so a label neither block names is selected
    by neither, and the pair — with whatever it measured — is absent from every line. Four clean stock
    pairs plus one pair labeled `unreadable` whose live rate is eight times its baseline reported B4 as
    PASS throughout and exited 0. It is also the one field here whose *absence* is refused: an arm B
    window's baseline is what places the pair in a comparison at all, and reading a missing label as
    the primary baseline would be guessing what the file meant.
    """

    def test_an_unknown_baseline_label_is_refused(self) -> None:
        failing = pairs(1, 9000.0, 1000.0, cpu=1.0)
        failing[1]["baseline"] = "unreadable"
        records = pairs(4, 1000.0, 1000.0, cpu=1.0) + failing
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.report_lines(records)
        self.assertIn("unreadable", str(raised.exception))

    def test_an_absent_baseline_label_is_refused(self) -> None:
        failing = pairs(1, 9000.0, 1000.0, cpu=1.0)
        del failing[1]["baseline"]
        records = pairs(4, 1000.0, 1000.0, cpu=1.0) + failing
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.report_lines(records)
        self.assertIn("baseline", str(raised.exception))


class PilotFlagTest(unittest.TestCase):
    """
    Finding: a `pilot` flag that is not a boolean is truthy, so it files the record as calibration.

    Both selectors filter on truthiness, and `"false"` is a truthy string in Python. Four live windows
    at 1% of one core beside four at 10% reported B5 PASS at 1% when the hot four carried one, and the
    same flag on a failing pair removed it from the alternation: with a complete record set and the
    black baseline declared device-limited, the report exited 0 over a live arm recording 9000 uA
    against a 1000 uA baseline.
    """

    def test_a_non_boolean_pilot_flag_is_refused(self) -> None:
        # `0` and `null` are not booleans either. The flag is written as a boolean by the phase that
        # writes it at all, so a value that is not one is a corrupt record rather than a third way of
        # saying "not a pilot".
        for value in ("false", 0, None):
            with self.subTest(pilot=value):
                records = [visible_record("A", 1000.0, cpu=1.0) for _ in range(4)]
                hot = [visible_record("A", 1000.0, cpu=10.0) for _ in range(4)]
                for record in hot:
                    record["pilot"] = value
                with self.assertRaises(device_layer.ProbeError) as raised:
                    report.report_lines(records + hot)
                self.assertIn("pilot", str(raised.exception))

    def test_a_non_boolean_pilot_flag_hides_no_b4_pair(self) -> None:
        failing = pairs(1, 9000.0, 1000.0, cpu=1.0)
        for record in failing:
            record["pilot"] = "false"
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.report_lines(pairs(4, 1000.0, 1000.0, cpu=1.0) + failing)
        self.assertIn("pilot", str(raised.exception))

    def test_an_absent_pilot_flag_is_not_a_pilot(self) -> None:
        # The visible phase does not write the flag at all — only the pilot phase does — so a record
        # that does not carry it is an ordinary measurement, and the refusal must not reach it.
        records = pairs(4, 1000.0, 1000.0, cpu=1.0)
        for record in records:
            del record["pilot"]
        lines = [verdict.line for verdict in report.report_lines(records)]
        self.assertTrue(any(line.startswith("B4 vs stock point estimate") and "PASS" in line for line in lines))
        self.assertTrue(any(line.startswith("B5 visible CPU (mean)") and "PASS" in line for line in lines))


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


class CorruptRecordTest(unittest.TestCase):
    """
    A state file the report did not write is refused, not read around.

    Every one of these is a field that is *present* and unreadable. The distinction matters: a field
    a record does not carry at all says an older revision had nothing to record, and the report must
    keep working on those. A field that is present and wrong is a corrupt or hand-edited file, and
    skipping it hides the record behind the ones that parsed.
    """

    def test_a_non_numeric_resource_field_is_refused(self) -> None:
        # One valid zero and one unreadable value would otherwise report B2 as PASS, judged on the
        # records that happened to parse.
        records = [
            {"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 0},
            {"phase": "resources", "wake_locks": "corrupt", "alarms": 0, "jobs": 0},
        ]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.report_lines(records)
        self.assertIn("wake_locks", str(raised.exception))

    def test_a_boolean_is_not_a_count(self) -> None:
        # `True` is an `int` in Python, so an unchecked reader takes `"wake_locks": true` for one
        # lock — or, worse here, for a value that sorts under the zero budget as `1` does not.
        records = [{"phase": "resources", "wake_locks": True, "alarms": 0, "jobs": 0}]
        with self.assertRaises(device_layer.ProbeError):
            report.report_lines(records)

    def test_a_negative_resource_count_is_refused(self) -> None:
        # A count of wake locks, alarms, or jobs cannot be negative, and one that is sorts under the
        # zero budget rather than failing it: an otherwise clean census carrying `-1` exited zero over
        # a value the device cannot have produced.
        for key in ("wake_locks", "alarms", "jobs"):
            with self.subTest(key=key):
                records = [{"phase": "resources", "wake_locks": 0, "alarms": 0, "jobs": 0}]
                records[0][key] = -1
                with self.assertRaises(device_layer.ProbeError) as raised:
                    report.report_lines(records)
                self.assertIn(key, str(raised.exception))

    def test_a_non_finite_value_is_refused(self) -> None:
        # `-inf` sorts below every budget, so it would pass a maximum-is-zero check it never measured.
        records = [
            {
                "phase": "hidden",
                "cpu_pct": float("-inf"),
                "duration_seconds": 600.0,
                "apk_sha256": "a",
                "api_level": 36,
            }
        ]
        with self.assertRaises(device_layer.ProbeError):
            report.report_lines(records)

    def test_a_negative_hidden_cpu_is_refused(self) -> None:
        # The same case as the resource count, on B3: a percentage of one core cannot be negative, and
        # one that is sits below the 0.1% budget instead of failing it. The record needs a valid
        # `duration_seconds` to reach the reader at all, because a record dropped as short never
        # bounds its budget.
        records = [{"phase": "hidden", "cpu_pct": -1, "duration_seconds": 600.0, "apk_sha256": "a", "api_level": 36}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.report_lines(records)
        self.assertIn("cpu_pct", str(raised.exception))

    def test_a_field_no_record_carries_is_still_not_bounded(self) -> None:
        # The refusal above must not extend to an absent field: a revision that never recorded one
        # has nothing to say about it, and a report over its records has to keep working. The
        # wake-lock row is the one this record measured, so it is the one that passes.
        records = [{"phase": "resources", "wake_locks": 0, "wake_lock_coverage": "all-types"}]
        lines = [verdict.line for verdict in report.report_lines(records)]
        self.assertTrue(any(line.startswith("B2 wake locks: PASS") for line in lines))
        self.assertTrue(any(line.startswith("B2 alarms: NOT MEASURED") for line in lines))
        self.assertTrue(any(line.startswith("B2 jobs: NOT MEASURED") for line in lines))

    def test_a_wholly_unknown_identity_is_refused(self) -> None:
        # A set whose every record says "unknown build" is homogeneous and still cannot name what it
        # measured, so aggregating it produces a verdict whose subject is unnamed. The rule reads
        # each identity field, so one field is enough to refuse the set.
        records = [{"apk_sha256": None, "api_level": 36}, {"apk_sha256": None, "api_level": 36}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.check_measurement_identity(records)
        self.assertIn("apk_sha256", str(raised.exception))

    def test_an_identity_field_no_record_carries_is_still_not_bounded(self) -> None:
        # The refusal above must not extend to an absent field, or the `resources` census — which
        # carries neither `power_source` nor `clk_tck` — would stop being reportable.
        report.check_measurement_identity([{"api_level": 36}, {"api_level": 36}])

    def test_a_non_scalar_identity_value_is_refused(self) -> None:
        # The identity check is a set per field, so the set is the comparison: a value it cannot hold
        # cannot be shown to agree with any other, and one it can hold but no record writes — a
        # boolean, which is an `int` in Python — would be compared as a build or a tick rate. A list
        # made the set construction raise `TypeError: unhashable type` out of the caller as a
        # traceback rather than the named refusal every other corrupt-record reader gives. Every
        # identity field is covered, not only the one the defect was reported against, because they
        # are all read by the same rule.
        for identity_field in report.IDENTITY_FIELDS:
            for value in ([], {}, set(), True):
                with self.subTest(identity_field=identity_field, value=value):
                    records = [{"phase": "resources", identity_field: value}]
                    with self.assertRaises(device_layer.ProbeError) as raised:
                        report.check_measurement_identity(records)
                    self.assertIn(identity_field, str(raised.exception))

    def test_a_non_finite_identity_value_is_refused(self) -> None:
        # The state file is JSON Lines read with `json.loads`, which accepts `NaN` and `Infinity` by
        # default, so a corrupt file really does reach here. Both are floats, and no identity field is
        # written as one, so they are refused by the kind check — see below for the rule they fall
        # under. Left at the wrong kind, `inf` would compare equal to itself and a set of them would
        # be read as agreement, with `inf` printed as the identity the run was measured under.
        for identity_field in report.IDENTITY_FIELDS:
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(identity_field=identity_field, value=value):
                    records = [{"phase": "resources", identity_field: value}]
                    with self.assertRaises(device_layer.ProbeError) as raised:
                        report.check_measurement_identity(records)
                    self.assertIn(identity_field, str(raised.exception))
        # The refusal must not reach a finite number, which is what every state file carries.
        report.check_measurement_identity([{"phase": "resources", "clk_tck": 100, "api_level": 36}])

    def test_an_identity_value_of_the_wrong_kind_is_refused(self) -> None:
        # Each identity field is written as one kind of thing, and the report is fed a state file it
        # did not write. A scalar of the other kind is a value no phase produces: read as an identity,
        # it passes the gate and the report prints it as the identity the run was measured under —
        # `API levels: not-an-api-level` — which is the complaint the check already makes about a set
        # whose every value is `null`. The kinds are the writers' own, not the report's invention.
        integer_fields = ("api_level", "clk_tck")
        text_fields = tuple(field for field in report.IDENTITY_FIELDS if field not in integer_fields)
        self.assertEqual(set(report.IDENTITY_FIELDS), set(integer_fields) | set(text_fields))
        for identity_field in integer_fields:
            for value in ("100", "36", "not-an-api-level", 3.0):
                with self.subTest(identity_field=identity_field, value=value):
                    records = [{"phase": "resources", identity_field: value}]
                    with self.assertRaises(device_layer.ProbeError) as raised:
                        report.check_measurement_identity(records)
                    self.assertIn("not the integer this field is written as", str(raised.exception))
        for identity_field in text_fields:
            for value in (7, 100, 0.5):
                with self.subTest(identity_field=identity_field, value=value):
                    records = [{"phase": "resources", identity_field: value}]
                    with self.assertRaises(device_layer.ProbeError) as raised:
                        report.check_measurement_identity(records)
                    self.assertIn("not the text this field is written as", str(raised.exception))
        # And the table the rule reads is the writers' own, so it cannot drift away from them.
        self.assertEqual(set(integer_fields), set(report.INTEGER_IDENTITY_FIELDS))

    def test_the_kinds_the_phase_writers_use_are_still_accepted(self) -> None:
        # The rule has to admit what the phases actually write, or the report stops reading its own
        # state files: the two integers and the three strings of the record below, which is a record
        # from the 2026-10-16 device pass. The `null` a pre-build record carries is admitted by this
        # reader as well, and refused by the identity check above it when it is the only value the
        # set holds — which is that check's decision, not this reader's.
        record = {
            "phase": "resources",
            "device_id": "2ec5a940ef5f",
            "apk_sha256": "a" * 64,
            "api_level": 36,
            "power_source": "simulated",
            "clk_tck": 100,
        }
        report.check_measurement_identity([record, dict(record)])
        self.assertIsNone(report.identity_value({"phase": "resources", "apk_sha256": None}, "apk_sha256"))

    def test_a_non_list_restore_problems_is_refused(self) -> None:
        # A failed restore is the one thing on a record that says the device was left changed, and
        # reading a string where the phases write a list as "no problems" would let the report exit
        # zero over an explicitly recorded failure.
        records = [{"phase": "hidden", "restore_problems": "airplane mode was not restored"}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.collect_restore_problems(records)
        self.assertIn("not a list", str(raised.exception))

    def test_an_explicit_null_restore_problems_is_refused(self) -> None:
        # `dict.get` answers `None` both for a record that never carried the key and for one that
        # carries it with a null, so the absence check was reading an explicit null as "this phase
        # collected no problems" and skipping it — the one reading of the field that lets a report
        # exit zero over restoration data it could not read. Absence is a membership test; a present
        # value has to be a list.
        records = [{"phase": "hidden", "restore_problems": None}]
        with self.assertRaises(device_layer.ProbeError) as raised:
            report.collect_restore_problems(records)
        self.assertIn("not a list", str(raised.exception))

    def test_an_absent_restore_problems_is_no_problem(self) -> None:
        self.assertEqual([], report.collect_restore_problems([{"phase": "hidden"}]))
        self.assertEqual(["x"], report.collect_restore_problems([{"phase": "hidden", "restore_problems": ["x"]}]))
