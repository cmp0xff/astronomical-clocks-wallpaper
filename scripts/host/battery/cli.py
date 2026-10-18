# SPDX-License-Identifier: Apache-2.0
"""
Battery and resource acceptance protocol: one bounded phase per subcommand.

Each phase appends one JSON record to a state directory and prints its own evidence. No phase performs
the wallpaper swap it measures; the arm is verified before a window and a mismatch refuses it.
"""

import argparse
import math
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from host.battery import phases, probes, report, snapshot, stats
from host.device import probes as device_probes


def add_window_arguments(parser: argparse.ArgumentParser, minimum_minutes: float) -> None:
    """
    Add the duration option shared by the windowed phases, floored at the documented length.

    A shorter window is a different measurement, not a cheaper one, so the minimum is the default.
    """
    parser.add_argument(
        "--minutes",
        type=minutes_at_least(minimum_minutes),
        default=minimum_minutes,
        help=f"Length of each measurement window in minutes (at least {minimum_minutes:g})",
    )


def add_arm_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arm and baseline options shared by the visible and pilot phases."""
    parser.add_argument("--arm", choices=[arm.value for arm in stats.Arm], required=True, help="A live, B baseline")
    parser.add_argument(
        "--baseline",
        choices=[baseline.value for baseline in stats.Baseline],
        default=None,
        help="Which static wallpaper arm B is bound to; required for arm B",
    )


def positive_float(raw: str) -> float:
    """
    Parse a finite, strictly positive duration within the bound a phase will hold the device for.

    `inf` would make the sampling loop unbounded and `nan` slips past a `<= 0` test, so both are
    refused. Finiteness is not enough: `1e15` minutes makes progress but is ~two billion years of
    samples, so the real rule is `MAXIMUM_WINDOW_MINUTES`.
    """
    value = float(raw)
    if not math.isfinite(value):
        message = f"{raw} is not a finite duration"
        raise argparse.ArgumentTypeError(message)
    if value <= 0:
        message = f"{raw} is not a positive duration"
        raise argparse.ArgumentTypeError(message)
    if value > phases.MAXIMUM_WINDOW_MINUTES:
        message = (
            f"{raw} minutes is longer than the {phases.MAXIMUM_WINDOW_MINUTES / 60.0:.0f} hours a measurement "
            "phase will hold the device for; a longer window is not a longer measurement, and the "
            "sampling loop would be running on the shared device long after the criterion was met"
        )
        raise argparse.ArgumentTypeError(message)
    return value


def minutes_at_least(minimum: float) -> Callable[[str], float]:
    """Build the `--minutes` parser for one phase, refusing a window below that phase's minimum."""

    def parse_minutes(raw: str) -> float:
        value = positive_float(raw)
        if value < minimum:
            message = (
                f"{raw} minutes is below the {minimum:g} minutes the acceptance matrix documents for "
                "this criterion, so the window would not be the measurement the budget is defined on"
            )
            raise argparse.ArgumentTypeError(message)
        return value

    return parse_minutes


def nonblank_reason(raw: str) -> str:
    """
    Parse the reason a run declares one of the document's baselines unavailable.

    A blank reason declares nothing, and the empty device-limited line would read as the silent
    omission the declaration replaces.
    """
    reason = raw.strip()
    if not reason:
        message = "an empty reason declares nothing; say why the device cannot set the baseline"
        raise argparse.ArgumentTypeError(message)
    return reason


def positive_int(raw: str) -> int:
    """Parse a strictly positive integer for the command line."""
    value = int(raw)
    if value <= 0:
        message = f"{raw} is not a positive count"
        raise argparse.ArgumentTypeError(message)
    return value


def positive_int_between(minimum: int, maximum: int) -> Callable[[str], int]:
    """
    Build a count parser that refuses a value outside the range the phase can use.

    Both ends refuse before any device work: the lower bound is what a noise floor needs, the upper
    what a pilot will hold the device for.
    """

    def parse_count(raw: str) -> int:
        value = positive_int(raw)
        if value < minimum:
            message = (
                f"{raw} windows is below the {minimum} a noise floor needs, so the run would measure "
                "no floor at all; the refusal is here rather than after the windows have been run"
            )
            raise argparse.ArgumentTypeError(message)
        if value > maximum:
            message = (
                f"{raw} windows is more than the {maximum} a pilot will hold the device for; a noise "
                "floor does not need that many, and the phase would still be opening windows long "
                "after the floor was established"
            )
            raise argparse.ArgumentTypeError(message)
        return value

    return parse_count


def add_state_argument(parser: argparse.ArgumentParser) -> None:
    """Add the state-directory option shared by the phase and report commands."""
    parser.add_argument(
        "--state",
        type=Path,
        default=snapshot.DEFAULT_STATE_DIR,
        help="Directory the phase records are written to",
    )


def add_black_unavailable_argument(parser: argparse.ArgumentParser) -> None:
    """Add the black-baseline declaration shared by the phase and report commands."""
    parser.add_argument(
        "--black-unavailable",
        type=nonblank_reason,
        default=None,
        metavar="REASON",
        help=(
            "Declare that this device cannot set a static wallpaper, so B4's black baseline is "
            "device-limited rather than an unmeasured criterion"
        ),
    )


def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command line: one bounded phase per subcommand."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--serial", help="Target ADB serial", default=None)
    add_state_argument(parser)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("resources", help="B2: wake locks and alarms the app holds")

    hidden = subcommands.add_parser("hidden", help="B3: hidden-state CPU over a screen-off window")
    add_window_arguments(hidden, stats.MINIMUM_HIDDEN_MINUTES)

    visible = subcommands.add_parser("visible", help="B4/B5: one visible window")
    add_arm_arguments(visible)
    add_window_arguments(visible, stats.MINIMUM_WINDOW_MINUTES)

    pilot = subcommands.add_parser("pilot", help="Repeated windows whose spread is the noise floor")
    add_arm_arguments(pilot)
    add_window_arguments(pilot, stats.MINIMUM_WINDOW_MINUTES)
    pilot.add_argument(
        "--runs",
        type=positive_int_between(stats.MIN_PAIRED_SAMPLES, phases.MAXIMUM_PILOT_RUNS),
        default=stats.MIN_PAIRED_SAMPLES,
        help=(
            f"Windows to run (between {stats.MIN_PAIRED_SAMPLES}, which is what a noise floor needs, and "
            f"{phases.MAXIMUM_PILOT_RUNS}, which is what the device will be held for)"
        ),
    )

    memory = subcommands.add_parser("memory", help="B6: total-PSS growth over a long window")
    add_window_arguments(memory, stats.MINIMUM_MEMORY_MINUTES)

    report = subcommands.add_parser("report", help="Aggregate the records the phases wrote")
    add_black_unavailable_argument(report)
    return parser


def dispatch(args: argparse.Namespace, device: device_probes.AdbDevice, recorder: snapshot.Recorder) -> None:
    """Run the requested phase, writing each record to the state file as it is produced."""
    if args.command == "resources":
        recorder.emit(phases.phase_resources(device))
    elif args.command == "hidden":
        recorder.emit(phases.phase_hidden(device, args.minutes, recorder=recorder))
    elif args.command == "visible":
        recorder.emit(
            phases.phase_visible(
                device,
                arm=stats.Arm(args.arm),
                baseline=baseline_of(args),
                minutes=args.minutes,
                recorder=recorder,
            )
        )
    elif args.command == "pilot":
        # `phase_pilot` emits its windows itself and returns only the floor record.
        for record in phases.phase_pilot(
            device,
            arm=stats.Arm(args.arm),
            baseline=baseline_of(args),
            runs=args.runs,
            minutes=args.minutes,
            recorder=recorder,
        ):
            recorder.emit(record)
    elif args.command == "memory":
        recorder.emit(phases.phase_memory(device, args.minutes, recorder=recorder))


def baseline_of(args: argparse.Namespace) -> stats.Baseline | None:
    """Read the requested baseline, as `None` when the arm does not need one."""
    return stats.Baseline(args.baseline) if args.baseline else None


def main(argv: list[str] | None = None) -> int:
    """Parse the command line, run one bounded phase, and write its record."""
    args = build_argument_parser().parse_args(argv)
    if args.command == "report":
        try:
            return report.phase_report(snapshot.load_records(args.state), black_unavailable=args.black_unavailable)
        except device_probes.ProbeError as error:
            print(f"ERROR: {device_probes.error_detail(error)}", file=sys.stderr)
            return 1
    recorder = snapshot.Recorder(args.state)
    try:
        # Inside the guard because it runs `adb devices`; `select_target_serial` exits via
        # `SystemExit`, which this guard deliberately does not catch.
        serial = device_probes.select_target_serial(args.serial)
        device = device_probes.AdbDevice(serial)
        sdk = device.run_adb(["shell", "getprop", "ro.build.version.sdk"]).decode(errors="replace")
        print(f"$ adb -s {serial}  (API {probes.parse_api_level(sdk)})")
        dispatch(args, device, recorder)
    except (subprocess.SubprocessError, OSError, device_probes.ProbeError) as error:
        print(f"ERROR: {device_probes.error_detail(error)}", file=sys.stderr)
        return 1
    restore_failures = report.collect_restore_problems([record.values for record in recorder.records])
    if restore_failures:
        print("DEVICE RESTORE FAILURES:", file=sys.stderr)
        for failure in restore_failures:
            print(f"  - FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


def build_report_argument_parser() -> argparse.ArgumentParser:
    """Build the standalone command line for aggregating phase records into a report."""
    parser = argparse.ArgumentParser(description=report.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_state_argument(parser)
    add_black_unavailable_argument(parser)
    return parser


def main_report(argv: list[str] | None = None) -> int:
    """Parse the standalone report command line and print the aggregate report."""
    args = build_report_argument_parser().parse_args(argv)
    forwarded = [f"--state={args.state}", "report"]
    if args.black_unavailable is not None:
        forwarded.append(f"--black-unavailable={args.black_unavailable}")
    return main(forwarded)
