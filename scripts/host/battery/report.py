# SPDX-License-Identifier: Apache-2.0
"""
Judgement half of the battery protocol: record dicts in, verdicts and an exit status out.

Nothing here touches a device; `host.battery.cli` imports this module, and this module imports only
the standard library, `host.battery.stats`, and `host.device.probes`.
"""

import math
from collections.abc import Mapping, Sequence
from typing import Final

from host.battery import stats
from host.device import probes

# A set that mixes these is not comparable. The threshold and noise floor are per device, so the
# device is part of the identity. `revision` is deliberately excluded: a documentation-only revision
# does not change how a window was measured.
IDENTITY_FIELDS: Final = ("device_id", "apk_sha256", "api_level", "power_source", "clk_tck")

# The fields written as integers; the other three are text. A scalar of the wrong kind is a value no
# phase produces.
INTEGER_IDENTITY_FIELDS: Final = ("api_level", "clk_tck")


def identity_value(record: Mapping[str, object], identity_field: str) -> object:
    """
    Return one record's value for an identity field, refusing a value that is not a scalar.

    The callers compare through a set, so an unhashable value is a traceback rather than the named
    refusal, and a bool is an `int` (`true` would read as one tick). Each field is written as one
    kind — integer or text — so the other kind is refused too. A null is admitted: it is the
    legitimate "unknown build", and refusing it is the identity check's decision.
    """
    value = record.get(identity_field)
    if isinstance(value, bool) or (value is not None and not isinstance(value, str | int | float)):
        detail = (
            f"a {record.get('phase')!r} record's {identity_field} is {value!r}, which is not a scalar, "
            "so it cannot be read as a measurement identity"
        )
        raise probes.ProbeError(detail)
    expected = int if identity_field in INTEGER_IDENTITY_FIELDS else str
    if value is not None and not isinstance(value, expected):
        written_as = "integer" if expected is int else "text"
        detail = (
            f"a {record.get('phase')!r} record's {identity_field} is {value!r}, which is not the "
            f"{written_as} this field is written as, so it cannot be read as a measurement identity"
        )
        raise probes.ProbeError(detail)
    return value


def check_measurement_identity(records: Sequence[Mapping[str, object]]) -> None:
    """
    Refuse a record set that mixes builds, API levels, power modes, or tick rates.

    Disagreeing records are not comparable, so the report names the distinct values instead of
    producing one verdict from two builds. An absent field does not bound the identity; a present null
    is a value and must not aggregate with a named one. Non-scalars are refused by `identity_value`.
    """
    for identity_field in IDENTITY_FIELDS:
        values = {identity_value(record, identity_field) for record in records if identity_field in record}
        if not values:
            continue
        if values == {None}:
            # A homogeneous "unknown" set still cannot say which build it measured.
            detail = (
                f"every record that carries {identity_field} records no value for it, so the set cannot "
                "say which measurement identity it was taken under; re-run the phases against one build "
                "and configuration"
            )
            raise probes.ProbeError(detail)
        if len(values) > 1:
            named = ", ".join("unknown" if value is None else str(value) for value in sorted(values, key=str))
            detail = (
                f"the record set mixes {identity_field} values ({named}), so it combines measurements "
                "from different measurement identities; re-run the phases against one build and configuration"
            )
            raise probes.ProbeError(detail)


def check_budget(label: str, value: float | None, budget: float, unit: str) -> stats.Verdict:
    """Build one pass/fail verdict, refusing to pass a budget that was never measured."""
    if value is None:
        return stats.Verdict(
            label,
            stats.VerdictStatus.NOT_MEASURED,
            f"{label}: NOT MEASURED — no value was recorded, so the budget is neither passed nor failed",
        )
    status = stats.VerdictStatus.PASS if value <= budget else stats.VerdictStatus.FAIL
    return stats.Verdict(label, status, f"{label}: {status.value} — {value:.4f}{unit} against {budget}{unit}")


def collect_restore_problems(records: Sequence[Mapping[str, object]]) -> list[str]:
    """
    Collect every restore problem the records carry, so a failed restore is never silent.

    Absence is a membership test, not `get(...) is None`: a present null must not read as "no
    problems". A present non-list is refused, since a string would otherwise hide an explicitly
    recorded failure.
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
            raise probes.ProbeError(detail)
        problems.extend(str(problem) for problem in found)
    return problems


def short_window_reason(record: Mapping[str, object]) -> str | None:
    """
    Return why a record's window is not the length its criterion documents, or None when it is.

    The state file may predate the CLI's minimum check, so a record that cannot be shown to have run
    for its documented length is named and excluded rather than averaged in.
    """
    phase = str(record.get("phase"))
    minimum = stats.MINIMUM_SECONDS_BY_PHASE.get(phase)
    if minimum is None:
        return None
    duration = record.get("duration_seconds")
    threshold = minimum * stats.MINIMUM_DURATION_TOLERANCE
    # Not routed through `finite_number`: this refusal is a returned reason, and a negative duration
    # is named by the threshold branch. `true` is an `int` and would read as a 1.0-second window.
    if isinstance(duration, bool) or not isinstance(duration, int | float):
        return (
            f"a {phase} record carries {duration!r} for `duration_seconds`, which is not a length of "
            f"time, so its window cannot be shown to be the documented {minimum / 60.0:.0f} minutes"
        )
    # `nan` is not below anything, so a bare comparison would admit it as a documented-length window.
    if not math.isfinite(duration):
        return (
            f"a {phase} record's window ran {duration!r}s, which is not a finite length of time, so it "
            f"cannot be shown to be the documented {minimum / 60.0:.0f} minutes"
        )
    if duration < threshold:
        return (
            f"a {phase} record's window ran {duration:.1f}s, below the documented "
            f"{minimum / 60.0:.0f} minutes ({threshold:.0f}s at the {stats.MINIMUM_DURATION_TOLERANCE:.0%} tolerance)"
        )
    return None


def black_baseline_verdict(declared_reason: str | None) -> stats.Verdict:
    """
    Build B4's secondary-baseline verdict from the run's declaration, or from its absence.

    An absent window cannot distinguish "the device cannot set one" from "the run skipped it", so the
    exemption is the explicit `--black-unavailable <reason>` declaration, quoted into the output. A
    run without one carries the criterion as unmeasured.
    """
    if declared_reason:
        return stats.Verdict(
            "B4 vs black",
            stats.VerdictStatus.DEVICE_LIMITED,
            "B4 vs black: NOT MEASURED (DEVICE-LIMITED) \u2014 the run declared that this device cannot "
            f"set a static wallpaper: {declared_reason}. That baseline is required only where the device "
            "permits setting one, so the dark-image comparison is left uncalibrated rather than assumed "
            "equal to stock",
        )
    return stats.Verdict(
        "B4 vs black",
        stats.VerdictStatus.NOT_MEASURED,
        "B4 vs black: NOT MEASURED - no black baseline windows were recorded and the run did not "
        "declare the device unable to set one, so the document's secondary baseline is unmeasured "
        "rather than exempt; pass --black-unavailable <reason> to record that the device is the reason",
    )


def b4_baseline_verdicts(
    pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    *,
    black_unavailable: str | None = None,
) -> list[stats.Verdict]:
    """
    Build B4's verdicts, one block per documented baseline rather than one per observed baseline.

    Otherwise a stock-only file would carry a complete-looking verdict while the secondary comparison
    silently vanished. A label the document does not name is refused: no block would select its pair,
    so its measurements would be missing from the whole report.
    """
    verdicts: list[stats.Verdict] = []
    baseline_labels = (stats.Baseline.STOCK.value, stats.Baseline.BLACK.value)
    for _, base in pairs:
        label = base.get("baseline")
        if label not in baseline_labels:
            detail = (
                f"a visible arm B window carries baseline={label!r}, which is not one of the document's "
                f"baselines ({' and '.join(baseline_labels)}), so the pair it belongs to stands outside "
                "every comparison this report prints"
            )
            raise probes.ProbeError(detail)
    for baseline in baseline_labels:
        selected = [(live, base) for live, base in pairs if base.get("baseline") == baseline]
        if not selected:
            if baseline == stats.Baseline.BLACK.value:
                verdicts.append(black_baseline_verdict(black_unavailable))
            else:
                verdicts.append(
                    stats.Verdict(
                        f"B4 vs {baseline}",
                        stats.VerdictStatus.NOT_MEASURED,
                        f"B4 vs {baseline}: NOT MEASURED - no {baseline} baseline windows were "
                        "recorded, so the comparison the criterion is gated on was not measured",
                    )
                )
            continue
        try:
            statistics = stats.paired_statistics(
                [stats.window_rate(base) for _, base in selected], [stats.window_rate(live) for live, _ in selected]
            )
        except probes.ProbeError as error:
            verdicts.append(
                stats.Verdict(
                    f"B4 vs {baseline}",
                    stats.VerdictStatus.NOT_MEASURED,
                    f"B4 vs {baseline}: NOT MEASURED - {error} ({len(selected)} pairs recorded)",
                )
            )
            continue
        verdicts.append(
            stats.Verdict(
                f"B4 vs {baseline}",
                stats.VerdictStatus.INFO,
                f"B4 vs {baseline} relative increase: {statistics.relative_delta_pct:.3f}% "
                f"(95% CI upper {statistics.ci_95_upper_pct:.3f}%, {statistics.sample_count} pairs, "
                f"df {statistics.degrees_of_freedom}, t {statistics.t_critical}, "
                f"sigma_d {statistics.standard_deviation:.0f} uA = "
                f"{statistics.standard_deviation / statistics.mean_baseline * 100:.3f}% of the baseline)",
            )
        )
        verdicts.extend(
            check_budget(f"B4 vs {baseline} {label}", value, stats.FINAL_MAX_VISIBLE_DELTA_PCT, "%")
            for label, value in (
                ("point estimate", statistics.relative_delta_pct),
                ("CI upper bound", statistics.ci_95_upper_pct),
            )
        )
    return verdicts


def report_lines(
    records: Sequence[Mapping[str, object]], *, black_unavailable: str | None = None
) -> list[stats.Verdict]:
    """Build the pass/fail verdicts against the committed thresholds."""
    verdicts: list[stats.Verdict] = []
    problems = collect_restore_problems(records)
    if problems:
        verdicts.append(
            stats.Verdict(
                "device restore",
                stats.VerdictStatus.FAIL,
                f"DEVICE RESTORE: FAIL - {len(problems)} problem(s) were recorded",
            )
        )
        verdicts.extend(stats.Verdict("device restore detail", stats.VerdictStatus.INFO, f"  - {p}") for p in problems)

    # A short window is not the measurement its criterion is defined on: fail and drop it first.
    reasons = [(record, short_window_reason(record)) for record in records]
    measured: list[Mapping[str, object]] = [record for record, reason in reasons if reason is None]
    short: list[str] = [str(reason) for _, reason in reasons if reason is not None]
    if short:
        verdicts.append(
            stats.Verdict(
                "window durations",
                stats.VerdictStatus.FAIL,
                f"WINDOW DURATIONS: FAIL - {len(short)} record(s) below their documented minimum",
            )
        )
        verdicts.extend(
            stats.Verdict("window duration detail", stats.VerdictStatus.INFO, f"  - {problem}") for problem in short
        )

    # B2 is the maximum over every resources record, not the last: the file is append-only, so a rerun
    # must not hide an earlier failure. Counts are non-negative, so `-1` must be refused rather than
    # passing under the zero budget. Wake locks are bounded only by all-types records.
    wake_locks = stats.wake_lock_values(measured)
    verdicts.append(check_budget("B2 wake locks", max(wake_locks) if wake_locks else None, 0.0, ""))
    budget_values = {
        label: stats.numeric_values(measured, "resources", key, non_negative=True)
        for label, key in (("B2 alarms", "alarms"), ("B2 jobs", "jobs"))
    }
    verdicts.extend(
        check_budget(label, max(values) if values else None, 0.0, "") for label, values in budget_values.items()
    )

    hidden = stats.numeric_values(measured, "hidden", "cpu_pct", non_negative=True)
    verdicts.append(check_budget("B3 hidden CPU", max(hidden) if hidden else None, stats.MAX_HIDDEN_CPU_PCT, "%"))

    try:
        pairs = stats.alternation_pairs(measured)
    except probes.ProbeError as error:
        # Only B4 needs the pairing; B5 is measured from live windows and reported either way.
        verdicts.append(
            stats.Verdict("B4 pairing", stats.VerdictStatus.NOT_MEASURED, f"B4 pairing: NOT MEASURED - {error}")
        )
        pairs = []

    verdicts.extend(b4_baseline_verdicts(pairs, black_unavailable=black_unavailable))

    # B5 checks the duration-weighted mean; the worst window is an observation beside it. One line
    # per outcome keeps an unreadable reading distinct from an absent window.
    windows = stats.live_windows(measured)
    if not windows:
        verdicts.append(
            stats.Verdict(
                "B5 visible CPU",
                stats.VerdictStatus.NOT_MEASURED,
                "B5 visible CPU: NOT MEASURED - no live window was recorded",
            )
        )
    else:
        try:
            samples = stats.live_cpu_values(windows)
            mean_cpu_pct = stats.duration_weighted_mean(samples)
        except probes.ProbeError as error:
            verdicts.append(
                stats.Verdict(
                    "B5 visible CPU", stats.VerdictStatus.NOT_MEASURED, f"B5 visible CPU: NOT MEASURED - {error}"
                )
            )
        else:
            verdicts.append(check_budget("B5 visible CPU (mean)", mean_cpu_pct, stats.FINAL_MAX_VISIBLE_CPU_PCT, "%"))
            verdicts.append(
                stats.Verdict(
                    "B5 worst window",
                    stats.VerdictStatus.INFO,
                    f"B5 worst window observed: {max(sample.cpu_pct for sample in samples):.4f}% "
                    f"over {len(samples)} windows",
                )
            )

    # B6's growth is signed: memory can shrink, and negative growth is the best result.
    growth_values = stats.numeric_values(measured, "memory", "pss_growth_kb")
    verdicts.append(
        check_budget(
            "B6 PSS growth (worst run)", max(growth_values) if growth_values else None, stats.MAX_PSS_GROWTH_KB, " kB"
        )
    )
    verdicts.append(
        stats.Verdict(
            "B1 hidden frames",
            stats.VerdictStatus.OUT_OF_SCOPE,
            "B1 hidden frames: NOT MEASURED HERE - the frame counter is #125 criterion 1, which "
            "records its own producer-trace evidence under docs/testing/reports/; this protocol "
            "does not own a console that could make a zero-frame claim",
        )
    )
    return verdicts


def report_exit_code(verdicts: Sequence[stats.Verdict]) -> int:
    """
    Derive the report's exit status from its verdicts rather than from its text.

    Only `NON_GATING_STATUSES` let a run exit zero: unmeasured criteria gate. Out-of-scope (B1) and
    device-limited (declared black baseline) absences are still printed. The rule lives on `Verdict`
    so it cannot disagree with `is_failure`.
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
    print(f"committed B4 threshold: {stats.FINAL_MAX_VISIBLE_DELTA_PCT}% relative increase")
    print(f"committed B5 threshold: {stats.FINAL_MAX_VISIBLE_CPU_PCT}% of one core")
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
            f"pilot floors committed with the thresholds: discharge 2 sigma {stats.PILOT_DISCHARGE_NOISE_FLOOR_UA} uA, "
            f"CPU 2 sigma {stats.PILOT_CPU_NOISE_FLOOR_PCT}% of one core ({stats.PILOT_REPORT})"
        )
    print()
    verdicts = report_lines(records, black_unavailable=black_unavailable)
    for verdict in verdicts:
        print(verdict.line)
    return report_exit_code(verdicts)
