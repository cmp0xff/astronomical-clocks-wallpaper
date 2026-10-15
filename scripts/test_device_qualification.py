# SPDX-License-Identifier: Apache-2.0
"""Host-only regression checks; these tests establish no physical-device behavior."""

import argparse
import contextlib
import io
import json
import math
import struct
import subprocess
import sys
import unittest
from types import ModuleType
from typing import Final
from unittest.mock import MagicMock, Mock, patch
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

import device_layer
import device_qualification as qualification
import device_smoke
from device_test_fixtures import (
    BYTES_PER_PIXEL,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    circular_difference_deg,
    hand_frame,
    palette_frame,
)

NUMERAL_GLYPH_OUTER_RADIUS: Final = 1.254

RIM_INSET_STROKE_RADIUS: Final = 1.344

MIN_PROBE_POINTS: Final = 2

FIXTURE_START_PID: Final = 111

FIXTURE_REBOUND_PID: Final = 222

# detect_hand_angle readings in the mocked main() path, in call order: the baseline, wake, preview,
# surface, rebind and time-travel-baseline captures (six 0.0 values), then the +30m and +12h advances
# (7.5 and 180.0), then the four midnight-rollover samples. The log-scan test asserts the count and
# that no phase but the log scan fails, so a value landing in the wrong phase is caught there too.
FIXTURE_HAND_ANGLES: Final = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 7.5, 180.0, 168.75, 179.958, 180.042, 191.25]

# get_wallpaper_pid readings in the mocked main() path, in call order: the pre-run baseline, the three
# screen-off reads (before the sleep request, after the sleep settles, and at the CPU sample window's
# end), then the rebind's pre-kill read and its two poll reads (the first poll still sees the old PID,
# the second observes the rebound one). The count is asserted by the log-scan test.
FIXTURE_WALLPAPER_PIDS: Final = [FIXTURE_START_PID] * 6 + [FIXTURE_REBOUND_PID]

# The physical size the mocked baseline reports, and the override the surface phase is expected to
# apply: the latter comes from the harness's own chooser, so the two cannot drift apart.
FIXTURE_PHYSICAL_SIZE: Final = "1080x2408"

FIXTURE_RECREATE_SIZE: Final = device_layer.choose_recreate_size(FIXTURE_PHYSICAL_SIZE, None)

# read_display_size readings in the mocked main() path, in call order: the pre-run baseline read, the
# surface phase's active-override read, and its post-restore read. The count is asserted by the
# log-scan test.
FIXTURE_DISPLAY_SIZES: Final = [
    (FIXTURE_PHYSICAL_SIZE, None),
    (FIXTURE_PHYSICAL_SIZE, FIXTURE_RECREATE_SIZE),
    (FIXTURE_PHYSICAL_SIZE, None),
]

# A realistic observing_location.xml dump: SharedPreferences XML-escapes the quotes in the stored
# JSON record, and LocationStore.kt writes the zone as a full IANA id.
VALID_PREFS_XML: Final = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <string name="location">'
    "{&quot;version&quot;:1,&quot;latitude&quot;:50.08,&quot;longitude&quot;:14.42,"
    "&quot;source&quot;:&quot;MANUAL&quot;,&quot;zoneId&quot;:&quot;Europe\\/Prague&quot;}"
    "</string>\n"
    "</map>\n"
)

# One record LocationStore.load() accepts: integral version 1, in-range coordinates, a known source,
# and a real IANA zone. Tests derive rejected variants from it field by field.
VALID_LOCATION_RECORD: Final = {
    "version": 1,
    "latitude": 50.08,
    "longitude": 14.42,
    "source": "MANUAL",
    "zoneId": "Europe/Prague",
}


def prefs_xml(record: object) -> str:
    """Wrap a stored record in the shared_prefs XML envelope, escaping quotes as LocationStore does."""
    text = record if isinstance(record, str) else json.dumps(record)
    escaped = xml_escape(text, {'"': "&quot;"})
    return (
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        "<map>\n"
        f'    <string name="location">{escaped}</string>\n'
        "</map>\n"
    )


DUPLICATED_CONSTANT_NAMES: Final = (
    "ADB_TIMEOUT_SECONDS",
    "ADB_RESTORE_TIMEOUT_SECONDS",
    "ANGLE_TOLERANCE_DEG",
    "CIVIL_MIDNIGHT_ANGLE_DEG",
    "CIVIL_SECONDS_PER_DEGREE",
    "COARSE_BIN_COUNT",
    "COARSE_BIN_WIDTH_DEG",
    "DARK_HAND_MIN_RED_MINUS_BLUE",
    "DARK_HAND_RGB_BOUNDS",
    "DARK_RIM_RGB",
    "DEBUG_ACTION",
    "DEBUG_CLOCK_RESET_EXTRAS",
    "DEBUG_CLOCK_RESET_MESSAGE",
    "DISPLAY_LOG_TAG",
    "DISPLAY_STATE_PATTERN",
    "KEYGUARD_SHOWING_PATTERN",
    "EXPECTED_ADVANCE_30M_DEG",
    "FULL_TURN_DEG",
    "HALF_TURN_DEG",
    "HAND_MIN_SAMPLES",
    "HAND_SCAN_INNER_FRACTION",
    "HAND_SCAN_OUTER_FRACTION",
    "HAND_SCAN_STEP_PX",
    "LIGHT_HAND_RGB_BOUNDS",
    "LIGHT_RIM_RGB",
    "MAX_BRIGHTNESS",
    "MIN_BRIGHTNESS",
    "MIN_SPLIT_FIELDS",
    "OVERRIDE_SIZE_PATTERN",
    "PHYSICAL_SIZE_PATTERN",
    "PIXEL_STRIDE",
    "REFINE_WEDGE_DEG",
    "RIM_PROBE_BEARINGS_DEG",
    "RIM_PROBE_RADIUS_FRACTION",
    "SCREENCAP_HEADER_BYTES",
    "SERVICE_LOG_TAG",
    "WAKE_READ_PATTERN",
)

HAND_UP_BEARING_DEG: Final = 0.0

HAND_DOWN_BEARING_DEG: Final = 180.0

DARK_HAND_RGB: Final = (244, 229, 184)

LIGHT_HAND_RGB: Final = (78, 52, 27)


class DeviceQualificationTest(unittest.TestCase):
    """Parsing, decision, and restore-verification helpers of the qualification harness."""

    def test_total_pss_parser_requires_a_numeric_total(self) -> None:
        self.assertEqual(qualification.extract_total_pss_kb("TOTAL 12345 0 0"), 12345)
        self.assertIsNone(qualification.extract_total_pss_kb("TOTAL measured"))

    def test_pss_growth_requires_both_samples_and_respects_budget(self) -> None:
        self.assertIsNone(qualification.evaluate_pss_growth(None, 12, 10))
        self.assertEqual(qualification.evaluate_pss_growth(100, 110, 10), (10, True))
        self.assertEqual(qualification.evaluate_pss_growth(100, 111, 10), (11, False))

    def test_pss_budget_must_be_non_negative(self) -> None:
        self.assertEqual(qualification.non_negative_int("0"), 0)
        with self.assertRaises(argparse.ArgumentTypeError):
            qualification.non_negative_int("-1")

    @patch.object(device_layer, "run_adb", return_value=b"mWakefulness=Awake")
    def test_awake_screen_state_is_detected(self, _run_adb: MagicMock) -> None:
        self.assertTrue(device_layer.read_screen_on("device"))

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=[b"power state unknown", b"Display State=OFF"],
    )
    def test_display_state_is_used_when_wakefulness_is_unknown(self, _run_adb: MagicMock) -> None:
        self.assertFalse(device_layer.read_screen_on("device"))

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=[b"power state unknown", b"display state unknown"],
    )
    def test_unknown_screen_state_stays_unknown(self, run_adb: MagicMock) -> None:
        self.assertIsNone(device_layer.read_screen_on("device"))
        self.assertEqual(run_adb.call_count, 2)

    @patch.object(device_layer, "run_adb", return_value=b"night mode output changed")
    def test_unknown_night_mode_stays_unknown(self, _run_adb: MagicMock) -> None:
        self.assertIsNone(device_layer.read_night_mode("device"))

    @patch.object(device_layer, "run_adb", return_value=b"Night mode: yes\n")
    def test_night_mode_is_parsed(self, _run_adb: MagicMock) -> None:
        self.assertEqual(device_layer.read_night_mode("device"), "yes")

    @patch.object(device_layer, "run_adb", return_value=b"  1234 5678\n")
    def test_wallpaper_pid_reads_the_first_numeric_token(self, _run_adb: MagicMock) -> None:
        self.assertEqual(device_layer.get_wallpaper_pid("device"), 1234)

    @patch.object(device_layer, "run_adb", side_effect=subprocess.CalledProcessError(1, ["adb"], stderr=b""))
    def test_an_absent_wallpaper_process_is_none_not_an_error(self, _run_adb: MagicMock) -> None:
        self.assertIsNone(device_layer.get_wallpaper_pid("device"))

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
    )
    def test_an_offline_pidof_probe_raises_instead_of_masquerading_as_absent(self, _run_adb: MagicMock) -> None:
        with self.assertRaises(subprocess.CalledProcessError):
            device_layer.get_wallpaper_pid("device")

    @patch.object(device_layer, "run_adb")
    def test_wallpaper_visibility_aggregates_complete_fields(self, run_adb: MagicMock) -> None:
        cases = (
            (b"mVisible=false\nmVisible=true", True),
            (b"mVisible=true\nmVisible=false", True),
            (b"mVisible=false\nmVisible=false", False),
            (b"mVisible=true\nmVisible=true", True),
            (b"mVisible=false mVisible=true mReportedVisible=false", True),
            (b"mVisible=false mVisible=false", False),
            (b"mVisible=trueish", None),
            (b"mVisible=false-ish", None),
            (b"mVisible=false=broken", None),
            (b"mVisible=true\xef\xbf\xbd", None),
            (b"mVisible=", None),
            (b"mVisible=\nmVisible=false", None),
            (b"mVisible=false mVisible=unknown", None),
            (b"mVisible=false mVisible=", None),
            (b"mVisible=true mVisible=unknown", True),
            (b"mVisible=unknown mVisible=true", True),
            (b"mReportedVisible=true other_mVisible=false", None),
            (b"mVisible=false mReportedVisible=true", False),
            (b"mVisible=false; mVisible=false}", False),
            (b"mVisible= mVisible=true", True),
            (b"mVisible= mVisible=false", None),
            (b"no matching pattern", None),
            (b"", None),
        )
        for output, expected in cases:
            with self.subTest(output=output):
                run_adb.return_value = output
                self.assertIs(device_layer.read_wallpaper_visible("device"), expected)
        run_adb.assert_called_with(
            ["shell", "dumpsys", "activity", "service", device_layer.SERVICE_NAME], serial="device"
        )

    @patch.object(device_layer, "run_adb")
    def test_failed_wallpaper_visible_probe_stays_none(self, run_adb: MagicMock) -> None:
        errors = (
            subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
            subprocess.TimeoutExpired(["adb"], 10, stderr=b"transport timeout"),
            OSError("adb transport closed"),
        )
        for error in errors:
            with self.subTest(error=error), contextlib.redirect_stderr(io.StringIO()) as error_output:
                run_adb.side_effect = error
                self.assertIsNone(device_layer.read_wallpaper_visible("device"))
            self.assertIn("wallpaper visibility probe failed", error_output.getvalue())
            self.assertIn(device_layer.error_detail(error), error_output.getvalue())

    @patch.object(device_layer, "run_adb")
    def test_process_cpu_ticks_reads_utime_and_stime(self, run_adb: MagicMock) -> None:
        stat_line = b"123 (wallpaper.service) S 1 2 3 4 5 6 7 8 9 10 42 18 0 0\n"
        run_adb.return_value = stat_line
        self.assertEqual(device_layer.read_process_cpu_ticks("device", 123), 60)
        run_adb.assert_called_with(["shell", "cat", "/proc/123/stat"], serial="device")

    @patch.object(device_layer, "run_adb")
    def test_process_cpu_ticks_returns_none_for_malformed_stat(self, run_adb: MagicMock) -> None:
        cases = (b"invalid", b"123 (comm)", b"123 (comm) S 1 2", b"123 no_parens")
        for output in cases:
            with self.subTest(output=output):
                run_adb.return_value = output
                self.assertIsNone(device_layer.read_process_cpu_ticks("device", 123))

    @patch.object(device_layer, "run_adb")
    def test_process_cpu_ticks_handles_probe_failure(self, run_adb: MagicMock) -> None:
        run_adb.side_effect = subprocess.CalledProcessError(1, ["adb"], stderr=b"no such process")
        with contextlib.redirect_stderr(io.StringIO()) as error_output:
            self.assertIsNone(device_layer.read_process_cpu_ticks("device", 123))
        self.assertIn("process CPU stat probe failed", error_output.getvalue())

    def test_saved_site_zone_parser_reads_the_stored_zone(self) -> None:
        self.assertEqual(device_layer.parse_saved_site_zone(VALID_PREFS_XML), "Europe/Prague")

    def test_saved_site_zone_parser_returns_none_for_unusable_prefs(self) -> None:
        cases = (
            "",
            "<map></map>",
            '<map><string name="latitude">50.08</string></map>',
            "<map><string",
            prefs_xml("not json"),
            prefs_xml(""),
            prefs_xml("[1, 2]"),
            prefs_xml("5"),
            prefs_xml('{"version": 1} trailing'),
        )
        for xml in cases:
            with self.subTest(xml=xml):
                self.assertIsNone(device_layer.parse_saved_site_zone(xml))

    def test_saved_site_zone_parser_mirrors_the_store_record_validation(self) -> None:
        """Only a record LocationStore.load() accepts yields its zone; rejected records fall back."""
        accepted = (
            VALID_LOCATION_RECORD,
            {**VALID_LOCATION_RECORD, "latitude": 90, "longitude": 180},
            {**VALID_LOCATION_RECORD, "latitude": -90, "longitude": -180},
            {**VALID_LOCATION_RECORD, "source": "CURRENT_COARSE"},
        )
        for record in accepted:
            with self.subTest(record=record):
                self.assertEqual(device_layer.parse_saved_site_zone(prefs_xml(record)), "Europe/Prague")

        def without(key: str) -> dict[str, object]:
            return {name: value for name, value in VALID_LOCATION_RECORD.items() if name != key}

        rejected = (
            without("version"),
            {**VALID_LOCATION_RECORD, "version": "1"},
            {**VALID_LOCATION_RECORD, "version": 1.0},
            {**VALID_LOCATION_RECORD, "version": True},
            {**VALID_LOCATION_RECORD, "version": 2},
            without("latitude"),
            {**VALID_LOCATION_RECORD, "latitude": "50.08"},
            {**VALID_LOCATION_RECORD, "latitude": True},
            {**VALID_LOCATION_RECORD, "latitude": None},
            {**VALID_LOCATION_RECORD, "latitude": 90.0001},
            {**VALID_LOCATION_RECORD, "latitude": -90.0001},
            {**VALID_LOCATION_RECORD, "latitude": float("nan")},
            without("longitude"),
            {**VALID_LOCATION_RECORD, "longitude": "14.42"},
            {**VALID_LOCATION_RECORD, "longitude": 180.0001},
            without("source"),
            {**VALID_LOCATION_RECORD, "source": "manual"},
            {**VALID_LOCATION_RECORD, "source": "GPS"},
            {**VALID_LOCATION_RECORD, "source": 5},
            {**VALID_LOCATION_RECORD, "source": ["MANUAL"]},
            without("zoneId"),
            {**VALID_LOCATION_RECORD, "zoneId": 5},
            {**VALID_LOCATION_RECORD, "zoneId": None},
            {**VALID_LOCATION_RECORD, "zoneId": ""},
        )
        for record in rejected:
            with self.subTest(record=record):
                self.assertIsNone(device_layer.parse_saved_site_zone(prefs_xml(record)))

    def test_a_present_but_unbuildable_zone_is_returned_for_the_phase_to_reject(self) -> None:
        """A stored zone the harness cannot build must not be read as "use the device zone"."""
        record = {**VALID_LOCATION_RECORD, "zoneId": "Invalid/Zone"}
        self.assertEqual(device_layer.parse_saved_site_zone(prefs_xml(record)), "Invalid/Zone")

    @patch.object(device_layer, "run_adb", return_value=VALID_PREFS_XML.encode())
    def test_saved_site_zone_reads_the_shared_prefs_file(self, run_adb: MagicMock) -> None:
        self.assertEqual(device_layer.read_saved_site_zone_id("device"), "Europe/Prague")
        run_adb.assert_called_once_with(
            ["shell", "run-as", device_layer.PACKAGE_NAME, "cat", device_layer.LOCATION_PREFS_PATH],
            serial="device",
        )

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(
            1,
            ["adb"],
            stderr=b"cat: " + device_layer.LOCATION_PREFS_PATH.encode() + b": No such file or directory",
        ),
    )
    def test_a_missing_saved_site_prefs_file_reads_as_absent(self, _run_adb: MagicMock) -> None:
        """A prefs file never written is the store's "no saved site", not a probe error."""
        self.assertIsNone(device_layer.read_saved_site_zone_id("device"))

    @patch.object(device_layer, "run_adb", return_value=prefs_xml({"version": 2}).encode())
    def test_a_readable_prefs_file_with_no_loadable_record_reads_as_absent(self, _run_adb: MagicMock) -> None:
        """A successful read whose record the store rejects still means "no saved site", not an error."""
        self.assertIsNone(device_layer.read_saved_site_zone_id("device"))

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(1, ["adb"], stderr=b"package not debuggable"),
    )
    def test_a_saved_site_probe_failure_raises_instead_of_reading_as_absent(self, _run_adb: MagicMock) -> None:
        """A failed read must not be read as "no saved site"; that would fall back to the device zone."""
        with self.assertRaises(device_layer.ProbeError) as raised:
            device_layer.read_saved_site_zone_id("device")
        self.assertIn("saved-site prefs probe failed", str(raised.exception))
        self.assertIn("package not debuggable", str(raised.exception))

    @patch.object(device_layer, "run_adb", side_effect=OSError("adb transport closed"))
    def test_a_saved_site_probe_oserror_raises(self, _run_adb: MagicMock) -> None:
        with self.assertRaises(device_layer.ProbeError):
            device_layer.read_saved_site_zone_id("device")

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=device_layer.ADB_TIMEOUT_SECONDS),
    )
    def test_a_saved_site_probe_timeout_raises_instead_of_aborting_the_run(self, _run_adb: MagicMock) -> None:
        """A stalled transport must fail this phase through ProbeError, not escape to main()."""
        with self.assertRaises(device_layer.ProbeError):
            device_layer.read_saved_site_zone_id("device")

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(
            1, ["adb"], stderr=b"cat: /data/local/tmp/nope.xml: No such file or directory"
        ),
    )
    def test_an_absent_marker_naming_another_path_is_not_a_saved_site_absence(self, _run_adb: MagicMock) -> None:
        """An ENOENT about some other path is a probe error, not the store's "no saved site"."""
        with self.assertRaises(device_layer.ProbeError):
            device_layer.read_saved_site_zone_id("device")

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(
            1,
            ["adb"],
            stderr=b"cat: " + device_layer.LOCATION_PREFS_PATH.encode() + b": Datei nicht gefunden",
        ),
    )
    def test_a_localized_absence_message_is_not_read_as_absent(self, _run_adb: MagicMock) -> None:
        """A non-English not-found message naming the prefs path must fail loudly, not read as absent."""
        with self.assertRaises(device_layer.ProbeError):
            device_layer.read_saved_site_zone_id("device")

    @patch.object(device_layer, "run_adb", return_value=b"100\n")
    def test_clock_tick_rate_is_parsed(self, _run_adb: MagicMock) -> None:
        self.assertEqual(device_layer.read_process_cpu_clock_ticks("device"), 100)

    @patch.object(device_layer, "run_adb")
    def test_clock_tick_rate_rejects_unusable_output(self, run_adb: MagicMock) -> None:
        for output in (b"", b"not a number\n", b"0\n", b"-5\n"):
            with self.subTest(output=output):
                run_adb.return_value = output
                self.assertIsNone(device_layer.read_process_cpu_clock_ticks("device"))

    @patch.object(device_layer, "run_adb", side_effect=OSError("adb transport closed"))
    def test_clock_tick_rate_probe_failure_is_reported(self, _run_adb: MagicMock) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as error_output:
            self.assertIsNone(device_layer.read_process_cpu_clock_ticks("device"))
        self.assertIn("clock-tick rate probe failed", error_output.getvalue())

    @patch.object(device_layer, "run_adb", return_value=b"Europe/Prague\n")
    def test_device_timezone_is_parsed(self, _run_adb: MagicMock) -> None:
        self.assertEqual(device_layer.read_device_timezone("device"), "Europe/Prague")

    @patch.object(device_layer, "run_adb", return_value=b"\n")
    def test_an_empty_device_timezone_is_none(self, _run_adb: MagicMock) -> None:
        self.assertIsNone(device_layer.read_device_timezone("device"))

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
    )
    def test_a_failed_wake_is_reported_not_silently_false(self, _run_adb: MagicMock) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as error_output:
            self.assertFalse(device_layer.wake_screen("device"))
        self.assertIn("screen wake failed", error_output.getvalue())

    def test_a_failed_wake_readback_is_reported_not_raised(self) -> None:
        errors = (
            subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
            OSError("adb transport closed"),
        )
        for error in errors:
            with (
                self.subTest(error=type(error).__name__),
                patch.object(device_layer, "run_adb"),
                patch.object(device_layer, "read_screen_on", side_effect=error),
                contextlib.redirect_stderr(io.StringIO()) as error_output,
            ):
                self.assertFalse(device_layer.wake_screen("device"))
                self.assertIn("screen wake failed", error_output.getvalue())

    def test_empty_or_header_only_renderer_log_is_inconclusive(self) -> None:
        self.assertEqual(qualification.matching_renderer_warnings(""), [])
        self.assertEqual(
            qualification.matching_renderer_warnings("--------- beginning of main\n"),
            [],
        )
        self.assertEqual(
            qualification.matching_renderer_warnings("--------- beginning of main\nW/DialRenderer: failed draw\n"),
            ["W/DialRenderer: failed draw"],
        )

    @patch.object(device_layer, "run_adb")
    def test_capture_frame_rejects_inconsistent_payload_length(self, run_adb: MagicMock) -> None:
        run_adb.return_value = struct.pack("<IIII", 2, 2, 1, 0) + bytes(15)
        with self.assertRaisesRegex(RuntimeError, "Unexpected screencap payload size"):
            device_layer.capture_frame("device")

    @patch.object(device_layer, "count_service_log_messages", side_effect=[0, 1])
    @patch.object(device_layer, "run_adb")
    def test_debug_clock_broadcast_requires_a_new_acceptance_log(
        self, run_adb: MagicMock, _count_logs: MagicMock
    ) -> None:
        self.assertTrue(
            device_layer.send_debug_clock_broadcast(
                "device",
                ["--el", "offset_minutes", "30"],
                "Debug clock offset set to 1800000ms",
            )
        )
        run_adb.assert_called_once()

    @patch.object(device_layer, "count_service_log_messages", return_value=2)
    @patch.object(device_layer.time, "sleep")
    @patch.object(device_layer, "run_adb")
    def test_debug_clock_broadcast_fails_without_a_new_acceptance_log(
        self, run_adb: MagicMock, _sleep: MagicMock, count_logs: MagicMock
    ) -> None:
        self.assertFalse(
            device_layer.send_debug_clock_broadcast(
                "device",
                ["--el", "offset_minutes", "30"],
                "Debug clock offset set to 1800000ms",
            )
        )
        run_adb.assert_called_once()
        self.assertEqual(count_logs.call_count, device_layer.LOG_CONFIRM_ATTEMPTS + 1)

    @patch.object(device_layer, "run_adb")
    def test_service_log_count_matches_the_tag_and_message_host_side(self, run_adb: MagicMock) -> None:
        message = "Debug clock offset set to 1800000ms"
        run_adb.return_value = (
            f"10-07 22:55:49.774 15746 15746 I {device_layer.SERVICE_LOG_TAG}: {message}\n"
            f"10-07 22:55:49.775 15746 15746 I SomeOtherTag: {message}\n"
            "10-07 22:55:49.776  1858  1858 D WALLPAPER_SVC:WallpaperManagerService( 1858): "
            f"ComponentInfo{{io.github.godaniya.astronomicalclockswallpaper.{device_layer.SERVICE_LOG_TAG}}}\n"
            f"10-07 22:55:49.777 15746 15746 W {device_layer.SERVICE_LOG_TAG}: unrelated warning\n"
        ).encode()
        self.assertEqual(device_layer.count_service_log_messages("device", message), 1)

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=False)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", "720x1280"))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(device_layer, "run_adb")
    def test_restore_succeeds_only_when_state_matches(
        self,
        run_adb: MagicMock,
        _send_clock: MagicMock,
        read_size: MagicMock,
        read_night: MagicMock,
        read_screen: MagicMock,
        _read_keyguard: MagicMock,
    ) -> None:
        self.assertTrue(
            qualification.restore_device(
                device_layer.AdbDevice("device"), "720x1280", initial_screen_was_on=False, initial_night_mode="auto"
            )
        )
        run_adb.assert_called()
        read_size.assert_called_once_with("device")
        read_night.assert_called_once_with("device")
        read_screen.assert_called_once_with("device")

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", None))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(device_layer, "run_adb")
    def test_restore_failure_is_reported(
        self,
        run_adb: MagicMock,
        _send_clock: MagicMock,
        _read_size: MagicMock,
        _read_night: MagicMock,
        _read_screen: MagicMock,
        _read_keyguard: MagicMock,
    ) -> None:
        def fail_night_mode_restore(command: list[str], **_kwargs: object) -> bytes:
            if command == ["shell", "cmd", "uimode", "night", "auto"]:
                raise subprocess.CalledProcessError(1, command, stderr=b"failed")
            return b""

        run_adb.side_effect = fail_night_mode_restore
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(
                qualification.restore_device(
                    device_layer.AdbDevice("device"), None, initial_screen_was_on=True, initial_night_mode="auto"
                )
            )

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=None)
    @patch.object(device_layer, "read_night_mode", return_value=None)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", None))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(device_layer, "run_adb")
    def test_restore_with_unknown_initial_state_is_not_reported_as_success(
        self,
        run_adb: MagicMock,
        _send_clock: MagicMock,
        _read_size: MagicMock,
        _read_night: MagicMock,
        _read_screen: MagicMock,
        _read_keyguard: MagicMock,
    ) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(
                qualification.restore_device(
                    device_layer.AdbDevice("device"), None, initial_screen_was_on=None, initial_night_mode=None
                )
            )
        self.assertFalse(
            any(call_args.args[0][:3] == ["shell", "cmd", "uimode"] for call_args in run_adb.call_args_list)
        )

    def test_adb_failure_attempts_restore_and_fails_run(self) -> None:
        def fail_environment_wakeup(command: list[str], **_kwargs: object) -> bytes:
            if command == ["shell", "date +'%m-%d %H:%M:%S.000'"]:
                return b"date"
            if command == ["shell", "input", "keyevent", "KEYCODE_WAKEUP"]:
                raise subprocess.CalledProcessError(1, command, stderr=b"device offline")
            return b"--------- beginning of main\n"

        with (
            patch.object(sys, "argv", ["device_qualification.py", "--max-pss-growth-kb", "10"]),
            patch.object(device_layer, "select_target_serial", return_value="device"),
            patch.object(device_layer, "run_adb", side_effect=fail_environment_wakeup),
            patch.object(device_layer, "read_display_size", return_value=("1080x2000", "720x1280")),
            patch.object(device_layer, "read_screen_on", return_value=True),
            patch.object(device_layer, "read_night_mode", return_value="auto"),
            patch.object(device_layer, "read_keyguard_locked", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(qualification, "restore_device", return_value=False) as restore_device,
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as exit_error,
        ):
            qualification.main()

        self.assertEqual(exit_error.exception.code, 1)
        restore_device.assert_called_once()
        self.assertEqual(restore_device.call_args.args[0].serial, "device")
        self.assertEqual(restore_device.call_args.args[1], "720x1280")
        self.assertEqual(restore_device.call_args.kwargs, {"initial_screen_was_on": True, "initial_night_mode": "auto"})

    def test_preflight_adb_failure_is_reported_before_mutation(self) -> None:
        with (
            patch.object(sys, "argv", ["device_qualification.py", "--max-pss-growth-kb", "10"]),
            patch.object(device_layer, "select_target_serial", return_value="device"),
            patch.object(
                device_layer,
                "run_adb",
                side_effect=subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
            ),
            patch.object(qualification, "restore_device") as restore_device,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            qualification.main()

        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("Could not inspect the device before making changes", error_output.getvalue())
        restore_device.assert_not_called()

    @patch.object(device_layer, "read_keyguard_locked", return_value=True)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", None))
    @patch.object(device_layer, "get_wallpaper_pid", return_value=123)
    @patch.object(device_layer, "select_target_serial", return_value="device")
    @patch.object(device_layer, "run_adb", return_value=b"")
    def test_a_locked_keyguard_stops_the_run_before_any_mutation(
        self,
        run_adb: MagicMock,
        _select: MagicMock,
        _pid: MagicMock,
        _size: MagicMock,
        _night: MagicMock,
        _screen: MagicMock,
        _keyguard: MagicMock,
    ) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            qualification.read_device_baseline(None)
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("device is locked", error_output.getvalue())
        self.assertFalse(
            any("KEYCODE_WAKEUP" in call_args.args[0] for call_args in run_adb.call_args_list),
        )

    @patch.object(device_layer, "read_keyguard_locked", return_value=None)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", None))
    @patch.object(device_layer, "get_wallpaper_pid", return_value=123)
    @patch.object(device_layer, "select_target_serial", return_value="device")
    @patch.object(device_layer, "run_adb", return_value=b"")
    def test_an_unreadable_keyguard_state_stops_the_run_before_any_mutation(
        self,
        _run_adb: MagicMock,
        _select: MagicMock,
        _pid: MagicMock,
        _size: MagicMock,
        _night: MagicMock,
        _screen: MagicMock,
        _keyguard: MagicMock,
    ) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            qualification.read_device_baseline(None)
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("could not read the keyguard state", error_output.getvalue())

    @patch.object(device_layer, "read_keyguard_locked", return_value=True)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", None))
    @patch.object(device_layer, "run_adb")
    def test_a_keyguard_that_reappears_fails_restore_even_when_everything_else_matches(
        self, _run_adb: MagicMock, _size: MagicMock, _night: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        with (
            patch.object(device_layer, "send_debug_clock_broadcast", return_value=True),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
        ):
            self.assertFalse(
                qualification.restore_device(
                    device_layer.AdbDevice("device"), None, initial_screen_was_on=True, initial_night_mode="auto"
                )
            )
        self.assertIn("not confirmed unlocked", error_output.getvalue())

    def test_an_unconfirmed_environment_reset_skips_phases_1_through_6_but_still_restores(self) -> None:
        def unconfirmed_setup(_device: device_layer.AdbDevice, failures: list[str]) -> bool:
            failures.append("Environment reset broadcast was not confirmed by the service log")
            return False

        baseline = qualification.DeviceBaseline(
            serial="device",
            run_start_marker="10-06 12:00:00.000",
            physical_size="1080x2000",
            size_override=None,
            screen_was_on=True,
            night_mode="auto",
            wallpaper_pid=123,
        )
        phase_names = (
            "phase_baseline_capture",
            "phase_screen_off_wake",
            "phase_preview_navigation",
            "phase_surface_recreation",
            "phase_process_rebind",
            "phase_time_travel",
            "phase_midnight_rollover",
            "phase_total_pss_growth",
            "phase_hidden_frames",
        )
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["device_qualification.py", "--max-pss-growth-kb", "10"]))
            stack.enter_context(patch.object(qualification, "read_device_baseline", return_value=baseline))
            stack.enter_context(patch.object(qualification, "phase_environment_setup", side_effect=unconfirmed_setup))
            stack.enter_context(patch.object(qualification, "phase_renderer_log_scan", return_value=True))
            restore_device = stack.enter_context(patch.object(qualification, "restore_device", return_value=True))
            phases = {name: stack.enter_context(patch.object(qualification, name)) for name in phase_names}
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            error_output = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            with self.assertRaises(SystemExit) as exit_error:
                qualification.main()

        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("Environment reset broadcast was not confirmed", error_output.getvalue())
        for phase in phases.values():
            phase.assert_not_called()
        restore_device.assert_called_once()

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=False)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=("1080x2000", None))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(device_layer, "run_adb")
    def test_restore_state_mismatch_is_reported(
        self,
        _run_adb: MagicMock,
        _send_clock: MagicMock,
        _read_size: MagicMock,
        _read_night: MagicMock,
        _read_screen: MagicMock,
        _read_keyguard: MagicMock,
    ) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(
                qualification.restore_device(
                    device_layer.AdbDevice("device"), None, initial_screen_was_on=True, initial_night_mode="auto"
                )
            )

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_night_mode", return_value="auto")
    @patch.object(device_layer, "read_display_size", return_value=(None, None))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(device_layer, "run_adb")
    def test_restore_with_an_unreadable_display_size_is_not_reported_as_success(
        self,
        _run_adb: MagicMock,
        _send_clock: MagicMock,
        _read_size: MagicMock,
        _read_night: MagicMock,
        _read_screen: MagicMock,
        _read_keyguard: MagicMock,
    ) -> None:
        # With no override to compare, an unreadable readback collapsed to None == None and passed.
        with contextlib.redirect_stderr(io.StringIO()) as error_output:
            self.assertFalse(
                qualification.restore_device(
                    device_layer.AdbDevice("device"), None, initial_screen_was_on=True, initial_night_mode="auto"
                )
            )
        self.assertIn("could not read the display size after restore", error_output.getvalue())


class RendererLogScanTest(unittest.TestCase):
    """Phase 8's split between an inconclusive scan and a failed collection."""

    @patch.object(
        device_layer,
        "run_adb",
        side_effect=subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
    )
    def test_collection_failure_is_a_failure_not_an_inconclusive_pass(self, _run_adb: MagicMock) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            inconclusive = qualification.phase_renderer_log_scan(
                device_layer.AdbDevice("device"), "10-06 12:00:00.000", results, failures
            )
        self.assertFalse(inconclusive)
        self.assertEqual(len(failures), 1)
        self.assertIn("could not be collected", failures[0])
        self.assertIn("device offline", failures[0])
        self.assertEqual(results[0][0], "renderer log scan")
        self.assertIn("Failed to collect", results[0][1])

    @patch.object(device_layer, "run_adb", return_value=b"--------- beginning of main\n")
    def test_empty_successful_scan_stays_inconclusive_and_fails_nothing(self, _run_adb: MagicMock) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            inconclusive = qualification.phase_renderer_log_scan(
                device_layer.AdbDevice("device"), "10-06 12:00:00.000", results, failures
            )
        self.assertTrue(inconclusive)
        self.assertEqual(failures, [])
        self.assertEqual(results[0][0], "renderer log scan")
        self.assertIn("Inconclusive", results[0][1])

    # typeshed's _patch.__enter__ returns MagicMock | AsyncMock (new defaults to DEFAULT), so the
    # honest element type is their common supertype; callers only read .call_count.
    def _mock_every_phase_succeeding(self, stack: contextlib.ExitStack) -> tuple[Mock, Mock, Mock]:
        """
        Enter the doubles under which every phase except the renderer log scan succeeds.

        The run is driven entirely by these fixtures, so each reading a phase makes has to come from
        the right one: a constant return or a miscounted fixture turns a phase's outcome quiet rather
        than loud. The hand-angle, wallpaper-PID, and display-size mocks are returned so the caller
        can pin how many readings each fixture supplied.
        """
        display_on = True

        def read_screen_on(_serial: str) -> bool:
            return display_on

        def sleep_screen(_serial: str) -> bool:
            nonlocal display_on
            display_on = False
            return True

        def wake_screen(_serial: str) -> bool:
            nonlocal display_on
            display_on = True
            return True

        def adb_side_effect(command: list[str], **_kwargs: object) -> bytes:
            if command[:2] == ["logcat", "-d"]:
                raise subprocess.CalledProcessError(1, command, stderr=b"logcat unavailable")
            if "meminfo" in command:
                return b"TOTAL 1000 0 0\n"
            # List membership is exact, and the action travels as one element of the argument list;
            # testing the element itself never matches, which left the preview launch unconfirmed.
            if any("CHANGE_LIVE_WALLPAPER" in argument for argument in command):
                return b"Status: ok\n"
            return b""

        # read_display_size is not constant across the run, so one return_value cannot serve it: the
        # baseline read sees no override, then the surface phase reads the override it applied and the
        # restored size.
        stack.enter_context(patch.object(sys, "argv", ["device_qualification.py", "--max-pss-growth-kb", "10"]))
        stack.enter_context(patch.object(device_layer, "select_target_serial", return_value="device"))
        stack.enter_context(patch.object(device_layer, "run_adb", side_effect=adb_side_effect))
        size_mock = stack.enter_context(
            patch.object(device_layer, "read_display_size", side_effect=FIXTURE_DISPLAY_SIZES)
        )
        stack.enter_context(patch.object(device_layer, "read_screen_on", side_effect=read_screen_on))
        stack.enter_context(patch.object(device_layer, "sleep_screen", side_effect=sleep_screen))
        stack.enter_context(patch.object(device_layer, "wake_screen", side_effect=wake_screen))
        stack.enter_context(patch.object(device_layer, "read_wallpaper_visible", return_value=False))
        stack.enter_context(patch.object(device_layer, "read_night_mode", return_value="auto"))
        stack.enter_context(patch.object(device_layer, "read_keyguard_locked", return_value=False))
        stack.enter_context(patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague"))
        pid_mock = stack.enter_context(
            patch.object(device_layer, "get_wallpaper_pid", side_effect=FIXTURE_WALLPAPER_PIDS)
        )
        stack.enter_context(patch.object(device_layer, "read_process_cpu_ticks", return_value=100))
        stack.enter_context(patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100))
        stack.enter_context(patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]))
        stack.enter_context(patch.object(device_layer, "send_debug_clock_broadcast", return_value=True))
        stack.enter_context(patch.object(qualification, "restore_device", return_value=True))
        stack.enter_context(patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))))
        stack.enter_context(patch.object(device_layer, "is_dark_palette", return_value=False))
        hand_mock = stack.enter_context(
            patch.object(device_layer, "detect_hand_angle", side_effect=FIXTURE_HAND_ANGLES)
        )
        stack.enter_context(patch.object(qualification.time, "sleep"))
        # The hidden producer window has its own tests; this fixture exists to make every *other*
        # phase succeed without also having to satisfy a trace console, and its pid/probe side
        # effects are consumed in a fixed order that another live phase would exhaust.
        stack.enter_context(patch.object(qualification, "phase_hidden_frames"))
        return hand_mock, pid_mock, size_mock

    def test_log_collection_failure_makes_the_whole_run_exit_nonzero(self) -> None:
        """
        Fail the whole run when the log scan cannot be collected.

        The reported defect: Phase 8 sits after the try/finally, so a collection error there was
        reported as an inconclusive pass and the run exited 0 even though no log was ever read.
        Every other phase is made to succeed, so the exit code turns on the log scan alone.
        """
        with contextlib.ExitStack() as stack:
            hand_mock, pid_mock, size_mock = self._mock_every_phase_succeeding(stack)
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            error_output = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            with self.assertRaises(SystemExit) as exit_error:
                qualification.main()

        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("Renderer log scan could not be collected", error_output.getvalue())
        # Exactly one check fails, and it is the log scan. This is what makes the docstring's claim
        # true: a fixture that drifts a phase into failing reports a second "- FAIL:" line here, so the
        # run cannot pass on the exit code while a phase it claims succeeds quietly does not.
        self.assertEqual(error_output.getvalue().count("FAIL:"), 1)
        # No phase failed, but the claim is that every other phase succeeded, so each must also have
        # published its own result row: the failure count alone would still read 1 if a phase returned
        # early without appending either a result or a failure.
        for check in (
            "visible dial baseline",
            "screen-off / wake navigation",
            "preview navigation",
            "surface recreation",
            "process rebind",
            "time travel (+30m, +12h)",
            "midnight date rollover",
            "total PSS sample",
        ):
            with self.subTest(check=check):
                self.assertIn(check, output.getvalue())
        # The hand and display-size reads are fixed in number, so comparing their call counts with
        # their fixture lengths catches both an over-long fixture (whose trailing values would go
        # unread) and an under-long one (which raises StopIteration out of main()).
        self.assertEqual(hand_mock.call_count, len(FIXTURE_HAND_ANGLES))
        self.assertEqual(size_mock.call_count, len(FIXTURE_DISPLAY_SIZES))
        # The rebind poll loop re-reads the PID until it changes, so its read count grows with the
        # fixture instead of pinning it, and comparing against len(FIXTURE_WALLPAPER_PIDS) would hold
        # for an extra leading START absorbed as one more poll. Pin the literal seven reads — the
        # baseline, the three screen-off reads, the rebind's pre-kill read and its two polls — and the
        # fixture length, so drift in either fails here.
        self.assertEqual(pid_mock.call_count, 7)
        self.assertEqual(len(FIXTURE_WALLPAPER_PIDS), 7)


class PaletteProbeTest(unittest.TestCase):
    "The civil-scale rim probe that classifies the visible palette."

    def test_rim_probe_reach_stays_clear_of_the_numeral_glyphs_and_the_rim_stroke(self) -> None:
        """
        Keep every probe inside the civil-scale annulus, the one band filled with palette.rim.

        Radius is converted back to DialRenderer's normalized units, where the numeral glyphs reach
        1.254 and the fine muted-gold stroke sits at 1.344.
        """
        scale = 0.43 * min(FRAME_WIDTH, FRAME_HEIGHT) / 1.37
        points = list(device_layer.rim_probe_points(FRAME_WIDTH, FRAME_HEIGHT))
        self.assertEqual(len(points), len(device_layer.RIM_PROBE_BEARINGS_DEG))
        for x, y in points:
            normalized = math.hypot(x - FRAME_WIDTH / 2.0, y - FRAME_HEIGHT / 2.0) / scale
            self.assertGreater(normalized, NUMERAL_GLYPH_OUTER_RADIUS)
            self.assertLess(normalized, RIM_INSET_STROKE_RADIUS)

    def test_dark_rim_annulus_reads_as_dark(self) -> None:
        pixels = palette_frame(device_layer.DARK_RIM_RGB, [])
        self.assertTrue(device_layer.is_dark_palette(FRAME_WIDTH, FRAME_HEIGHT, pixels))

    def test_light_rim_annulus_reads_as_light(self) -> None:
        pixels = palette_frame(device_layer.LIGHT_RIM_RGB, [])
        self.assertFalse(device_layer.is_dark_palette(FRAME_WIDTH, FRAME_HEIGHT, pixels))

    def test_gold_pixel_at_the_previous_probe_point_does_not_read_as_light(self) -> None:
        """
        Keep a gold numeral pixel from flipping a dark frame to light.

        The reported defect: the old probe sat on the XII numeral, which is palette.gold, so a dark
        frame could be classified light and detect_hand_angle then searched for LIGHT_HAND. The
        frame here is dark rim everywhere, with the gold glyph at exactly the point it sampled.
        """
        old_probe = (
            FRAME_WIDTH // 2,
            int(FRAME_HEIGHT / 2 - 0.38 * min(FRAME_WIDTH, FRAME_HEIGHT)),
        )
        pixels = palette_frame(device_layer.DARK_RIM_RGB, [old_probe])
        self.assertTrue(device_layer.is_dark_palette(FRAME_WIDTH, FRAME_HEIGHT, pixels))

    def test_modal_sample_ignores_a_single_gold_probe_hit(self) -> None:
        """Keep one intruder over a probe from changing the modal rim colour."""
        points = list(device_layer.rim_probe_points(FRAME_WIDTH, FRAME_HEIGHT))
        self.assertGreater(len(points), MIN_PROBE_POINTS)
        pixels = palette_frame(device_layer.DARK_RIM_RGB, points[:1])
        self.assertTrue(device_layer.is_dark_palette(FRAME_WIDTH, FRAME_HEIGHT, pixels))

    def test_nearest_rim_literal_reproduces_both_pinned_tones(self) -> None:
        self.assertEqual(device_layer.nearest_rim_rgb(device_layer.DARK_RIM_RGB), device_layer.DARK_RIM_RGB)
        self.assertEqual(device_layer.nearest_rim_rgb(device_layer.LIGHT_RIM_RGB), device_layer.LIGHT_RIM_RGB)

    def test_nearest_rim_literal_resolves_an_intermediate_colour(self) -> None:
        # A colour between the two rim tones is classified by distance, not by an RGB-sum threshold:
        # the midpoint of the pair is nearer the dark literal, and a light grey is nearer the light one.
        dark = device_layer.DARK_RIM_RGB
        light = device_layer.LIGHT_RIM_RGB
        midpoint = ((dark[0] + light[0]) // 2, (dark[1] + light[1]) // 2, (dark[2] + light[2]) // 2)
        self.assertEqual(device_layer.nearest_rim_rgb(midpoint), device_layer.DARK_RIM_RGB)
        self.assertEqual(device_layer.nearest_rim_rgb((200, 200, 200)), device_layer.LIGHT_RIM_RGB)

    @patch.object(device_layer, "DARK_RIM_RGB", (0, 0, 0))
    @patch.object(device_layer, "LIGHT_RIM_RGB", (2, 2, 2))
    def test_nearest_rim_literal_breaks_a_tie_towards_the_dark_tone(self) -> None:
        self.assertEqual(device_layer.nearest_rim_rgb((1, 1, 1)), (0, 0, 0))


class SharedDeviceLayerTest(unittest.TestCase):
    """The tunables shared by the two harnesses live only in device_layer."""

    def test_shared_constants_live_in_the_shared_layer(self) -> None:
        for name in DUPLICATED_CONSTANT_NAMES:
            with self.subTest(constant=name):
                self.assertTrue(hasattr(device_layer, name), f"device_layer lacks {name}")

    def test_harnesses_do_not_redefine_the_shared_constants(self) -> None:
        for harness in (qualification, device_smoke):
            for name in DUPLICATED_CONSTANT_NAMES:
                with self.subTest(harness=harness.__name__, constant=name):
                    self.assertFalse(hasattr(harness, name), f"{harness.__name__} shadows {name}")


class WallpaperComponentParsingTest(unittest.TestCase):
    """The arm-critical `dumpsys wallpaper` read, parsed directly rather than through a mock."""

    # The read's expected values are the identifiers the harness itself names — `SERVICE_NAME` and
    # `PLATFORM_STATIC_WALLPAPER` — while the dump prints each of them inside a `ComponentInfo{}`
    # wrapper. The two must be equal for an arm check to compare a reading against a named component
    # at all, so the parser strips the wrapper and these fixtures keep both forms apart on purpose.
    LIVE = device_layer.SERVICE_NAME
    LOCK = "com.example.lock/com.example.LockWallpaper"
    STATIC = device_layer.PLATFORM_STATIC_WALLPAPER

    def wrapped(self, component: str) -> str:
        """Return a component as `dumpsys wallpaper` prints it, inside the `ComponentInfo{}` wrapper."""
        return f"ComponentInfo{{{component}}}"

    def read(self, output: str) -> str:
        """Parse a wallpaper dump through the real parser."""
        with patch.object(device_layer, "run_adb", return_value=output.encode()):
            return device_layer.read_wallpaper_component("device")

    def test_the_component_wrapper_is_stripped(self) -> None:
        # The normalization the arm check rests on, and the one case it must not admit: a wrapper with
        # nothing inside it names no component, so it normalizes to the empty string the parser skips
        # rather than to a component with no name in it.
        cases = (
            ("ComponentInfo{com.example.app/.Main}", "com.example.app/.Main"),
            ("com.example.app/.Main", "com.example.app/.Main"),
            ("  ComponentInfo{com.example.app/.Main}  ", "com.example.app/.Main"),
            ("ComponentInfo{}", ""),
            ("", ""),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, device_layer.component_identifier(value))

    def test_the_device_forwards_the_readback(self) -> None:
        # The follow-on arm check reads through `AdbDevice` as every other probe in the harness does,
        # so the parser is reachable as a method and not only as a module function.
        dump = f"  mLastWallpaper state:\n    mInfo.component={self.wrapped(self.LIVE)}\n"
        with patch.object(device_layer, "run_adb", return_value=dump.encode()):
            self.assertEqual(self.LIVE, device_layer.AdbDevice("device").read_wallpaper_component())

    def test_the_live_home_component_is_returned(self) -> None:
        dump = (
            "System wallpaper state:\n"
            "  mLastWallpaper state:\n"
            "    mName=null\n"
            "    mWallpaperId=57\n"
            f"    mInfo.component={self.wrapped(self.LIVE)}\n"
            "  mLastLockWallpaper state:\n"
            f"    mInfo.component={self.wrapped(self.LOCK)}\n"
        )
        self.assertEqual(self.LIVE, self.read(dump))

    def test_the_lock_section_is_not_read_through(self) -> None:
        # The lock slot reports the same `mInfo.component=` field a few lines below the home one. A
        # parser that read past the boundary would report the lock wallpaper as the home component,
        # which would admit a window against an arm the device is not in.
        dump = (
            "System wallpaper state:\n"
            "  mLastWallpaper state:\n"
            "    mName=ImageWallpaper\n"
            "    mWallpaperId=57\n"
            "  mLastLockWallpaper state:\n"
            f"    mInfo.component={self.wrapped(self.LOCK)}\n"
            "Wallpaper per-file components:\n"
            "  Wallpaper #0:\n"
            f"    mWallpaperComponent={self.wrapped(self.STATIC)}\n"
        )
        self.assertEqual(self.STATIC, self.read(dump))

    def test_the_home_scan_stops_at_the_per_file_components(self) -> None:
        # With no lock section to bound it the home scan would run to the end of the dump, where a
        # later `mInfo.component=` — another slot's, or a reshaped per-file entry's — would be
        # reported as the home component and admit a window against an arm the device is not in. The
        # per-file header bounds it the way the lock header does, so the static fallback still wins.
        dump = (
            "System wallpaper state:\n"
            "  mLastWallpaper state:\n"
            "    mName=ImageWallpaper\n"
            "Wallpaper per-file components:\n"
            "  Wallpaper #0:\n"
            f"    mWallpaperComponent={self.wrapped(self.STATIC)}\n"
            "  Wallpaper #1:\n"
            f"    mInfo.component={self.wrapped(self.LOCK)}\n"
        )
        self.assertEqual(self.STATIC, self.read(dump))

    def test_the_static_fallback_reports_the_first_per_file_component(self) -> None:
        # With nothing live bound the home section carries no component, and the per-file list is what
        # names the static wallpaper a baseline arm is recorded against.
        dump = (
            "System wallpaper state:\n"
            "  mLastWallpaper state:\n"
            "    mName=ImageWallpaper\n"
            "Wallpaper per-file components:\n"
            "  Wallpaper #0:\n"
            f"    mWallpaperComponent={self.wrapped(self.STATIC)}\n"
            "  Wallpaper #1:\n"
            f"    mWallpaperComponent={self.wrapped(self.LOCK)}\n"
        )
        self.assertEqual(self.STATIC, self.read(dump))

    def test_a_dump_naming_no_component_is_refused(self) -> None:
        for dump in (
            "",
            "System wallpaper state:\n  mLastWallpaper state:\n    mName=null\n",
            "Wallpaper per-file components:\n  Wallpaper #0:\n    mWallpaperId=57\n",
        ):
            with self.subTest(dump=dump[:30]), self.assertRaises(device_layer.ProbeError):
                self.read(dump)

    def test_a_component_field_with_no_value_is_refused(self) -> None:
        # A truncated or reshaped dump can carry the field with nothing after the `=`. Returning that
        # empty string reads as a successful parse, and since `component_is_live("")` is false, a
        # baseline window would be recorded as valid against no component at all: an unreadable input
        # standing in for a reading. With nothing usable anywhere, the read is refused.
        dump = (
            "System wallpaper state:\n"
            "  mLastWallpaper state:\n"
            "    mName=null\n"
            "    mInfo.component=\n"
            "Wallpaper per-file components:\n"
            "  Wallpaper #0:\n"
            "    mWallpaperComponent=\n"
        )
        with self.assertRaises(device_layer.ProbeError):
            self.read(dump)

    def test_an_empty_home_value_falls_through_to_the_per_file_component(self) -> None:
        # With the home slot carrying no value, the names that remain are the per-file ones, which is
        # what the fallback exists for.
        dump = (
            "System wallpaper state:\n"
            "  mLastWallpaper state:\n"
            "    mInfo.component=\n"
            "Wallpaper per-file components:\n"
            "  Wallpaper #0:\n"
            f"    mWallpaperComponent={self.wrapped(self.STATIC)}\n"
        )
        self.assertEqual(self.STATIC, self.read(dump))


class ProducerTraceTest(unittest.TestCase):
    """Checks the producer-window parsing and the zero-versus-unread distinction it exists for."""

    # Mark pairs in the shape this device emits them: the name is on the begin half and the end
    # half is nameless, with a decoy from another process and an untracked event name alongside.
    TRACE = """# tracer: nop
     wallpaper-1234  [001] d..2 120.000000: tracing_mark_write: B|1234|dequeueBuffer
     wallpaper-1234  [001] d..2 120.010000: tracing_mark_write: E|1234
     wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer
     wallpaper-1234  [001] d..2 120.030000: tracing_mark_write: E|1234
     SurfaceFlinger-999  [000] d..2 120.040000: tracing_mark_write: B|999|queueBuffer
     SurfaceFlinger-999  [000] d..2 120.050000: tracing_mark_write: E|999
     wallpaper-1234  [001] d..2 121.020000: tracing_mark_write: B|1234|onFrameCommitted
     wallpaper-1234  [001] d..2 121.030000: tracing_mark_write: E|1234
"""

    def test_counts_the_traced_process_buffer_events(self) -> None:
        counts = device_layer.parse_producer_events(self.TRACE, 1234)
        self.assertEqual({"dequeueBuffer": 1, "queueBuffer": 1}, counts)

    def test_ignores_events_from_another_process(self) -> None:
        self.assertEqual({}, device_layer.parse_producer_events(self.TRACE, 42))

    def test_an_empty_trace_has_no_events(self) -> None:
        self.assertEqual({}, device_layer.parse_producer_events("", 1234))

    def test_a_window_with_frames_reports_them(self) -> None:
        window = device_layer.ProducerWindow(
            window_seconds=30.0,
            pid=1234,
            event_counts={"queueBuffer": 30, "dequeueBuffer": 30},
            trace_lines=60,
            display_states=(True, True),
        )
        self.assertEqual(30, window.frame_count)
        self.assertTrue(window.display_on_at_every_sample)
        self.assertFalse(window.display_off_at_every_sample)

    def test_an_empty_event_map_is_a_measured_zero_not_a_missing_key(self) -> None:
        window = device_layer.ProducerWindow(
            window_seconds=60.0, pid=1234, event_counts={}, trace_lines=3, display_states=(False, False, False)
        )
        self.assertEqual(0, window.frame_count)
        self.assertTrue(window.display_off_at_every_sample)

    def test_a_window_with_no_display_samples_is_neither_on_nor_off(self) -> None:
        window = device_layer.ProducerWindow(
            window_seconds=60.0, pid=1234, event_counts={}, trace_lines=3, display_states=()
        )
        self.assertFalse(window.display_off_at_every_sample)
        self.assertFalse(window.display_on_at_every_sample)

    def test_an_unreadable_display_sample_is_counted_as_unknown(self) -> None:
        window = device_layer.ProducerWindow(
            window_seconds=60.0, pid=1234, event_counts={}, trace_lines=3, display_states=(False, None, False)
        )
        self.assertEqual(1, window.display_unknown_samples)
        self.assertFalse(window.display_off_at_every_sample)

    def test_a_zero_sampling_interval_is_refused_before_the_trace_starts(self) -> None:
        # A zero interval would make the sampling loop spin forever with the trace already running;
        # it is refused by the duration floor, which is also the interval the read cap is derived
        # from.
        with (
            patch.object(device_layer, "run_adb") as run_adb,
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=10.0, sample_seconds=0.0)
        run_adb.assert_not_called()

    def test_a_non_positive_window_is_refused(self) -> None:
        # A zero window is below the duration floor, so it never reaches the trace.
        with (
            patch.object(device_layer, "run_adb") as run_adb,
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=0.0, sample_seconds=1.0)
        run_adb.assert_not_called()

    def test_non_finite_durations_are_refused_before_the_trace_starts(self) -> None:
        # `nan <= 0` and `inf <= 0` are both false, so a positivity-only guard lets them through:
        # `inf` makes the sampling loop never terminate with the trace running, and `nan` skips the
        # loop and returns a zero-frame window with no display samples. The duration range excludes
        # them as well, and names them so the intent is not left to comparison semantics.
        for seconds, sample_seconds in (
            (float("inf"), 1.0),
            (float("nan"), 1.0),
            (10.0, float("inf")),
            (10.0, float("nan")),
        ):
            with (
                self.subTest(seconds=seconds, sample_seconds=sample_seconds),
                patch.object(device_layer, "run_adb") as run_adb,
                self.assertRaises(device_layer.ProbeError),
            ):
                device_layer.capture_producer_window("device", 1234, seconds=seconds, sample_seconds=sample_seconds)
            run_adb.assert_not_called()

    def test_a_window_longer_than_the_cap_is_refused_before_the_trace_starts(self) -> None:
        # Finiteness and positivity are both satisfied by 1e17, but `remaining -= interval` is a
        # no-op in float64 at that magnitude, so the accumulating loop would never terminate once
        # `atrace` was running. The duration range excludes it before the trace starts.
        with (
            patch.object(device_layer, "run_adb") as run_adb,
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=1e17, sample_seconds=2.0)
        run_adb.assert_not_called()

    def test_a_window_sampled_too_finely_is_refused_before_the_trace_starts(self) -> None:
        # 0.01 s is below the duration floor, and the floor is what keeps the read count within
        # MAX_TRACE_SAMPLES: a 600 s window at the smallest accepted interval is exactly the cap.
        with (
            patch.object(device_layer, "run_adb") as run_adb,
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=600.0, sample_seconds=0.01)
        run_adb.assert_not_called()

    def test_a_subnormal_sampling_interval_is_refused_before_the_trace_starts(self) -> None:
        # 5e-324 is finite and positive, so the finiteness/positivity guard let it through, and
        # 600.0 / 5e-324 overflows to inf, which `math.ceil` raises OverflowError for. That is an
        # ArithmeticError, which the caller does not catch, so it crashed the whole run rather than
        # failing the probe. The duration floor refuses it before the trace starts.
        with (
            patch.object(device_layer, "run_adb") as run_adb,
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=600.0, sample_seconds=5e-324)
        run_adb.assert_not_called()

    def test_a_window_and_interval_that_underflow_the_read_count_are_refused_before_the_trace_starts(self) -> None:
        # The reciprocal extreme: 1e-300 / 1e300 underflows to 0.0, so `range(0)` took no display
        # reads at all while `atrace` ran and the window came back with `display_states=()` — the
        # fabricated "unreadable" result this probe exists to prevent. Both values are outside the
        # duration range, so the pair is refused before the trace starts.
        with (
            patch.object(device_layer, "run_adb") as run_adb,
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=1e-300, sample_seconds=1e300)
        run_adb.assert_not_called()

    def sample_window(self, seconds: float, sample_seconds: float) -> tuple[int, int]:
        """Capture a window with the trace and the clock stubbed, returning (sleeps, samples)."""
        trace = b"wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer"

        def run(command: list[str], **_kwargs: object) -> bytes:
            return trace if "--async_dump" in command else b""

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer.time, "sleep") as sleep,
        ):
            window = device_layer.capture_producer_window(
                "device", 1234, seconds=seconds, sample_seconds=sample_seconds
            )
        return sleep.call_count, len(window.display_states)

    def test_the_window_cap_is_a_bound_and_the_reads_are_counted(self) -> None:
        # The cap is inclusive: a window at exactly the maximum is accepted, and the loop sleeps
        # exactly ceil(seconds / sample_seconds) times, a count fixed before the trace starts.
        expected = math.ceil(device_layer.MAX_TRACE_WINDOW_SECONDS / device_layer.TRACE_DISPLAY_SAMPLE_SECONDS)
        self.assertEqual(
            (expected, expected),
            self.sample_window(device_layer.MAX_TRACE_WINDOW_SECONDS, device_layer.TRACE_DISPLAY_SAMPLE_SECONDS),
        )

    def test_the_duration_range_corners_are_inclusive(self) -> None:
        # The floor and the ceiling are both accepted, and the read count over the whole range stays
        # in [1, MAX_TRACE_SAMPLES]: the smallest accepted interval against the longest accepted
        # window is exactly the cap, and the shortest window still takes one read.
        self.assertEqual(
            (device_layer.MAX_TRACE_SAMPLES, device_layer.MAX_TRACE_SAMPLES),
            self.sample_window(device_layer.MAX_TRACE_WINDOW_SECONDS, device_layer.MIN_TRACE_DURATION_SECONDS),
        )
        minimum = device_layer.MIN_TRACE_DURATION_SECONDS
        for seconds, sample_seconds in ((minimum, minimum), (minimum, device_layer.MAX_TRACE_WINDOW_SECONDS)):
            with self.subTest(seconds=seconds, sample_seconds=sample_seconds):
                self.assertEqual((1, 1), self.sample_window(seconds, sample_seconds))

    def test_the_window_reports_the_span_it_traced_and_paces_the_reads_to_the_deadline(self) -> None:
        # The display reads and the dump run inside the trace, so the window the frames describe is
        # always wider than the duration requested, and reporting the request would advertise a
        # narrower window than was traced. Each read here costs 0.5 s: pacing against a deadline
        # fixed at the trace's start spends that latency out of the following sleep, so the last read
        # still lands at 10 s and the window spans 10.5 s. Sleeping a flat interval instead would
        # have pushed every later read out by its latency and spanned 12.5 s, which is the drift the
        # deadline removes; either way the reported value is the measured span.
        trace = b"wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer"
        clock = [1000.0]
        read_latency = 0.5

        def run(command: list[str], **_kwargs: object) -> bytes:
            return trace if "--async_dump" in command else b""

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        def read_screen_on(_serial: str) -> bool:
            clock[0] += read_latency
            return True

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer.time, "monotonic", monotonic),
            patch.object(device_layer.time, "sleep", sleep),
            patch.object(device_layer, "read_screen_on", read_screen_on),
        ):
            window = device_layer.capture_producer_window("device", 1234, seconds=10.0, sample_seconds=2.0)
        self.assertEqual(1, window.frame_count)
        self.assertEqual(5, len(window.display_states))
        self.assertAlmostEqual(10.0 + read_latency, window.window_seconds)

    def test_the_reads_are_anchored_to_the_trace_start_not_to_the_start_call(self) -> None:
        # The trace is already active while `atrace --async_start` is in flight, so anchoring the
        # schedule when that call returns would leave its latency outside the window the samples
        # describe — and outside the span reported below, which is meant to be that window. The clock
        # is read before the call, so the reads land on the trace's own start.
        reads: list[float] = []
        clock = [1000.0]
        start_latency = 0.25

        def run(command: list[str], **_kwargs: object) -> bytes:
            if "--async_start" in command:
                clock[0] += start_latency
                return b""
            if "--async_dump" in command:
                return b"wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer"
            return b""

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        def read_screen_on(_serial: str) -> bool:
            reads.append(clock[0])
            return True

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer.time, "monotonic", monotonic),
            patch.object(device_layer.time, "sleep", sleep),
            patch.object(device_layer, "read_screen_on", read_screen_on),
        ):
            window = device_layer.capture_producer_window("device", 1234, seconds=10.0, sample_seconds=2.0)
        # Five reads at two-second offsets from the trace's start, not from the moment the start call
        # returned 0.25 s later.
        self.assertEqual([1002.0, 1004.0, 1006.0, 1008.0, 1010.0], reads)
        self.assertAlmostEqual(10.0, window.window_seconds)

    def test_the_final_read_is_taken_when_it_lands_on_the_window_end(self) -> None:
        # The last read is scheduled at the window's own end, and on a real clock a sleep that
        # overshoots by anything at all lands past it. Refusing a read there would refuse the sample
        # that closes the window, so only the reads before it are refused for starting after the end:
        # the final one is taken, and the window can overrun by that one read at most.
        trace = b"wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer"
        clock = [1000.0]
        read_latency = 2.0

        def run(command: list[str], **_kwargs: object) -> bytes:
            return trace if "--async_dump" in command else b""

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        def read_screen_on(_serial: str) -> bool:
            clock[0] += read_latency
            return True

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer.time, "monotonic", monotonic),
            patch.object(device_layer.time, "sleep", sleep),
            patch.object(device_layer, "read_screen_on", read_screen_on),
        ):
            window = device_layer.capture_producer_window("device", 1234, seconds=10.0, sample_seconds=2.0)
        self.assertEqual(5, len(window.display_states))

    def test_a_read_that_outruns_the_window_refuses_rather_than_extending_it(self) -> None:
        # The read count bounds how many reads are owed, not how long they may take: a read spends up
        # to two adb timeouts, so a slow transport could hold atrace open for a multiple of the
        # window's width — at the (600 s, 0.1 s) corner, many hours. A read that would start after the
        # window's own end is refused instead of taken, so the device is held for the window rather
        # than for the window plus everything the reads overran.
        trace = b"wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer"
        clock = [1000.0]
        read_latency = 3.0

        def run(command: list[str], **_kwargs: object) -> bytes:
            return trace if "--async_dump" in command else b""

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        def read_screen_on(_serial: str) -> bool:
            clock[0] += read_latency
            return True

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer.time, "monotonic", monotonic),
            patch.object(device_layer.time, "sleep", sleep),
            patch.object(device_layer, "read_screen_on", read_screen_on),
            self.assertRaises(device_layer.ProbeError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=10.0, sample_seconds=2.0)
        # It stopped just past the window instead of taking the two reads still owed to it, which at
        # 3 s each would have run to 17 s.
        self.assertGreater(clock[0] - 1000.0, 10.0)
        self.assertLessEqual(clock[0] - 1000.0, 10.0 + read_latency)

    def test_an_uncapturable_trace_is_an_error(self) -> None:
        with (
            patch.object(device_layer, "run_adb", side_effect=subprocess.CalledProcessError(1, "atrace")),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=1.0, sample_seconds=1.0)

    def test_a_trace_with_no_mark_lines_is_refused_rather_than_read_as_zero(self) -> None:
        # The guard is on the buffer, not on the pid: a window in which the process drew nothing
        # legitimately holds no marks of its own, while this one holds none from any process, so the
        # tracer captured nothing to count. Reading a zero off it would be reading an unread counter,
        # which is the distinction the phase's controls rest on. Driven through the real function
        # rather than an injected message, so the "an unusable trace raises, it is never a zero"
        # claim is pinned to the code that makes it.
        with (
            patch.object(device_layer, "run_adb", return_value=b"# tracer: nop\n"),
            patch.object(device_layer, "read_screen_on", return_value=False),
            patch.object(device_layer.time, "sleep"),
            self.assertRaises(device_layer.ProbeError) as raised,
        ):
            device_layer.capture_producer_window("device", 1234, seconds=1.0, sample_seconds=1.0)
        # "at all" is the claim the check supports: it says nothing about what this pid drew.
        self.assertIn("no mark lines at all", str(raised.exception))

    def test_a_stop_failure_says_the_trace_may_still_be_running(self) -> None:
        # A read overruns the window and the same stalled transport then fails the stop, so the
        # failure the caller records names the stop. The stop's error cannot say on its own that a
        # trace may still be filling a buffer on the shared device, so that is said out loud before
        # it is raised.
        stop_error = subprocess.TimeoutExpired(["adb", "shell", "atrace", "--async_stop"], 120)
        clock = [1000.0]

        def run(command: list[str], **_kwargs: object) -> bytes:
            if "--async_stop" in command:
                raise stop_error
            return b""

        def monotonic() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            clock[0] += seconds

        def read_screen_on(_serial: str) -> bool:
            clock[0] += 3.0
            return True

        stderr = io.StringIO()
        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer.time, "monotonic", monotonic),
            patch.object(device_layer.time, "sleep", sleep),
            patch.object(device_layer, "read_screen_on", read_screen_on),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=10.0, sample_seconds=2.0)
        self.assertIn("atrace --async_stop failed and a trace may still be active", stderr.getvalue())

    def test_a_stop_failure_with_no_body_failure_is_the_window_failure(self) -> None:
        # Nothing in the body went wrong, so a stop that failed is the whole reason the window cannot
        # be read: an unstopped trace keeps filling the buffer the next window's dump reads, which
        # would attribute this window's frames to that one. It fails rather than warning.
        stop_error = subprocess.TimeoutExpired(["adb", "shell", "atrace", "--async_stop"], 120)
        trace = b"wallpaper-1234  [001] d..2 120.020000: tracing_mark_write: B|1234|queueBuffer"

        def run(command: list[str], **_kwargs: object) -> bytes:
            if "--async_stop" in command:
                raise stop_error
            return trace if "--async_dump" in command else b""

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            patch.object(device_layer, "read_screen_on", return_value=True),
            patch.object(device_layer.time, "sleep"),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=1.0, sample_seconds=1.0)

    def test_a_start_that_raises_still_stops_the_trace(self) -> None:
        # The start is what leaves atrace running, so it belongs inside the cleanup guard: a start
        # that raises after the device has begun tracing would otherwise strand an active trace on a
        # shared device with nothing to stop it, which is what a bounded window says cannot happen.
        # Its timeout is the trace timeout the dump and stop already use, not the shorter default the
        # untimed probe calls take.
        calls: list[tuple[list[str], dict[str, object]]] = []

        def run(command: list[str], **kwargs: object) -> bytes:
            calls.append((command, kwargs))
            raise subprocess.TimeoutExpired(command, device_layer.ADB_TRACE_TIMEOUT_SECONDS)

        with (
            patch.object(device_layer, "run_adb", side_effect=run),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            device_layer.capture_producer_window("device", 1234, seconds=1.0, sample_seconds=1.0)
        started = [(command, kwargs) for command, kwargs in calls if "--async_start" in command]
        stopped = [command for command, _ in calls if "--async_stop" in command]
        self.assertEqual(1, len(started))
        self.assertEqual(1, len(stopped))
        self.assertEqual(device_layer.ADB_TRACE_TIMEOUT_SECONDS, started[0][1]["timeout"])


class HiddenFramesPhaseTest(unittest.TestCase):
    """Checks the hidden-window phase's accept, refuse, and tracer-validation branches."""

    def window(self, frames: int, states: tuple[bool | None, ...]) -> device_layer.ProducerWindow:
        counts = {"queueBuffer": frames} if frames else {}
        return device_layer.ProducerWindow(
            window_seconds=20.0, pid=123, event_counts=counts, trace_lines=40, display_states=states
        )

    def stub_device(self) -> MagicMock:
        """Return a device whose phase probes all succeed, for a test to override the one it checks."""
        device = MagicMock()
        device.get_wallpaper_pid.return_value = 123
        device.sleep_screen.return_value = True
        device.read_screen_on.return_value = False
        device.read_wallpaper_visible.return_value = False
        device.wake_screen.return_value = True
        device.capture_producer_window.side_effect = [
            self.window(20, (True, True)),
            self.window(0, (False, False)),
            self.window(20, (True, True)),
        ]
        return device

    def run_with(self, device: MagicMock) -> tuple[list[tuple[str, str]], list[str]]:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_hidden_frames(device, results, failures)
        return results, failures

    def run_phase(self, windows: list[object]) -> tuple[list[tuple[str, str]], list[str]]:
        device = self.stub_device()
        device.capture_producer_window.side_effect = windows
        return self.run_with(device)

    def test_zero_frames_with_working_controls_pass(self) -> None:
        results, failures = self.run_phase(
            [self.window(20, (True, True)), self.window(0, (False, False, False)), self.window(20, (True, True))]
        )
        self.assertEqual([], failures)
        self.assertEqual(3, len(results))

    def test_frames_in_the_hidden_window_fail_the_budget(self) -> None:
        _, failures = self.run_phase(
            [self.window(20, (True, True)), self.window(5, (False, False)), self.window(20, (True, True))]
        )
        self.assertTrue(any("5 producer frames were rendered" in failure for failure in failures))

    def test_a_blind_control_refuses_instead_of_recording_a_zero(self) -> None:
        results, failures = self.run_phase([self.window(0, (True, True))])
        # The blind control is recorded as evidence that it was run and found nothing, and the
        # hidden window is never traced at all, so no zero can be read as a hidden-frame result.
        self.assertEqual(1, len(results))
        self.assertIn("visible control", results[0][0])
        self.assertTrue(any("unread counter rather than a measured zero" in failure for failure in failures))

    def test_a_control_whose_display_evidence_is_invalid_skips_the_hidden_window(self) -> None:
        # The control is the phase's only check that the display reader reports on when lit: the
        # gate before the hidden window only confirms it reports off after the sleep, which a
        # reader stuck on False also satisfies. So a control whose samples are off or unreadable
        # has not validated the reading the hidden window's claim rests on, and — like the blind
        # control — it refuses before the screen is slept, so nothing else is traced and no hidden
        # zero is recorded inside a bracket just declared invalid.
        for states in ((True, False), (True, None)):
            with self.subTest(states=states):
                results: list[tuple[str, str]] = []
                failures: list[str] = []
                device = MagicMock()
                device.get_wallpaper_pid.return_value = 123
                device.sleep_screen.return_value = True
                device.read_screen_on.return_value = False
                device.read_wallpaper_visible.return_value = False
                device.wake_screen.return_value = True
                device.capture_producer_window.side_effect = [
                    self.window(20, states),
                    self.window(0, (False, False)),
                    self.window(20, (True, True)),
                ]
                with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                    qualification.phase_hidden_frames(device, results, failures)
                # The opening control alone is traced and recorded; the hidden window and the
                # closing control are both skipped, so the fixture's later windows go unused.
                self.assertEqual(1, device.capture_producer_window.call_count)
                self.assertEqual(1, len(results))
                self.assertIn("visible control", results[0][0])
                self.assertTrue(any("display was not on for every sample" in failure for failure in failures))
                self.assertFalse(any("hidden" in label for label, _ in results))

    def test_a_failed_sleep_request_skips_the_hidden_window(self) -> None:
        # The sleep is what hides the engine, so a request that was not delivered leaves nothing for
        # the window to measure: the phase fails before it rather than tracing a window on a screen
        # it never put to sleep.
        device = self.stub_device()
        device.sleep_screen.return_value = False
        results, failures = self.run_with(device)
        self.assertEqual(1, device.capture_producer_window.call_count)
        self.assertEqual(1, len(results))
        self.assertTrue(any("Screen sleep request failed" in failure for failure in failures))
        self.assertFalse(any("hidden" in label for label, _ in results))

    def test_a_display_that_is_not_confirmed_off_skips_the_hidden_window(self) -> None:
        # `is not False` is the point of the gate: an unreadable display read is not a screen-off
        # window, and one traced on it would record a zero for a screen the phase never saw go off.
        for state in (None, True):
            with self.subTest(state=state):
                device = self.stub_device()
                device.read_screen_on.return_value = state
                results, failures = self.run_with(device)
                self.assertEqual(1, device.capture_producer_window.call_count)
                self.assertTrue(any("Display was not confirmed off" in failure for failure in failures))
                self.assertFalse(any("hidden" in label for label, _ in results))

    def test_an_engine_that_is_not_confirmed_hidden_abandons_the_window(self) -> None:
        # An unreadable probe is not a hidden engine, and a visible one is the state criterion 1
        # excludes: either way the window is abandoned and the device woken, and nothing is traced.
        for visible in (None, True):
            with self.subTest(visible=visible):
                device = self.stub_device()
                device.read_wallpaper_visible.return_value = visible
                results, failures = self.run_with(device)
                self.assertEqual(1, device.capture_producer_window.call_count)
                self.assertTrue(any("not confirmed hidden" in failure for failure in failures))
                self.assertFalse(any("hidden" in label for label, _ in results))
                device.wake_screen.assert_called_once_with()

    def test_an_unreadable_wallpaper_pid_skips_the_window(self) -> None:
        # No pid is no window: the phase records the missing process rather than tracing nothing and
        # letting an empty trace stand in for a measured zero.
        device = self.stub_device()
        device.get_wallpaper_pid.return_value = None
        results, failures = self.run_with(device)
        self.assertEqual(0, device.capture_producer_window.call_count)
        self.assertEqual([], results)
        self.assertTrue(any("No wallpaper process to trace" in failure for failure in failures))

    def test_a_wake_failure_after_the_hidden_window_skips_the_closing_control(self) -> None:
        # Tracing the closing control on a screen that never woke would fail the tracer for the
        # wake, so the phase reports the wake and traces nothing after the hidden window.
        device = self.stub_device()
        device.wake_screen.return_value = False
        results, failures = self.run_with(device)
        self.assertEqual(2, device.capture_producer_window.call_count)
        self.assertEqual(2, len(results))
        self.assertTrue(any("closing control trace was skipped" in failure for failure in failures))

    def test_the_summary_labels_a_window_by_the_samples_it_read(self) -> None:
        # "mixed" claims that an on sample and an off sample were both seen, so it is used only when
        # both were. A window whose every sample was unreadable observed neither state, and one whose
        # readable samples were all one way observed only that way; calling either of those mixed
        # would assert a contrast the reads never made.
        for states, expected in (
            ((True, True), "on at every readable sample"),
            ((False, False), "off at every readable sample"),
            ((True, False), "mixed"),
            ((False, None), "off at every readable sample"),
            ((True, None), "on at every readable sample"),
            ((None, None), "unreadable at every sample"),
        ):
            with self.subTest(states=states):
                self.assertIn(expected, qualification.producer_window_summary(self.window(0, states)))

    def test_a_restart_before_the_hidden_window_abandons_it_before_tracing(self) -> None:
        # `confirm_pid_unchanged` returns the fresh reading even when it does not match, and the trace
        # is filtered to the pid it is handed. Adopting a replacement here would trace the new process
        # and then compare the post-window reading against that same replacement, so the mismatch
        # would satisfy its own check and a zero for a process the opening control never bracketed
        # would be recorded. The window is therefore abandoned before anything is traced.
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        device = MagicMock()
        # The third reading is only reached without the fix, where the replacement pid is adopted and
        # the post-window check then reads it back to compare against itself.
        device.get_wallpaper_pid.side_effect = [123, 124, 124]
        device.sleep_screen.return_value = True
        device.read_screen_on.return_value = False
        device.read_wallpaper_visible.return_value = False
        device.wake_screen.return_value = True
        device.capture_producer_window.return_value = self.window(20, (True, True))
        with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_hidden_frames(device, results, failures)
        # Only the opening control was traced, and it is the only row: the hidden window and the
        # closing control are both skipped, so no zero is recorded for the replacement process.
        self.assertEqual(1, device.capture_producer_window.call_count)
        self.assertEqual(1, len(results))
        self.assertIn("visible control", results[0][0])
        self.assertFalse(any("hidden" in label for label, _ in results))
        self.assertTrue(any("before=123, after=124" in failure for failure in failures))
        # The failure says the window was abandoned, not that a measurement is unattributable: at
        # this point nothing has been measured, and the post-window check reports the same span in
        # words of its own so the two cannot read as one.
        self.assertTrue(
            any("abandoned rather than traced against a process it never bracketed" in failure for failure in failures)
        )
        self.assertFalse(any("is not attributable" in failure for failure in failures))
        device.wake_screen.assert_called_once_with()

    def test_a_process_that_restarted_inside_the_hidden_window_invalidates_the_zero(self) -> None:
        # The trace is filtered to one pid, so a restart inside the window would leave the
        # replacement's frames outside the trace and let the old pid's zero read as hidden-state
        # evidence. The pid is therefore reconfirmed after the window, as the CPU window already does.
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        device = MagicMock()
        # The fourth reading is the closing control's, taken after the wake.
        device.get_wallpaper_pid.side_effect = [123, 123, 124, 124]
        device.sleep_screen.return_value = True
        device.read_screen_on.return_value = False
        device.read_wallpaper_visible.return_value = False
        device.wake_screen.return_value = True
        device.capture_producer_window.side_effect = [
            self.window(20, (True, True)),
            self.window(0, (False, False)),
            self.window(20, (True, True)),
        ]
        with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_hidden_frames(device, results, failures)
        self.assertTrue(
            any(
                "was not confirmed unchanged across the hidden producer window" in failure
                and "before=123, after=124" in failure
                for failure in failures
            )
        )
        # The result is not merely failed: it is removed, because the trace was filtered to the old
        # pid and the zero describes a process that is no longer the one running. Only the two
        # visible controls remain, and neither row is the hidden window's.
        self.assertEqual(2, len(results))
        self.assertFalse(any("hidden" in label for label, _ in results))

    def test_the_hidden_row_is_kept_when_the_pid_held(self) -> None:
        # The suppression above must not swallow a good window: with the pid unchanged across the
        # hidden window, all three rows are recorded and the evaluation still runs.
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        device = MagicMock()
        device.get_wallpaper_pid.side_effect = [123, 123, 123, 123]
        device.sleep_screen.return_value = True
        device.read_screen_on.return_value = False
        device.read_wallpaper_visible.return_value = False
        device.wake_screen.return_value = True
        device.capture_producer_window.side_effect = [
            self.window(20, (True, True)),
            self.window(0, (False, False)),
            self.window(20, (True, True)),
        ]
        with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_hidden_frames(device, results, failures)
        self.assertEqual([], failures)
        self.assertEqual(3, len(results))
        self.assertTrue(any("hidden" in label for label, _ in results))

    def test_the_closing_control_runs_on_the_home_screen_after_the_wake(self) -> None:
        # Waking confirms only that the display is on, which on a locked device is the keyguard, not
        # the home screen the opening control ran on. The closing control has to bracket the hidden
        # window in the same context to be its counterpart, so the keyguard is dismissed and Home
        # shown before it is traced — the same repair `phase_screen_off_wake` makes after its wake.
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        device = MagicMock()
        device.get_wallpaper_pid.return_value = 123
        device.sleep_screen.return_value = True
        device.read_screen_on.return_value = False
        device.read_wallpaper_visible.return_value = False
        device.wake_screen.return_value = True
        device.capture_producer_window.side_effect = [
            self.window(20, (True, True)),
            self.window(0, (False, False)),
            self.window(20, (True, True)),
        ]
        with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_hidden_frames(device, results, failures)
        order = [
            name
            for name, _args, _kwargs in device.mock_calls
            if name in {"wake_screen", "dismiss_keyguard", "show_home", "capture_producer_window"}
        ]
        self.assertEqual(
            [
                "capture_producer_window",  # opening control, on the home screen
                "capture_producer_window",  # the hidden window itself
                "wake_screen",  # after the hidden window
                "dismiss_keyguard",
                "show_home",
                "capture_producer_window",  # closing control, in the opening control's context
            ],
            order,
        )
        self.assertEqual([], failures)

    def test_the_hidden_summary_is_printed_only_once_its_pid_check_has_held(self) -> None:
        # The abandoned window's row is removed from the results, but a summary printed before the pid
        # check could not be retracted: the unattributable zero would still sit on the console beside
        # the failure saying it is not evidence. It is printed after the check, and only when it held.
        for pids, printed in (([123, 123, 123, 123], True), ([123, 123, 124, 124], False)):
            with self.subTest(pids=pids):
                captured = io.StringIO()
                results: list[tuple[str, str]] = []
                failures: list[str] = []
                device = MagicMock()
                device.get_wallpaper_pid.side_effect = pids
                device.sleep_screen.return_value = True
                device.read_screen_on.return_value = False
                device.read_wallpaper_visible.return_value = False
                device.wake_screen.return_value = True
                device.capture_producer_window.side_effect = [
                    self.window(20, (True, True)),
                    self.window(0, (False, False)),
                    self.window(20, (True, True)),
                ]
                with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(captured):
                    qualification.phase_hidden_frames(device, results, failures)
                self.assertEqual(printed, "hidden, screen off:" in captured.getvalue())

    def test_a_restart_during_the_wake_does_not_blame_the_tracer_for_it(self) -> None:
        # The closing control asks whether the tracer still works, not whether the process survived
        # the wake: tracing the pid that held through the hidden window would find no frames if the
        # process was recreated during the wake and Home restoration, and would fail the tracer for a
        # process change that says nothing about it. The current pid is read after the wake and traced.
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        device = MagicMock()
        device.get_wallpaper_pid.side_effect = [123, 123, 123, 124]
        device.sleep_screen.return_value = True
        device.read_screen_on.return_value = False
        device.read_wallpaper_visible.return_value = False
        device.wake_screen.return_value = True
        device.capture_producer_window.side_effect = [
            self.window(20, (True, True)),
            self.window(0, (False, False)),
            self.window(20, (True, True)),
        ]
        with patch.object(qualification.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_hidden_frames(device, results, failures)
        # The opening control and the hidden window ran on the pid current then; the closing control
        # ran on the one read after the wake.
        self.assertEqual(
            [123, 123, 124],
            [call.args[0] for call in device.capture_producer_window.call_args_list],
        )
        self.assertEqual([], failures)
        self.assertEqual(3, len(results))

    def test_a_control_that_stopped_working_invalidates_the_zero(self) -> None:
        # The failure states what the closing control observed — no frames on the pid read after the
        # wake, so the tracer is not confirmed working — rather than the cause, which a process
        # recreated during the wake produces with a working tracer, on the very path the code
        # deliberately traces the current pid for.
        _, failures = self.run_phase(
            [self.window(20, (True, True)), self.window(0, (False, False)), self.window(0, (True, True))]
        )
        self.assertTrue(
            any(
                "tracer is not confirmed to have been working across the hidden window" in failure
                for failure in failures
            )
        )

    def test_a_closing_control_that_was_not_on_screen_is_not_a_visible_bracket(self) -> None:
        # The closing control's frame count carries the tracer's claim, but its display state is what
        # makes it the visible control it is named as: without the same display check the opening
        # control gets, a window with frames and every sample off would pass as that bracket.
        _, failures = self.run_phase(
            [self.window(20, (True, True)), self.window(0, (False, False)), self.window(20, (False, False))]
        )
        self.assertTrue(any("does not confirm a visible control after" in failure for failure in failures))

    def test_a_window_whose_display_came_on_is_not_a_screen_off_window(self) -> None:
        _, failures = self.run_phase(
            [self.window(20, (True, True)), self.window(0, (False, True, False)), self.window(20, (True, True))]
        )
        self.assertTrue(any("does not describe a screen-off window" in failure for failure in failures))

    def test_a_mixed_window_does_not_claim_its_frames_were_rendered_with_the_display_off(self) -> None:
        # A window whose samples are not all off has a frame count that is not attributable to a
        # screen-off window, so the zero-frame budget must not be applied to it: reporting those
        # frames as rendered with the display off would claim more than the samples support, and the
        # display-state failure is the whole verdict.
        for states in ((False, True, False), (False, None)):
            with self.subTest(states=states):
                _, failures = self.run_phase(
                    [self.window(20, (True, True)), self.window(5, states), self.window(20, (True, True))]
                )
                self.assertTrue(any("does not describe a screen-off window" in failure for failure in failures))
                self.assertFalse(any("budget is zero" in failure for failure in failures))

    def test_a_failed_trace_is_reported_rather_than_read_as_zero(self) -> None:
        # The injected message is the test's own, so it pins the phase's handling of a trace that
        # raised — reported, never read as a zero — and not the shape of any particular refusal. The
        # trace's own empty-buffer refusal is checked against the real function in ProducerTraceTest.
        results, failures = self.run_phase(
            [
                device_layer.ProbeError("the atrace dump could not be read"),
                self.window(0, (False, False)),
                self.window(20, (True, True)),
            ]
        )
        self.assertEqual([], results)
        self.assertTrue(any("producer trace failed" in failure for failure in failures))
        self.assertTrue(any("the atrace dump could not be read" in failure for failure in failures))


class HandDetectionTest(unittest.TestCase):
    """The hand-ink predicates and the angle estimate they feed."""

    def test_hand_ink_predicates_match_their_pinned_literals(self) -> None:
        self.assertTrue(device_layer.is_dark_hand_pixel(*DARK_HAND_RGB))
        self.assertFalse(device_layer.is_dark_hand_pixel(*device_layer.DARK_RIM_RGB))
        self.assertTrue(device_layer.is_light_hand_pixel(*LIGHT_HAND_RGB))
        self.assertFalse(device_layer.is_light_hand_pixel(*device_layer.LIGHT_RIM_RGB))

    def test_hand_ink_predicates_enforce_their_direction_guards(self) -> None:
        # Inside the dark bounds, but not warm enough: red minus blue stays below the guard.
        self.assertFalse(device_layer.is_dark_hand_pixel(230, 220, 215))
        # Inside the light bounds, but not red > green > blue.
        self.assertFalse(device_layer.is_light_hand_pixel(78, 78, 27))

    def test_coarse_angle_picks_the_densest_bin(self) -> None:
        self.assertAlmostEqual(device_layer.coarse_hand_angle([0.0] * 5 + [180.0] * 2), 2.0)

    def test_refine_angle_requires_the_minimum_sample_count(self) -> None:
        self.assertIsNone(device_layer.refine_hand_angle([0.0] * (device_layer.HAND_MIN_SAMPLES - 1), 0.0))
        refined = device_layer.refine_hand_angle([0.0] * device_layer.HAND_MIN_SAMPLES, 0.0)
        if refined is None:
            self.fail("a full sample set must be refined")
        self.assertAlmostEqual(refined, 0.0)

    def test_civil_hand_angle_matches_the_pinned_clock_state_anchors(self) -> None:
        """Mirror ClockStateTest.kt: noon is up, midnight down, 06:00 270°, 18:00 90°."""
        anchors = ((0, 180.0), (6 * 3600, 270.0), (12 * 3600, 0.0), (18 * 3600, 90.0), (3 * 3600 + 15 * 60, 228.75))
        for seconds, want in anchors:
            with self.subTest(seconds=seconds):
                self.assertAlmostEqual(device_layer.civil_hand_angle_deg(seconds), want, places=6)

    def test_civil_hand_angle_wraps_across_noon(self) -> None:
        self.assertAlmostEqual(device_layer.civil_hand_angle_deg(11 * 3600 + 59 * 60 + 59), 359.995833, places=5)
        self.assertAlmostEqual(device_layer.civil_hand_angle_deg(12 * 3600 + 1), 0.004167, places=5)

    def test_signed_circular_difference_wraps_across_zero(self) -> None:
        self.assertAlmostEqual(device_layer.signed_circular_difference_deg(1.0, 359.0), 2.0)
        self.assertAlmostEqual(device_layer.signed_circular_difference_deg(359.0, 1.0), -2.0)
        self.assertAlmostEqual(device_layer.signed_circular_difference_deg(198.75, 168.75), 30.0)
        # A half turn resolves to -180, so the documented range is the half-open [-180, 180).
        self.assertAlmostEqual(device_layer.signed_circular_difference_deg(0.0, 180.0), -180.0)
        self.assertAlmostEqual(device_layer.signed_circular_difference_deg(180.0, 0.0), -180.0)

    def test_dark_hand_stroke_is_located_on_a_synthetic_frame(self) -> None:
        frame = hand_frame(device_layer.DARK_RIM_RGB, DARK_HAND_RGB, HAND_UP_BEARING_DEG)
        angle = device_layer.detect_hand_angle(FRAME_WIDTH, FRAME_HEIGHT, frame)
        if angle is None:
            self.fail("the synthetic dark-ink stroke must be located")
        self.assertAlmostEqual(circular_difference_deg(angle, HAND_UP_BEARING_DEG), 0.0, places=1)

    def test_light_hand_stroke_is_located_on_a_synthetic_light_frame(self) -> None:
        frame = hand_frame(device_layer.LIGHT_RIM_RGB, LIGHT_HAND_RGB, HAND_DOWN_BEARING_DEG)
        angle = device_layer.detect_hand_angle(FRAME_WIDTH, FRAME_HEIGHT, frame)
        if angle is None:
            self.fail("the synthetic light-ink stroke must be located")
        self.assertAlmostEqual(circular_difference_deg(angle, HAND_DOWN_BEARING_DEG), 0.0, places=1)

    def test_the_wrong_theme_leaves_the_hand_unfound(self) -> None:
        # The cream hand ink on a light plate is not hand ink for that palette, so the probe must
        # report no hand rather than measuring the other theme's ink.
        frame = hand_frame(device_layer.LIGHT_RIM_RGB, DARK_HAND_RGB, HAND_UP_BEARING_DEG)
        self.assertIsNone(device_layer.detect_hand_angle(FRAME_WIDTH, FRAME_HEIGHT, frame))

    def test_capture_frame_accepts_a_consistent_payload(self) -> None:
        raw = struct.pack("<IIII", 2, 2, 1, 0) + bytes(2 * 2 * BYTES_PER_PIXEL)
        with (
            patch.object(device_layer, "run_adb", return_value=raw),
            patch.object(device_layer, "read_dial_layout", return_value=device_layer.DialLayout(1, 1, 0.5)),
        ):
            self.assertEqual(device_layer.capture_frame("device"), (2, 2, bytes(2 * 2 * BYTES_PER_PIXEL)))

    @patch.object(device_layer, "run_adb", return_value=bytes(8))
    def test_capture_frame_rejects_a_truncated_header(self, _run_adb: MagicMock) -> None:
        with self.assertRaisesRegex(RuntimeError, "payload too small"):
            device_layer.capture_frame("device")


class ProcessRebindPhaseTest(unittest.TestCase):
    """The rebind poll outlasts a transient startup capture but never hides a real failure."""

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "run_adb")
    @patch.object(
        device_layer,
        "get_wallpaper_pid",
        side_effect=[FIXTURE_START_PID, FIXTURE_REBOUND_PID, FIXTURE_REBOUND_PID],
    )
    @patch.object(
        device_layer,
        "capture_frame",
        side_effect=[
            device_layer.ScreencapError("Expected one fresh visible dial layout, found 0"),
            (2, 2, bytes(16)),
        ],
    )
    @patch.object(device_layer, "detect_hand_angle", return_value=0.0)
    @patch.object(qualification.time, "sleep")
    def test_a_transient_capture_error_is_retried_until_the_hand_is_seen(
        self,
        _sleep: MagicMock,
        _angle: MagicMock,
        capture: MagicMock,
        _pid: MagicMock,
        _run_adb: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """A fresh PID can precede a visible surface, so the first capture may find no layout yet."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_process_rebind(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "process rebind")

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "run_adb")
    @patch.object(
        device_layer,
        "get_wallpaper_pid",
        side_effect=[FIXTURE_START_PID, *([FIXTURE_REBOUND_PID] * qualification.REBIND_POLL_COUNT)],
    )
    @patch.object(device_layer, "capture_frame", side_effect=device_layer.ScreencapError("no usable layout"))
    @patch.object(device_layer, "detect_hand_angle", return_value=0.0)
    @patch.object(qualification.time, "sleep")
    def test_the_poll_budget_expiring_still_fails_the_phase(
        self,
        _sleep: MagicMock,
        _angle: MagicMock,
        capture: MagicMock,
        _pid: MagicMock,
        _run_adb: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """Retrying must not turn a permanently absent surface into a passing row."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_process_rebind(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(capture.call_count, qualification.REBIND_POLL_COUNT)
        self.assertEqual(results, [])
        self.assertEqual(
            failures,
            [f"Process recreation failed (PIDs: {FIXTURE_START_PID} -> {FIXTURE_REBOUND_PID}, angle=None)"],
        )

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "run_adb")
    @patch.object(device_layer, "get_wallpaper_pid", side_effect=[FIXTURE_START_PID, FIXTURE_REBOUND_PID])
    @patch.object(device_layer, "capture_frame", side_effect=subprocess.TimeoutExpired("screencap", 5))
    @patch.object(device_layer, "detect_hand_angle", return_value=0.0)
    @patch.object(qualification.time, "sleep")
    def test_a_transport_error_is_not_retried(
        self,
        _sleep: MagicMock,
        _angle: MagicMock,
        capture: MagicMock,
        _pid: MagicMock,
        _run_adb: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """Only ScreencapError is transient; a dead transport must stay loud, not become a delay."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(subprocess.TimeoutExpired):
            qualification.phase_process_rebind(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(capture.call_count, 1)


class PhaseDecisionTest(unittest.TestCase):
    """Decisions taken inside the extracted phase helpers."""

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "detect_hand_angle", side_effect=[0.0, 8.1, 180.0])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_time_travel_rejects_an_advance_past_the_tolerance(
        self, _sleep: MagicMock, _send: MagicMock, _capture: MagicMock, _angle: MagicMock, _ensure: MagicMock
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("residuals exceeded tolerance", failures[0])
        self.assertEqual(results, [])

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "detect_hand_angle", side_effect=[359.0, 6.5, 179.0])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_time_travel_measures_across_the_zero_crossing(
        self, _sleep: MagicMock, _send: MagicMock, _capture: MagicMock, _angle: MagicMock, _ensure: MagicMock
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "detect_hand_angle", side_effect=[0.0, 8.0, 180.0])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_time_travel_accepts_a_residual_exactly_on_the_tolerance(
        self, _sleep: MagicMock, _send: MagicMock, _capture: MagicMock, _angle: MagicMock, _ensure: MagicMock
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, [])

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "detect_hand_angle", side_effect=[0.0, 8.0004, 180.0])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_time_travel_rejects_a_residual_that_only_rounds_to_the_tolerance(
        self, _sleep: MagicMock, _send: MagicMock, _capture: MagicMock, _angle: MagicMock, _ensure: MagicMock
    ) -> None:
        """A 0.5004° residual rounds to 0.500°; comparing the raw value must still reject it."""
        res_30m = 8.0004 - device_layer.EXPECTED_ADVANCE_30M_DEG
        self.assertGreater(res_30m, device_layer.ANGLE_TOLERANCE_DEG)
        self.assertEqual(round(res_30m, qualification.ANGLE_REPORT_DECIMALS), device_layer.ANGLE_TOLERANCE_DEG)
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("Angle residuals exceeded tolerance", failures[0])
        self.assertEqual(results, [])

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "detect_hand_angle", side_effect=[0.0, 8.0, 179.0])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_time_travel_rejects_a_twelve_hour_residual_past_the_tolerance(
        self, _sleep: MagicMock, _send: MagicMock, _capture: MagicMock, _angle: MagicMock, _ensure: MagicMock
    ) -> None:
        """The +12h conjunct is checked on its own: a -1.000° residual must fail while +30m sits on the boundary."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, ["Angle residuals exceeded tolerance: 30m=+0.500°, 12h=-1.000°"])
        self.assertEqual(results, [])

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "detect_hand_angle")
    @patch.object(device_layer, "capture_frame")
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=False)
    @patch.object(qualification.time, "sleep")
    def test_time_travel_skips_angle_measurement_when_its_own_reset_is_unconfirmed(
        self, _sleep: MagicMock, _send: MagicMock, capture: MagicMock, angle: MagicMock, _ensure: MagicMock
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, ["Time-travel baseline reset was not confirmed by the service log"])
        self.assertEqual(results, [])
        capture.assert_not_called()
        angle.assert_not_called()

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague")
    @patch.object(device_layer, "detect_hand_angle", side_effect=[168.75, 179.958, 180.042, 191.25])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_brackets_the_saved_site_civil_midnight(
        self,
        _sleep: MagicMock,
        send: MagicMock,
        _capture: MagicMock,
        _angle: MagicMock,
        _zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], "midnight date rollover")
        self.assertIn("Europe/Prague", results[0][1])
        self.assertIn("saved site", results[0][1])
        self.assertIn("2026-06-20T21:15:00Z", results[0][1])
        self.assertIn("absolute civil-hand angle", results[0][1])
        self.assertIn("bracketed within 10s, not resolved", results[0][1])
        self.assertEqual(send.call_count, 5)

    def test_expected_rollover_angles_are_the_absolute_civil_angles(self) -> None:
        """The four Europe/Prague probe instants map to the absolute civil-hand angles 168.75 to 191.25°."""
        zone = ZoneInfo("Europe/Prague")
        instants = qualification.rollover_instants(zone)
        expected = qualification.expected_rollover_angles_deg(zone, instants)
        wants = [168.75, 179.958333, 180.041667, 191.25]
        for value, want in zip(expected, wants, strict=True):
            self.assertAlmostEqual(value, want, places=4)
        # The expected angles depend on the zone: interpreting the same instants in another zone
        # shifts them, so a probe that resolved the wrong zone lands far outside the tolerance.
        tokyo = qualification.expected_rollover_angles_deg(ZoneInfo("Asia/Tokyo"), instants)
        self.assertNotEqual([round(value, 3) for value in expected], [round(value, 3) for value in tokyo])

    def test_evaluate_rollover_flags_a_hand_that_jumps_backwards(self) -> None:
        """A backward step across a discriminable gap is reported even though residuals also fail."""
        zone = ZoneInfo("Europe/Prague")
        instants = qualification.rollover_instants(zone)
        measured = [168.75, 179.958, 180.042, 180.0]
        _, _, problems = qualification.evaluate_rollover(zone, instants, measured, "Europe/Prague")
        self.assertTrue(any("did not advance monotonically" in problem for problem in problems))

    def test_evaluate_rollover_accepts_the_exact_tolerance_boundary(self) -> None:
        """A residual of exactly the 0.5° tolerance passes, matching the time-travel boundary."""
        zone = ZoneInfo("Europe/Prague")
        instants = qualification.rollover_instants(zone)
        measured = [168.75, 179.958, 180.042, 191.75]
        _, residuals, problems = qualification.evaluate_rollover(zone, instants, measured, "Europe/Prague")
        self.assertAlmostEqual(residuals[-1], 0.5)
        self.assertEqual(problems, [])

    def test_evaluate_rollover_rejects_a_residual_that_only_rounds_to_the_tolerance(self) -> None:
        """A 0.5004° residual rounds to 0.500°; comparing the raw value must still flag it."""
        zone = ZoneInfo("Europe/Prague")
        instants = qualification.rollover_instants(zone)
        measured = [168.75, 179.958, 180.042, 191.7504]
        _, residuals, problems = qualification.evaluate_rollover(zone, instants, measured, "Europe/Prague")
        self.assertGreater(abs(residuals[-1]), device_layer.ANGLE_TOLERANCE_DEG)
        self.assertEqual(
            round(abs(residuals[-1]), qualification.ANGLE_REPORT_DECIMALS), device_layer.ANGLE_TOLERANCE_DEG
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("exceeds the", problems[0])

    def test_evaluate_rollover_rejects_a_negative_residual_past_the_tolerance(self) -> None:
        """The check uses the residual's magnitude: a -0.5004° residual must flag, not cancel out."""
        zone = ZoneInfo("Europe/Prague")
        instants = qualification.rollover_instants(zone)
        measured = [168.75, 179.958, 180.042, 190.7496]
        _, residuals, problems = qualification.evaluate_rollover(zone, instants, measured, "Europe/Prague")
        self.assertLess(residuals[-1], -device_layer.ANGLE_TOLERANCE_DEG)
        self.assertEqual(len(problems), 1)
        self.assertIn("exceeds the", problems[0])

    def test_evaluate_screen_off_cpu_decides_on_the_one_tick_upper_bound(self) -> None:
        """
        Pin the decision to the (delta + 1) upper bound rather than the raw delta.

        A delta of D ticks cannot exceed D+1 ticks of CPU time, so at 100 Hz the 5-tick sample a raw
        D/CLK_TCK comparison passed at 0.050 s has a 0.060 s bound and must fail: deciding on the
        quotient would report CPU time the measurement cannot establish. Both comparisons are strict,
        so a single tick exactly equal to the budget (CLK_TCK == 20) still passes a zero delta, and
        the first clock coarse enough to exceed the budget is 19.
        """
        cases = (
            # ticks_start, ticks_end, clock_ticks, expected delta, expected failure substring
            (100, 100, 100, 0, None),  # zero delta, well inside the budget
            (100, 104, 100, 4, None),  # bound 0.050 s: exactly the budget, so it still passes
            (100, 105, 100, 5, "used up to 0.060s CPU"),  # the tightened boundary: bound 0.060 s
            (100, 101, 40, 1, None),  # a non-round ratio puts the boundary at 2/40 == 0.050 s too
            (100, 102, 40, 2, "used up to 0.075s CPU"),  # 3/40 s crosses it
            (100, 100, 20, 0, None),  # one tick == the budget: true CPU time is below it
            (100, 101, 20, 1, "used up to 0.100s CPU"),  # the equality clock passes the guard, then the budget governs
            (100, 100, 19, None, "cannot establish it"),  # one tick > the budget: the clock cannot resolve it
            (100, 100, 10, None, "cannot establish it"),
        )
        for ticks_start, ticks_end, clock_ticks, expected_delta, expected_failure in cases:
            with self.subTest(clock_ticks=clock_ticks, ticks_end=ticks_end):
                delta, failure = qualification.evaluate_screen_off_cpu(
                    ticks_start=ticks_start,
                    ticks_end=ticks_end,
                    clock_ticks=clock_ticks,
                    window_seconds=4.00,
                )
                self.assertEqual(delta, expected_delta)
                self.assertEqual(failure is not None, expected_failure is not None)
                if failure is not None and expected_failure is not None:
                    self.assertIn(expected_failure, failure)

    def test_format_screen_off_observation_reports_the_one_tick_upper_bound(self) -> None:
        """
        The reported figure is the one-tick upper bound, marked "<", for every delta.

        The 0-tick / 100 Hz / 4.01 s row is asserted verbatim: it is the row in
        2026-10-09-test-6-civil-midnight-and-cpu-evidence.md, and the same CPU clause appears in the
        other two 2026-10-09 reports at a different window or wake angle, so collapsing the two
        reporting branches must not move the recorded text.
        """
        self.assertEqual(
            qualification.format_screen_off_observation(
                wallpaper_visible=False,
                ticks_delta=0,
                clock_ticks=100,
                window_seconds=4.01,
                angle_wake=69.884,
            ),
            (
                "wallpaper reported hidden (mVisible=false); 0 CPU ticks over the measured 4.01s "
                "screen-off window at 100 ticks/s (<0.010s CPU, <0.25% of the window; budget 0.05s); "
                "hand detected after wake at 69.884°"
            ),
        )
        # A nonzero delta reports its own bound, not the raw delta/rate quotient.
        nonzero = qualification.format_screen_off_observation(
            wallpaper_visible=False,
            ticks_delta=5,
            clock_ticks=100,
            window_seconds=4.00,
            angle_wake=69.884,
        )
        self.assertIn("5 CPU ticks over the measured 4.00s screen-off window at 100 ticks/s", nonzero)
        self.assertIn("<0.060s CPU", nonzero)
        # Without a clock rate there is no bound to report, so the row stays unmeasured.
        self.assertIn(
            "rendering while asleep was not measured",
            qualification.format_screen_off_observation(
                wallpaper_visible=False,
                ticks_delta=0,
                clock_ticks=None,
                window_seconds=4.00,
                angle_wake=69.884,
            ),
        )

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague")
    @patch.object(device_layer, "detect_hand_angle", side_effect=[198.75, 209.958, 210.042, 221.25])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_rejects_a_constant_angular_shift(
        self,
        _sleep: MagicMock,
        _send: MagicMock,
        _capture: MagicMock,
        _angle: MagicMock,
        _zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """The relative check accepted a fixed +30° error on every sample; the absolute one rejects it."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(results, [])
        self.assertEqual(len(failures), 4)
        for failure in failures:
            self.assertIn("residual +30.000°", failure)
            self.assertIn("exceeds the 0.5° tolerance", failure)

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_device_timezone")
    @patch.object(
        device_layer,
        "read_saved_site_zone_id",
        side_effect=device_layer.ProbeError("saved-site prefs probe failed: package not debuggable"),
    )
    @patch.object(device_layer, "send_debug_clock_broadcast")
    @patch.object(device_layer, "capture_frame")
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_fails_loudly_when_the_saved_site_probe_fails(
        self,
        _sleep: MagicMock,
        capture: MagicMock,
        send: MagicMock,
        _saved: MagicMock,
        device_zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """A failed saved-site read must fail the phase, not fall back to the device timezone."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(results, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("the saved-site preference probe failed", failures[0])
        self.assertIn("package not debuggable", failures[0])
        device_zone.assert_not_called()
        send.assert_not_called()
        capture.assert_not_called()

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_device_timezone", return_value="Europe/Prague")
    @patch.object(device_layer, "read_saved_site_zone_id", return_value=None)
    @patch.object(device_layer, "detect_hand_angle", side_effect=[168.75, 179.958, 180.042, 191.25])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_falls_back_to_the_device_timezone(
        self,
        _sleep: MagicMock,
        _send: MagicMock,
        _capture: MagicMock,
        _angle: MagicMock,
        _saved: MagicMock,
        _device_zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assertIn("device timezone", results[0][1])
        self.assertIn("Europe/Prague", results[0][1])

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_device_timezone", return_value=None)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value=None)
    @patch.object(device_layer, "send_debug_clock_broadcast")
    @patch.object(device_layer, "capture_frame")
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_fails_without_a_readable_zone(
        self,
        _sleep: MagicMock,
        capture: MagicMock,
        send: MagicMock,
        _saved: MagicMock,
        _device_zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """An unresolvable zone must fail loudly rather than pass without verifying anything."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            [
                (
                    "Midnight rollover could not be verified: neither the saved site zone nor the device "
                    "timezone was readable"
                )
            ],
        )
        self.assertEqual(results, [])
        send.assert_not_called()
        capture.assert_not_called()

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_device_timezone", return_value=None)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Invalid/Zone")
    @patch.object(device_layer, "send_debug_clock_broadcast")
    @patch.object(device_layer, "capture_frame")
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_fails_on_an_unrecognized_zone(
        self,
        _sleep: MagicMock,
        capture: MagicMock,
        send: MagicMock,
        _saved: MagicMock,
        _device_zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """A zone the harness cannot build fails loudly; it must not probe the device zone instead."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            [
                (
                    "Midnight rollover could not be verified: the rendered zone 'Invalid/Zone' (saved site) "
                    "does not resolve to a timezone this harness can construct"
                )
            ],
        )
        self.assertEqual(results, [])
        send.assert_not_called()
        capture.assert_not_called()

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague")
    @patch.object(device_layer, "detect_hand_angle", side_effect=[168.75, 179.958, 180.042, 180.042])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_fails_when_the_hand_is_frozen(
        self,
        _sleep: MagicMock,
        _send: MagicMock,
        _capture: MagicMock,
        _angle: MagicMock,
        _zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        """A hand frozen through midnight advances 0°, so the residuals must reject it."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(results, [])
        self.assertNotEqual(failures, [])
        self.assertTrue(all("exceeds the 0.5° tolerance" in failure for failure in failures))

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague")
    @patch.object(device_layer, "detect_hand_angle", side_effect=[168.75, 179.958, 180.042, 195.0])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_fails_when_a_sample_residual_exceeds_tolerance(
        self,
        _sleep: MagicMock,
        _send: MagicMock,
        _capture: MagicMock,
        _angle: MagicMock,
        _zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("2026-06-20T22:45:00Z", failures[0])
        self.assertIn("residual +3.750°", failures[0])
        self.assertEqual(results, [])

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague")
    @patch.object(device_layer, "detect_hand_angle", side_effect=[168.75, 168.75, 168.75, 168.75])
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_fails_when_every_sample_is_frozen(
        self,
        _sleep: MagicMock,
        _send: MagicMock,
        _capture: MagicMock,
        _angle: MagicMock,
        _zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(results, [])
        self.assertEqual(len(failures), 3)

    @patch.object(qualification, "ensure_screen_on", return_value=True)
    @patch.object(device_layer, "read_saved_site_zone_id", return_value="Europe/Prague")
    @patch.object(device_layer, "detect_hand_angle")
    @patch.object(device_layer, "capture_frame")
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=False)
    @patch.object(qualification.time, "sleep")
    def test_midnight_rollover_skips_when_instant_broadcast_fails(
        self,
        _sleep: MagicMock,
        _send: MagicMock,
        capture: MagicMock,
        angle: MagicMock,
        _zone: MagicMock,
        _ensure: MagicMock,
    ) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(len(failures), 1)
        self.assertIn("was not confirmed or hand not detected", failures[0])
        self.assertEqual(results, [])
        capture.assert_not_called()
        angle.assert_not_called()

    def test_midnight_rollover_skips_when_screen_not_confirmed(self) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(qualification, "ensure_screen_on", return_value=False),
            patch.object(device_layer, "send_debug_clock_broadcast") as broadcast,
            patch.object(device_layer, "capture_frame") as capture,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_midnight_rollover(device_layer.AdbDevice("device"), results, failures)
        broadcast.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(results, [])

    @patch.object(device_layer, "run_adb", return_value=b"--------- beginning of main\nW/DialRenderer: failed draw\n")
    def test_a_matching_warning_record_fails_the_log_scan(self, _run_adb: MagicMock) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with contextlib.redirect_stdout(io.StringIO()):
            inconclusive = qualification.phase_renderer_log_scan(
                device_layer.AdbDevice("device"), "marker", results, failures
            )
        self.assertFalse(inconclusive)
        self.assertEqual(failures, ["1 unexpected warning(s) in logcat"])

    @patch.object(
        device_layer,
        "run_adb",
        return_value=struct.pack("<IIII", 2, 2, 1, 0) + bytes(2 * 2 * BYTES_PER_PIXEL),
    )
    def test_recreate_size_is_chosen_to_differ_from_the_size_in_effect(self, _run_adb: MagicMock) -> None:
        self.assertEqual(device_layer.choose_recreate_size("1080x2000", None), "1080x1800")
        self.assertEqual(device_layer.choose_recreate_size("1080x2408", "1080x2000"), "1080x1800")
        self.assertEqual(device_layer.choose_recreate_size("1080x2000", "1080x1200"), "1080x2000")

    def test_size_restore_command_puts_back_an_override_verbatim(self) -> None:
        self.assertEqual(device_layer.size_restore_command("720x1280")[-1], "720x1280")
        self.assertEqual(device_layer.size_restore_command(None)[-1], "reset")

    def test_restore_commands_place_the_night_mode_only_when_it_was_read(self) -> None:
        self.assertEqual(
            qualification.restore_commands(None, "auto"),
            [
                ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                ["shell", "wm", "size", "reset"],
                ["shell", "cmd", "uimode", "night", "auto"],
                ["shell", "input", "keyevent", "KEYCODE_HOME"],
            ],
        )
        self.assertEqual(
            qualification.restore_commands("720x1280", None),
            [
                ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                ["shell", "wm", "size", "720x1280"],
                ["shell", "input", "keyevent", "KEYCODE_HOME"],
            ],
        )


class DeviceStateParsingTest(unittest.TestCase):
    """Uncertain device readings must not authorize mutation or discard an override."""

    def test_display_size_readings_are_complete_positive_and_consistent(self) -> None:
        cases = (
            ("Physical size: 1080x2408", ("1080x2408", None)),
            ("Physical size: 1080x2408\nOverride size: 720x1280", ("1080x2408", "720x1280")),
            ("Physical size: 1080x2408\nPhysical size: 1080x2408", ("1080x2408", None)),
            ("", (None, None)),
            ("Override size: 720x1280", (None, None)),
            ("Physical size: unknown\nOverride size: 720x1280", (None, None)),
            ("Physical size: 1080x2408\nOverride size: unknown", (None, None)),
            ("Physical size: 1080x2408\nOverride size: 720x1280 trailing", (None, None)),
            ("Physical size: 0x2408", (None, None)),
            ("Physical size: 1080x2408\nOverride size: 720x0", (None, None)),
            ("Physical size: 1080x2408\nPhysical size: 720x1280", (None, None)),
            ("Physical size: 1080x2408\nOverride size: 720x1280\nOverride size: 480x800", (None, None)),
        )
        for output, expected in cases:
            with (
                self.subTest(output=output),
                patch.object(device_layer, "run_adb", return_value=output.encode()),
            ):
                self.assertEqual(device_layer.read_display_size("device"), expected)

    def test_keyguard_requires_complete_values_and_all_unlocked_readings(self) -> None:
        cases = (
            ("isKeyguardShowing=false", False),
            ("isKeyguardShowing=false isKeyguardShowing=false", False),
            ("isKeyguardShowing=true", True),
            ("isKeyguardShowing=false\nisKeyguardShowing=true", True),
            ("isKeyguardShowing=unknown\nisKeyguardShowing=true", True),
            ("isKeyguardShowing=\nisKeyguardShowing=true", True),
            ("isKeyguardShowing=\nisKeyguardShowing=false", None),
            ("", None),
            ("isKeyguardShowing=", None),
            ("isKeyguardShowing=falseish", None),
            ("isKeyguardShowing=trueish", None),
            ("isKeyguardShowing=false\nisKeyguardShowing=unknown", None),
            ("isKeyguardShowing=unknown\nisKeyguardShowing=false", None),
        )
        for output, expected in cases:
            with (
                self.subTest(output=output),
                patch.object(device_layer, "run_adb", return_value=output.encode()),
            ):
                self.assertIs(device_layer.read_keyguard_locked("device"), expected)


class ForegroundProbeTest(unittest.TestCase):
    """Finding 3: the surface in front is read back, and it is read as a package."""

    def read(self, output: str) -> object:
        """Run the foreground probe against a `dumpsys window` body."""
        with patch.object(device_layer, "run_adb", return_value=output.encode()):
            return device_layer.read_foreground_package("device")

    def test_the_focus_window_names_the_package(self) -> None:
        output = "  mCurrentFocus=Window{a1b2c3d u0 com.example.app/com.example.app.MainActivity}\n"
        self.assertEqual("com.example.app", self.read(output))

    def test_a_focus_window_without_a_package_is_undecidable_not_a_fallthrough(self) -> None:
        # This device's own state while the shade is down: the focused *window* is a system window
        # with no package in it. Reading the resumed activity from the line below would report the
        # launcher as the surface in front while an overlay covers it, and `read_home_in_front`
        # would then admit a window measured on the shade as a visible home-screen window.
        output = (
            "  mCurrentFocus=Window{39e680d u0 NotificationShade}\n"
            "  mFocusedApp=ActivityRecord{111535639 u0 com.example.launcher/"
            ".activities.LauncherActivity t6685}\n"
        )
        self.assertIsNone(self.read(output))

    def test_a_null_focus_reads_the_resumed_app(self) -> None:
        output = "  mCurrentFocus=null\n  mFocusedApp=ActivityRecord{1 u0 com.example.app/.Main t1}\n"
        self.assertEqual("com.example.app", self.read(output))

    def test_no_focus_line_at_all_is_none_rather_than_a_guess(self) -> None:
        for output in ("", "  mCurrentFocus=null\n", "  something else entirely\n"):
            with self.subTest(output=output[:24]):
                self.assertIsNone(self.read(output))

    def test_the_home_package_is_the_one_the_device_resolves(self) -> None:
        # The device is asked which launcher it runs rather than the harness naming one: a launcher
        # package in this repository would be an OEM and model statement on a public surface.
        output = (
            "priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true\n"
            "com.example.launcher/.activities.LauncherActivity\n"
        )
        with patch.object(device_layer, "run_adb", return_value=output.encode()):
            self.assertEqual("com.example.launcher", device_layer.read_home_package("device"))

    def test_an_unresolvable_home_package_is_none(self) -> None:
        for output in ("", "No activity found\n"):
            with self.subTest(output=output[:20]), patch.object(device_layer, "run_adb", return_value=output.encode()):
                self.assertIsNone(device_layer.read_home_package("device"))

    def read_home_in_front(self, focus: str, home: str) -> bool | None:
        """Answer `dumpsys window` with `focus` and the home resolution with `home`, then read it."""

        def run(args: list[str], serial: str | None = None, timeout: int = 0) -> bytes:
            del serial, timeout
            body = home if "resolve-activity" in args else f"  mCurrentFocus={focus}\n"
            return body.encode()

        with patch.object(device_layer, "run_adb", side_effect=run):
            return device_layer.read_home_in_front("device")

    def test_home_in_front_compares_packages_not_activity_classes(self) -> None:
        # The launcher's activity class differs between builds and launchers; the package that owns
        # the home screen is what says the home screen is showing. A focus window with no package —
        # this device's own state while the shade is down — leaves the answer undecidable rather than
        # being read either way.
        cases = (
            (
                "Window{1 u0 com.example.launcher/.activities.LauncherActivity}",
                "com.example.launcher/.activities.OtherLauncherActivity",
                True,
            ),
            ("Window{1 u0 com.example.other/.MainActivity}", "com.example.launcher/.L", False),
            ("Window{1 u0 NotificationShade}", "com.example.launcher/.L", None),
        )
        for focus, home, expected in cases:
            with self.subTest(focus=focus):
                self.assertIs(self.read_home_in_front(focus, home), expected)

    def test_home_in_front_is_undecidable_when_the_home_package_cannot_be_resolved(self) -> None:
        self.assertIs(self.read_home_in_front("Window{1 u0 com.example.app/.Main}", ""), None)

    def test_the_stable_home_read_is_taken_before_the_foreground_snapshot(self) -> None:
        # The ordering is the point of the fix: resolving `HOME` first leaves the foreground read as
        # late as possible before the comparison, narrowing the window in which a switch away from
        # home could be read as the state now. Later rounds must not put the snapshot back in front.
        with patch.object(device_layer, "run_adb", return_value=b"  mCurrentFocus=null\n") as run_adb:
            device_layer.read_home_in_front("device")
        # Each recorded invocation, named as such: `call` here would read like `unittest.mock.call`,
        # which this module does not import and does not need.
        self.assertEqual(
            [invocation.args[0][:2] for invocation in run_adb.call_args_list],
            [["shell", "cmd"], ["shell", "dumpsys"]],
        )


class InstalledApkDigestTest(unittest.TestCase):
    """Finding 4: the digest names the build the device is running, not the host's artifact."""

    BASE_APK = "/data/app/~~xyz==/io.github.godaniya.astronomicalclockswallpaper.debug-abc==/base.apk"
    DIGEST = "3ef8bf795587aff1488e2e073b3cc3bf70eb7620df821cb4bfa3d953fda3dc4d"

    def run_with(self, path_output: bytes, digest_output: bytes) -> object:
        """Run the digest probe against the two commands' outputs."""
        with patch.object(device_layer, "run_adb", side_effect=[path_output, digest_output]):
            return device_layer.read_installed_apk_sha256("device")

    def test_the_base_apk_digest_is_returned(self) -> None:
        self.assertEqual(
            self.DIGEST,
            self.run_with(
                f"package:{self.BASE_APK}\n".encode(),
                f"{self.DIGEST}  {self.BASE_APK}\n".encode(),
            ),
        )

    def test_a_split_listing_still_hashes_the_base_apk(self) -> None:
        paths = (
            f"package:{self.BASE_APK}\n"
            "package:/data/app/~~xyz==/io.github.godaniya.astronomicalclockswallpaper.debug-abc==/split_config.arm64.apk\n"
        )
        self.assertEqual(
            self.DIGEST,
            self.run_with(paths.encode(), f"{self.DIGEST}  {self.BASE_APK}\n".encode()),
        )

    def test_a_package_that_is_not_installed_is_none(self) -> None:
        self.assertIsNone(self.run_with(b"", b""))

    def test_a_device_without_sha256sum_is_none_rather_than_an_exception(self) -> None:
        # A device lacking `sha256sum` answers 127, the shell's command-not-found, which `adb shell`
        # propagates and `run_adb` checks. That status is the refusal the docstring promises rather
        # than an exception escaping the probe.
        with patch.object(
            device_layer,
            "run_adb",
            side_effect=[
                f"package:{self.BASE_APK}\n".encode(),
                subprocess.CalledProcessError(127, ["adb"], stderr=b"/system/bin/sh: sha256sum: not found"),
            ],
        ):
            self.assertIsNone(device_layer.read_installed_apk_sha256("device"))

    def test_other_hash_failures_propagate(self) -> None:
        # Only command-not-found is a refusal. A transport that dropped or a `pm` that could not
        # answer is this probe failing, and returning None would record an unreadable identity in
        # place of a reading.
        with (
            patch.object(
                device_layer,
                "run_adb",
                side_effect=[
                    f"package:{self.BASE_APK}\n".encode(),
                    subprocess.CalledProcessError(1, ["adb"], stderr=b"device offline"),
                ],
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            device_layer.read_installed_apk_sha256("device")

    def test_error_text_on_stdout_is_not_read_as_a_digest(self) -> None:
        # A shell that reports an error while still exiting 0 puts that text where the digest would
        # be; recording its first token would make it look like the build's identity. This is the
        # shape guard, which is independent of the exit status handled above.
        self.assertIsNone(
            self.run_with(
                f"package:{self.BASE_APK}\n".encode(),
                b"/system/bin/sh: sha256sum: not found\n",
            )
        )


class PhasePrerequisiteTest(unittest.TestCase):
    """Each phase must verify its own prerequisite before collecting passing evidence."""

    def test_unconfirmed_pss_reset_skips_sampling_and_passing_rows(self) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "send_debug_clock_broadcast", return_value=False),
            patch.object(device_layer, "run_adb", return_value=b"TOTAL 100 0 0") as run_adb,
            patch.object(qualification, "ensure_screen_on", return_value=True),
            patch.object(qualification.time, "sleep") as sleep,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_total_pss_growth(device_layer.AdbDevice("device"), 10, results, failures)
        self.assertEqual(failures, ["PSS baseline reset was not confirmed by the service log"])
        self.assertEqual(results, [])
        run_adb.assert_not_called()
        sleep.assert_not_called()

    def test_confirmed_pss_sampling_enforces_the_budget(self) -> None:
        for after, within_budget in ((110, True), (111, False)):
            results: list[tuple[str, str]] = []
            failures: list[str] = []
            with (
                self.subTest(after=after),
                patch.object(device_layer, "send_debug_clock_broadcast", return_value=True),
                patch.object(device_layer, "run_adb", side_effect=[b"TOTAL 100 0 0", f"TOTAL {after} 0 0".encode()]),
                patch.object(qualification, "ensure_screen_on", return_value=True),
                patch.object(qualification.time, "sleep"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                qualification.phase_total_pss_growth(device_layer.AdbDevice("device"), 10, results, failures)
                self.assertEqual(bool(results), within_budget)
                self.assertEqual(bool(failures), not within_budget)

    def test_surface_phase_verifies_restore_before_capturing(self) -> None:
        for original in (None, "720x1280"):
            for readback in (("1080x2408", original), ("1080x2408", "1080x2000"), (None, None)):
                with self.subTest(original=original, readback=readback):
                    self.check_surface_restore(original, readback)

    def test_surface_failure_survives_successful_final_cleanup(self) -> None:
        for readback in (("1080x2408", "1080x2000"), (None, None)):
            with self.subTest(readback=readback):
                self.check_surface_failure_through_main(qualification, readback)

    def test_the_preview_phase_passes_only_a_confirmed_launch_with_a_visible_hand(self) -> None:
        """
        The preview phase's evidence is a confirmed launch plus a hand on the home screen.

        Neither an unconfirmed launch nor a missing hand may leave a passing row behind. The
        unconfirmed-launch row is the one the full-run test used to emit while claiming every other
        phase succeeded, so it is pinned here on its own.
        """
        for launch, angle, expected_failures in (
            (b"Status: error\n", 1.0, ["Wallpaper preview did not report a successful launch"]),
            (b"Status: ok\n", None, ["Hand missing after preview close"]),
            (b"Status: ok\n", 1.0, []),
        ):
            with self.subTest(launch=launch, angle=angle):
                results: list[tuple[str, str]] = []
                failures: list[str] = []
                with (
                    patch.object(device_layer, "run_adb", return_value=launch),
                    patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
                    patch.object(device_layer, "detect_hand_angle", return_value=angle),
                    patch.object(qualification, "ensure_screen_on", return_value=True),
                    patch.object(qualification.time, "sleep"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    qualification.phase_preview_navigation(device_layer.AdbDevice("device"), results, failures)
                self.assertEqual(failures, expected_failures)
                self.assertEqual(len(results), int(not expected_failures))

    def test_an_override_that_did_not_take_fails_the_surface_phase(self) -> None:
        """A resize the readback never shows as active must fail, not be reported as a redrawn dial."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "read_display_size", side_effect=[("1080x2408", None), ("1080x2408", None)]),
            patch.object(device_layer, "run_adb") as run_adb,
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=1.0),
            patch.object(qualification, "ensure_screen_on", return_value=True),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_surface_recreation(
                device_layer.AdbDevice("device"), None, "1080x2000", results, failures
            )
        self.assertEqual(failures, ["Display-size override was not active as requested (None)"])
        self.assertEqual(results, [])
        # The restore command still runs: a failed override must not leave the display resized.
        self.assertEqual(run_adb.call_args.args[0], device_layer.size_restore_command(None))

    def test_a_failed_environment_wake_skips_the_reset_broadcast(self) -> None:
        """An unconfirmed environment wake must stop phase 0 before any reset or navigation."""
        failures: list[str] = []
        with (
            patch.object(device_layer, "wake_screen", return_value=False) as wake,
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
            patch.object(device_layer, "send_debug_clock_broadcast") as broadcast,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            confirmed = qualification.phase_environment_setup(device_layer.AdbDevice("device"), failures)
        self.assertFalse(confirmed)
        wake.assert_called_once_with("device")
        dismiss.assert_not_called()
        home.assert_not_called()
        broadcast.assert_not_called()
        self.assertEqual(failures, ["Environment wake was not confirmed by the screen-state readback"])

    def test_a_failed_sleep_stops_the_screen_off_phase(self) -> None:
        """A refused screen-sleep request must not be papered over by the wake/capture checks."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=False) as sleep,
            patch.object(device_layer, "read_screen_on") as read_screen,
            patch.object(device_layer, "wake_screen") as wake,
            patch.object(device_layer, "capture_frame") as capture,
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        sleep.assert_called_once_with("device")
        read_screen.assert_not_called()
        wake.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(failures, ["Screen sleep request failed; the screen-off / wake phase was skipped"])
        self.assertEqual(results, [])

    def test_a_failed_wake_stops_the_screen_off_phase(self) -> None:
        """An unconfirmed wake must stop the phase before the keyguard, home, and capture checks."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", return_value=False) as read_screen,
            patch.object(device_layer, "read_wallpaper_visible", return_value=None),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", return_value=100),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(device_layer, "wake_screen", return_value=False) as wake,
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
            patch.object(device_layer, "capture_frame") as capture,
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(read_screen.call_count, 2)
        wake.assert_called_once_with("device")
        dismiss.assert_not_called()
        home.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(failures, ["Device wake was not confirmed after the screen-off interval"])
        self.assertEqual(results, [])

    def test_screen_off_wake_preserves_visibility_and_wake_observations(self) -> None:
        """Visibility is tri-state; no visibility result proves rendering stopped."""
        cases = (
            (False, "wallpaper reported hidden (mVisible=false)"),
            (None, "wallpaper visibility was unreadable"),
            (True, "wallpaper reported visible (mVisible=true)"),
        )
        for visible, diagnostic in cases:
            results: list[tuple[str, str]] = []
            failures: list[str] = []
            with (
                self.subTest(visible=visible),
                patch.object(device_layer, "sleep_screen", return_value=True),
                patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
                patch.object(device_layer, "read_wallpaper_visible", return_value=visible),
                patch.object(device_layer, "get_wallpaper_pid", return_value=123),
                patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]),
                patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
                patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
                patch.object(device_layer, "wake_screen", return_value=True) as wake,
                patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
                patch.object(device_layer.AdbDevice, "show_home") as home,
                patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
                patch.object(device_layer, "detect_hand_angle", return_value=123.456),
                patch.object(qualification.time, "sleep"),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
            if visible is True:
                self.assertEqual(
                    failures, ["Wallpaper reported visible (mVisible=true) while the screen was confirmed off"]
                )
            else:
                self.assertEqual(failures, [])
            wake.assert_called_once_with("device")
            dismiss.assert_called_once()
            home.assert_called_once()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], "screen-off / wake navigation")
            self.assertIn(diagnostic, results[0][1])
            self.assertIn("0 CPU ticks over the measured 4.00s screen-off window at 100 ticks/s", results[0][1])
            self.assertIn("<0.010s CPU", results[0][1])
            self.assertIn("budget 0.05s", results[0][1])
            self.assertIn("hand detected after wake at 123.456°", results[0][1])
            for unsupported in (
                "0.0%",
                "dormancy",
                "halted",
                "within 1s",
                "off for 4s",
                "sleep interval",
                "confirmed-off",
            ):
                self.assertNotIn(unsupported, results[0][1])
                self.assertNotIn(unsupported, output.getvalue())

    def test_screen_off_wake_reports_the_upper_bound_for_a_nonzero_sample(self) -> None:
        """
        A nonzero sample inside the budget is reported as an upper bound, not the exact measurement.

        Four ticks at 100 Hz bound CPU time below 5 ticks, i.e. below 0.050 s: that bound sits exactly
        on the budget, yet the strict comparison still passes it.
        """
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 104]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.2]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 1)
        self.assertIn("4 CPU ticks over the measured 4.20s screen-off window at 100 ticks/s", results[0][1])
        self.assertIn("<0.050s CPU", results[0][1])

    def test_screen_off_wake_fails_when_cpu_ticks_cannot_be_read(self) -> None:
        """When CPU ticks cannot be read during screen-off, the phase must append a failure."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", return_value=None),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, ["Failed to sample wallpaper CPU ticks during confirmed screen-off state"])
        self.assertEqual(len(results), 1)
        self.assertIn("rendering while asleep was not measured", results[0][1])

    def test_screen_off_wake_fails_when_cpu_ticks_exceed_the_seconds_budget(self) -> None:
        """Six ticks at 100 Hz bound CPU time below 7 ticks (0.070 s), which exceeds the 0.050 s budget."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 106]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            [
                (
                    "Wallpaper process used up to 0.070s CPU while screen off "
                    "(6 ticks over the measured 4.00s window at 100 ticks/s); budget 0.05s"
                )
            ],
        )
        self.assertEqual(len(results), 1)

    def test_screen_off_wake_refuses_a_sample_the_clock_cannot_resolve(self) -> None:
        """
        A clock tick wider than the budget can never show the sample within budget, so the phase fails.

        At CLK_TCK=10 one tick is 0.100 s, over the 0.050 s budget: even a zero-tick delta leaves the
        sample unestablishable, so the observation row reads "not measured" rather than passing.
        """
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=10),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            [
                (
                    "The device clock ticks at 10/s, so one 0.100s tick is wider than the "
                    "0.05s budget and this sample cannot establish it"
                )
            ],
        )
        self.assertEqual(len(results), 1)
        self.assertIn("rendering while asleep was not measured", results[0][1])

    def test_screen_off_wake_fails_when_the_clock_tick_rate_is_unreadable(self) -> None:
        """Without CLK_TCK the tick count has no CPU-time meaning, so the phase must fail."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=None),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            ["Could not read the device clock-tick rate (getconf CLK_TCK); CPU time is unbounded"],
        )
        self.assertEqual(len(results), 1)
        self.assertIn("rendering while asleep was not measured", results[0][1])

    def test_screen_off_wake_fails_when_cpu_ticks_go_backward(self) -> None:
        """If CPU tick counter decreases, it indicates PID recycling or stat corruption."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 90]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, ["CPU ticks went backward during screen-off: 100 -> 90"])
        self.assertEqual(len(results), 1)

    def test_screen_off_wake_fails_when_the_screen_wakes_mid_window(self) -> None:
        """A display that is on at the window's end voids the bounded-window claim."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, True, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", return_value=123),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, ["Display was not confirmed off at the end of the CPU sample window"])
        self.assertEqual(len(results), 1)

    def test_screen_off_wake_fails_when_the_process_changes_mid_window(self) -> None:
        """A PID that rebounds by the window's end means the closing ticks belong to another process."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", side_effect=[123, 123, 124]),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            [
                (
                    "Wallpaper PID was not confirmed unchanged across the CPU sample window "
                    "(before=123, after=124); the measurement is not attributable to one process"
                )
            ],
        )
        self.assertEqual(len(results), 1)

    def test_screen_off_wake_fails_when_the_process_changes_across_the_sleep_transition(self) -> None:
        """A rebound between the two settling reads is rejected before the window even opens."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]),
            patch.object(device_layer, "read_wallpaper_visible", return_value=False),
            patch.object(device_layer, "get_wallpaper_pid", side_effect=[123, 124, 124]),
            patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]),
            patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100),
            patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]),
            patch.object(device_layer, "wake_screen", return_value=True),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard"),
            patch.object(device_layer.AdbDevice, "show_home"),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "detect_hand_angle", return_value=123.456),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(
            failures,
            [
                (
                    "Wallpaper PID was not confirmed unchanged across the screen-off transition "
                    "(before=123, after=124); the measurement is not attributable to one process"
                )
            ],
        )
        self.assertEqual(len(results), 1)

    def test_screen_off_wake_fails_when_screen_not_confirmed_off(self) -> None:
        """If the screen never turns off after sleep command, the phase must fail and halt."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(device_layer, "sleep_screen", return_value=True),
            patch.object(device_layer, "read_screen_on", return_value=True),
            patch.object(device_layer, "wake_screen") as wake,
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        self.assertEqual(failures, ["Display was not confirmed off after sleep request"])
        self.assertEqual(results, [])
        wake.assert_not_called()

    def test_visible_while_off_failure_survives_wake_recovery_and_cleanup(self) -> None:
        """The violation fails the run even after recovery or a later wake failure."""
        baseline = qualification.DeviceBaseline(
            serial="device",
            run_start_marker="date",
            physical_size="1080x2408",
            size_override=None,
            screen_was_on=True,
            night_mode="auto",
            wallpaper_pid=123,
        )
        for wake_ok, wake_error in ((True, None), (False, None), (False, RuntimeError("wake probe failed"))):
            with contextlib.ExitStack() as stack:
                stack.enter_context(self.subTest(wake_ok=wake_ok, wake_error=wake_error))
                stack.enter_context(patch.object(sys, "argv", ["harness", "--max-pss-growth-kb", "10"]))
                stack.enter_context(patch.object(qualification, "read_device_baseline", return_value=baseline))
                stack.enter_context(patch.object(qualification, "phase_environment_setup", return_value=True))
                for phase in (
                    "phase_baseline_capture",
                    "phase_preview_navigation",
                    "phase_surface_recreation",
                    "phase_process_rebind",
                    "phase_time_travel",
                    "phase_midnight_rollover",
                    "phase_total_pss_growth",
                    "phase_hidden_frames",
                ):
                    stack.enter_context(patch.object(qualification, phase))
                stack.enter_context(patch.object(device_layer, "sleep_screen", return_value=True))
                stack.enter_context(patch.object(device_layer, "read_screen_on", side_effect=[False, False, True]))
                stack.enter_context(
                    patch.object(device_layer, "run_adb", return_value=b"mVisible=false\nmVisible=true")
                )
                stack.enter_context(patch.object(device_layer, "get_wallpaper_pid", return_value=123))
                stack.enter_context(patch.object(device_layer, "read_process_cpu_ticks", side_effect=[100, 100]))
                stack.enter_context(patch.object(device_layer, "read_process_cpu_clock_ticks", return_value=100))
                stack.enter_context(patch.object(qualification.time, "monotonic", side_effect=[10.0, 14.0]))
                wake = stack.enter_context(
                    patch.object(device_layer, "wake_screen", return_value=wake_ok, side_effect=wake_error)
                )
                stack.enter_context(patch.object(device_layer.AdbDevice, "dismiss_keyguard"))
                stack.enter_context(patch.object(device_layer.AdbDevice, "show_home"))
                stack.enter_context(patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))))
                stack.enter_context(patch.object(device_layer, "detect_hand_angle", return_value=123.456))
                stack.enter_context(patch.object(qualification.time, "sleep"))
                restore = stack.enter_context(patch.object(qualification, "restore_device", return_value=True))
                stack.enter_context(patch.object(qualification, "phase_renderer_log_scan", return_value=True))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                error_output = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit) as exit_error:
                    qualification.main()
            self.assertEqual(exit_error.exception.code, 1)
            wake.assert_called_once_with("device")
            restore.assert_called_once()
            self.assertIn(
                "Wallpaper reported visible (mVisible=true) while the screen was confirmed off", error_output.getvalue()
            )
            if wake_error is not None:
                self.assertIn("wake probe failed", error_output.getvalue())
            elif not wake_ok:
                self.assertIn("Device wake was not confirmed", error_output.getvalue())

    def test_an_awake_screen_is_left_alone_before_a_phase(self) -> None:
        """A visible screen must not gain extra wake, keyguard, or home input."""
        failures: list[str] = []
        with (
            patch.object(device_layer, "read_screen_on", return_value=True),
            patch.object(device_layer, "wake_screen") as wake,
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
        ):
            prepared = qualification.ensure_screen_on(device_layer.AdbDevice("device"), failures)
        self.assertTrue(prepared)
        wake.assert_not_called()
        dismiss.assert_not_called()
        home.assert_not_called()
        self.assertEqual(failures, [])

    def test_a_timed_out_screen_is_woken_before_a_phase(self) -> None:
        """The display timeout can switch the screen off mid-run; the phase must repair it first."""
        failures: list[str] = []
        with (
            patch.object(device_layer, "read_screen_on", return_value=False),
            patch.object(device_layer, "wake_screen", return_value=True) as wake,
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
            patch.object(qualification.time, "sleep"),
        ):
            prepared = qualification.ensure_screen_on(device_layer.AdbDevice("device"), failures)
        self.assertTrue(prepared)
        wake.assert_called_once_with("device")
        dismiss.assert_called_once_with()
        home.assert_called_once_with()
        self.assertEqual(failures, [])

    def test_an_unconfirmed_phase_wake_fails_the_prerequisite(self) -> None:
        """An unconfirmed wake must record the failure and leave the keyguard and home alone."""
        failures: list[str] = []
        with (
            patch.object(device_layer, "read_screen_on", return_value=False),
            patch.object(device_layer, "wake_screen", return_value=False),
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
        ):
            prepared = qualification.ensure_screen_on(device_layer.AdbDevice("device"), failures)
        self.assertFalse(prepared)
        dismiss.assert_not_called()
        home.assert_not_called()
        self.assertEqual(failures, ["Screen wake was not confirmed before a phase that needs the screen on"])

    def test_time_travel_skips_the_broadcasts_when_the_screen_cannot_be_confirmed(self) -> None:
        """No time-travel evidence may be collected while the screen's visibility is unconfirmed."""
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        with (
            patch.object(qualification, "ensure_screen_on", return_value=False),
            patch.object(device_layer, "send_debug_clock_broadcast") as broadcast,
            patch.object(device_layer, "capture_frame") as capture,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_time_travel(device_layer.AdbDevice("device"), results, failures)
        broadcast.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(results, [])

    def check_surface_failure_through_main(self, module: ModuleType, readback: tuple[str | None, str | None]) -> None:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["harness", "--max-pss-growth-kb", "10"]))
            stack.enter_context(patch.object(device_layer, "run_adb"))
            stack.enter_context(patch.object(module.time, "sleep"))
            stack.enter_context(
                patch.object(device_layer, "read_display_size", side_effect=[("1080x2408", "1080x2000"), readback])
            )
            stack.enter_context(patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))))
            stack.enter_context(patch.object(device_layer, "detect_hand_angle", return_value=1.0))
            restore = stack.enter_context(patch.object(module, "restore_device", return_value=True))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            error_output = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            baseline = qualification.DeviceBaseline(
                serial="device",
                run_start_marker="date",
                physical_size="1080x2408",
                size_override=None,
                screen_was_on=True,
                night_mode="auto",
                wallpaper_pid=123,
            )
            stack.enter_context(patch.object(module, "read_device_baseline", return_value=baseline))
            stack.enter_context(patch.object(module, "phase_environment_setup", return_value=True))
            stack.enter_context(patch.object(module, "phase_renderer_log_scan", return_value=True))
            stack.enter_context(patch.object(module, "ensure_screen_on", return_value=True))
            for phase in (
                "phase_baseline_capture",
                "phase_screen_off_wake",
                "phase_preview_navigation",
                "phase_process_rebind",
                "phase_time_travel",
                "phase_midnight_rollover",
                "phase_total_pss_growth",
                "phase_hidden_frames",
            ):
                stack.enter_context(patch.object(module, phase))
            with self.assertRaises(SystemExit) as exit_error:
                module.main()
            self.assertEqual(exit_error.exception.code, 1)
            self.assertIn("not verified after surface recreation restore", error_output.getvalue())
            restore.assert_called_once()

    def check_surface_restore(self, original: str | None, readback: tuple[str | None, str | None]) -> None:
        results: list[tuple[str, str]] = []
        failures: list[str] = []
        verified = readback[0] is not None and readback[1] == original
        with (
            patch.object(device_layer, "read_display_size", side_effect=[("1080x2408", "1080x2000"), readback]),
            patch.object(device_layer, "run_adb") as run_adb,
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))) as capture,
            patch.object(device_layer, "detect_hand_angle", return_value=1.0),
            patch.object(qualification, "ensure_screen_on", return_value=True),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_surface_recreation(
                device_layer.AdbDevice("device"), original, "1080x2000", results, failures
            )
            self.assertEqual(bool(results), verified)
            self.assertEqual(bool(failures), not verified)
            self.assertEqual(capture.call_count, int(verified))
            self.assertEqual(run_adb.call_args.args[0], device_layer.size_restore_command(original))


if __name__ == "__main__":
    unittest.main()
