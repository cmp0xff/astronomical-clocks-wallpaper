# SPDX-License-Identifier: Apache-2.0
"""Host-only regression checks; these tests establish no physical-device behavior."""

import argparse
import contextlib
import io
import math
import struct
import subprocess
import sys
import unittest
from types import ModuleType
from typing import Final
from unittest.mock import MagicMock, patch

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

FIXTURE_HAND_ANGLES: Final = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 7.5, 180.0]

DUPLICATED_CONSTANT_NAMES: Final = (
    "ADB_TIMEOUT_SECONDS",
    "ADB_RESTORE_TIMEOUT_SECONDS",
    "ANGLE_TOLERANCE_DEG",
    "COARSE_BIN_COUNT",
    "COARSE_BIN_WIDTH_DEG",
    "DARK_HAND_MIN_RED_MINUS_BLUE",
    "DARK_HAND_RGB_BOUNDS",
    "DARK_RIM_RGB",
    "DEBUG_ACTION",
    "DEBUG_CLOCK_RESET_EXTRAS",
    "DEBUG_CLOCK_RESET_MESSAGE",
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
            "phase_total_pss_growth",
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
    """Phase 7's split between an inconclusive scan and a failed collection."""

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

    def test_log_collection_failure_makes_the_whole_run_exit_nonzero(self) -> None:
        """
        Fail the whole run when the log scan cannot be collected.

        The reported defect: Phase 7 sits after the try/finally, so a collection error there was
        reported as an inconclusive pass and the run exited 0 even though no log was ever read.
        Every other phase is made to succeed, so the exit code turns on the log scan alone.
        """

        def adb_side_effect(command: list[str], **_kwargs: object) -> bytes:
            if command[:2] == ["logcat", "-d"]:
                raise subprocess.CalledProcessError(1, command, stderr=b"logcat unavailable")
            if "meminfo" in command:
                return b"TOTAL 1000 0 0\n"
            if "CHANGE_LIVE_WALLPAPER" in command:
                return b"Status: ok\n"
            return b""

        with (
            patch.object(sys, "argv", ["device_qualification.py", "--max-pss-growth-kb", "10"]),
            patch.object(device_layer, "select_target_serial", return_value="device"),
            patch.object(device_layer, "run_adb", side_effect=adb_side_effect),
            patch.object(device_layer, "read_display_size", return_value=("1080x2408", None)),
            patch.object(device_layer, "read_screen_on", return_value=True),
            patch.object(device_layer, "read_night_mode", return_value="auto"),
            patch.object(device_layer, "read_keyguard_locked", return_value=False),
            patch.object(
                device_layer,
                "get_wallpaper_pid",
                side_effect=[FIXTURE_START_PID, FIXTURE_START_PID, FIXTURE_START_PID, FIXTURE_REBOUND_PID],
            ),
            patch.object(device_layer, "send_debug_clock_broadcast", return_value=True),
            patch.object(qualification, "restore_device", return_value=True),
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))),
            patch.object(device_layer, "is_dark_palette", return_value=False),
            patch.object(device_layer, "detect_hand_angle", side_effect=FIXTURE_HAND_ANGLES),
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            qualification.main()

        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("Renderer log scan could not be collected", error_output.getvalue())


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
        with patch.object(device_layer, "run_adb", return_value=raw):
            self.assertEqual(device_layer.capture_frame("device"), (2, 2, bytes(2 * 2 * BYTES_PER_PIXEL)))

    @patch.object(device_layer, "run_adb", return_value=bytes(8))
    def test_capture_frame_rejects_a_truncated_header(self, _run_adb: MagicMock) -> None:
        with self.assertRaisesRegex(RuntimeError, "payload too small"):
            device_layer.capture_frame("device")


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
            patch.object(device_layer, "wake_screen", return_value=False) as wake,
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
            patch.object(device_layer, "capture_frame") as capture,
            patch.object(qualification.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            qualification.phase_screen_off_wake(device_layer.AdbDevice("device"), results, failures)
        read_screen.assert_called_once_with("device")
        wake.assert_called_once_with("device")
        dismiss.assert_not_called()
        home.assert_not_called()
        capture.assert_not_called()
        self.assertEqual(failures, ["Device wake was not confirmed after the screen-off interval"])
        self.assertEqual(results, [])

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
                "phase_total_pss_growth",
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
