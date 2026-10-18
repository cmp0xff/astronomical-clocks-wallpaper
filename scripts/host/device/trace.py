# SPDX-License-Identifier: Apache-2.0
"""
Producer accounting for a hidden window (#125 criterion 1).

A frame count comes from the wallpaper's own producer pipeline, not its lifecycle state. The window
is bracketed by a visible control, so a zero count is distinguishable from a trace that silently
produced nothing: a control that finds no frames is an error, never a zero.
"""

import math
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Final

from host.device import probes

# A trace dump moves the whole trace buffer over the transport, which a plain shell call cannot.
ADB_TRACE_TIMEOUT_SECONDS: Final = 120

TRACE_WINDOW_SECONDS: Final = 30.0
TRACE_DISPLAY_SAMPLE_SECONDS: Final = 2.0
# The probe holds a shared device for the whole window, so both durations are bounded to keep
# `seconds / sample_seconds` finite and the read count in [1, MAX_TRACE_SAMPLES]; the floor is the
# interval at which a 600 s window reaches the cap.
MIN_TRACE_DURATION_SECONDS: Final = 0.1
MAX_TRACE_WINDOW_SECONDS: Final = 600.0
MAX_TRACE_SAMPLES: Final = 6000
TRACE_CATEGORIES: Final = ("gfx", "view")
# The text trace marks each event with `B|<pid>|<name>` / `E|<pid>`; only the `B` half is counted, or
# every event would be doubled, and it carries the name.
TRACE_MARK_PATTERN: Final = re.compile(r"tracing_mark_write:\s*B\|\s*(\d+)\|([^\s|]+)")
# A produced frame is the buffer the producer hands to the consumer; a dequeue without a queue is a
# draw that never committed.
PRODUCER_QUEUE_EVENT: Final = "queueBuffer"
PRODUCER_TRACKED_EVENTS: Final = frozenset({PRODUCER_QUEUE_EVENT, "dequeueBuffer"})


@dataclass(frozen=True)
class ProducerWindow:
    """One bounded producer-trace window, with the display state sampled inside it."""

    window_seconds: float
    pid: int
    event_counts: dict[str, int]
    trace_lines: int
    display_states: tuple[bool | None, ...]

    @property
    def frame_count(self) -> int:
        """The number of frames the traced process handed to the consumer during the window."""
        return self.event_counts.get(PRODUCER_QUEUE_EVENT, 0)

    @property
    def display_off_at_every_sample(self) -> bool:
        """Whether every discrete sample found the display off; between samples is unseen."""
        return bool(self.display_states) and all(state is False for state in self.display_states)

    @property
    def display_on_at_every_sample(self) -> bool:
        """Whether every discrete sample found the display on; same limitation."""
        return bool(self.display_states) and all(state is True for state in self.display_states)

    @property
    def display_unknown_samples(self) -> int:
        """How many samples could not be read at all."""
        return sum(1 for state in self.display_states if state is None)


def parse_producer_events(trace_text: str, pid: int) -> dict[str, int]:
    """Count one process's producer buffer events in an `atrace` text trace."""
    return Counter(
        name
        for match in TRACE_MARK_PATTERN.finditer(trace_text)
        if (captured_pid := probes.captured_text(match, 1)).isdigit()
        and int(captured_pid) == pid
        and (name := probes.captured_text(match, 2)) in PRODUCER_TRACKED_EVENTS
    )


def capture_producer_window(
    serial: str,
    pid: int,
    *,
    seconds: float = TRACE_WINDOW_SECONDS,
    sample_seconds: float = TRACE_DISPLAY_SAMPLE_SECONDS,
) -> ProducerWindow:
    """
    Trace a process's producer pipeline, sampling the display throughout the window.

    An unusable trace is raised rather than read as zero frames. Both durations must be in
    [MIN_TRACE_DURATION_SECONDS, MAX_TRACE_WINDOW_SECONDS]; the returned span is the measured local
    one the reads make at least `seconds` wide.
    """
    # Non-finite values are named explicitly: the range is what bounds the loop, keeping the quotient
    # finite and the read count in [1, MAX_TRACE_SAMPLES].
    if not (
        math.isfinite(seconds)
        and math.isfinite(sample_seconds)
        and MIN_TRACE_DURATION_SECONDS <= seconds <= MAX_TRACE_WINDOW_SECONDS
        and MIN_TRACE_DURATION_SECONDS <= sample_seconds <= MAX_TRACE_WINDOW_SECONDS
    ):
        detail = (
            f"a trace window needs finite durations between {MIN_TRACE_DURATION_SECONDS} and "
            f"{MAX_TRACE_WINDOW_SECONDS:.0f} s but got seconds={seconds}, sample_seconds={sample_seconds}"
        )
        raise probes.ProbeError(detail)
    sample_count = math.ceil(seconds / sample_seconds)
    start_command = ["shell", "atrace", "--async_start", "-a", str(pid)]
    start_command.extend(TRACE_CATEGORIES)
    try:
        # The clock is read before the start call: the trace is already active while `--async_start`
        # is in flight. The start shares the dump/stop guard, so a raising start cannot strand it.
        started = time.monotonic()
        probes.run_adb(start_command, serial=serial, timeout=ADB_TRACE_TIMEOUT_SECONDS)
        states: list[bool | None] = []
        # Reads are paced against a monotonic deadline so a slow read shortens the next sleep instead
        # of pushing every later read out; the span is measured because a read can still overrun it.
        for index in range(sample_count):
            deadline = started + min((index + 1) * sample_seconds, seconds)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            elif index + 1 < sample_count and time.monotonic() >= started + seconds:
                # A read that would start after the window's own end is not taken: the count bounds how
                # many reads are owed, not how long each may take. The last read is exempt because it
                # closes the window.
                overrun = time.monotonic() - (started + seconds)
                detail = (
                    f"the {seconds} s trace window could not take its display reads inside the "
                    f"window: read {index + 1} of {sample_count} would have started {overrun:.1f} s "
                    "after the window ended, so the samples would not span the width it was asked for"
                )
                raise probes.ProbeError(detail)
            states.append(probes.read_screen_on(serial))
        dump = probes.run_adb(["shell", "atrace", "--async_dump"], serial=serial, timeout=ADB_TRACE_TIMEOUT_SECONDS)
        # The trace opens before the start call and closes after the dump returns, so the locally
        # measured span is the tightest bound on a window that covers at least `seconds`.
        window_seconds = time.monotonic() - started
    finally:
        try:
            probes.run_adb(["shell", "atrace", "--async_stop"], serial=serial, timeout=ADB_TRACE_TIMEOUT_SECONDS)
        except (subprocess.SubprocessError, OSError) as stop_error:
            # A failed stop fails the window: an unstopped trace would leak this window's frames into
            # the next. It is reported first because the caller's error names only the stop.
            print(
                f"WARNING: atrace --async_stop failed and a trace may still be active: "
                f"{probes.error_detail(stop_error)}",
                file=sys.stderr,
            )
            raise
    trace_text = dump.decode("utf-8", errors="replace")
    if "tracing_mark_write" not in trace_text:
        # The check is on the buffer, not the pid: a process drawing nothing legitimately holds no
        # marks, but a buffer holding none from any process was never filled.
        detail = (
            f"the atrace window produced no mark lines at all, so it cannot evidence a frame count "
            f"for pid {pid}; check that atrace is permitted on this build before reading the window"
        )
        raise probes.ProbeError(detail)
    return ProducerWindow(
        window_seconds=window_seconds,
        pid=pid,
        event_counts=parse_producer_events(trace_text, pid),
        trace_lines=len(trace_text.splitlines()),
        display_states=tuple(states),
    )
