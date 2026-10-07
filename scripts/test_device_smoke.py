# SPDX-License-Identifier: Apache-2.0
"""Host-only regression checks; these tests establish no physical-device behavior."""

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

HAND_UP_BEARING_DEG: Final = 0.0

HAND_DOWN_BEARING_DEG: Final = 180.0

DARK_HAND_RGB: Final = (244, 229, 184)

LIGHT_HAND_RGB: Final = (78, 52, 27)


class SmokeHarnessHardeningTest(unittest.TestCase):
    """The smoke harness's own false-pass paths, closed alongside the qualification ones."""

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", None))
    @patch.object(device_layer, "count_service_log_messages", return_value=0)
    @patch.object(device_layer, "run_adb")
    def test_restore_fails_when_the_clock_reset_is_never_logged(
        self, _run_adb: MagicMock, _count: MagicMock, _size: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        # `am broadcast` exits 0 even when nothing consumes it, so an unlogged reset is not a reset.
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(device_smoke.restore_device(device_layer.AdbDevice("device"), None, screen_was_on=True))

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", "720x1280"))
    @patch.object(device_layer, "count_service_log_messages", side_effect=[0, 1])
    @patch.object(device_layer, "run_adb")
    def test_restore_fails_when_the_display_size_does_not_return(
        self, _run_adb: MagicMock, _count: MagicMock, _size: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(device_smoke.restore_device(device_layer.AdbDevice("device"), None, screen_was_on=True))

    @patch.object(device_layer, "read_keyguard_locked", return_value=False)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=(None, None))
    @patch.object(device_layer, "count_service_log_messages", side_effect=[0, 1])
    @patch.object(device_layer, "run_adb")
    def test_restore_with_an_unreadable_display_size_is_not_reported_as_success(
        self, _run_adb: MagicMock, _count: MagicMock, _size: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        # With no override to compare, an unreadable readback collapsed to None == None and passed.
        with contextlib.redirect_stderr(io.StringIO()) as error_output:
            self.assertFalse(device_smoke.restore_device(device_layer.AdbDevice("device"), None, screen_was_on=True))
        self.assertIn("could not read the display size after restore", error_output.getvalue())

    def test_restore_runs_display_then_clock_then_sleep(self) -> None:
        """Pin the documented restore order: size first, then the clock reset, then sleep."""
        device = device_layer.AdbDevice("device")
        order = MagicMock()
        order.run_restore_command.return_value = True
        order.confirm_virtual_clock_reset.return_value = True
        order.sleep_screen.return_value = True
        with (
            patch.object(device_layer, "run_restore_command", order.run_restore_command),
            patch.object(device_layer, "confirm_virtual_clock_reset", order.confirm_virtual_clock_reset),
            patch.object(device_layer, "sleep_screen", order.sleep_screen),
            patch.object(device_smoke, "verify_restored_state", return_value=True),
        ):
            self.assertTrue(device_smoke.restore_device(device, None, screen_was_on=False))
        self.assertEqual(
            [call[0] for call in order.mock_calls],
            ["run_restore_command", "confirm_virtual_clock_reset", "sleep_screen"],
        )

    @patch.object(device_layer, "detect_hand_angle", return_value=1.0)
    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", None))
    @patch.object(device_layer, "run_adb")
    def test_recreation_reports_an_override_that_never_took_effect(
        self, _run_adb: MagicMock, _size: MagicMock, _capture: MagicMock, _angle: MagicMock
    ) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            recreation = device_smoke.step_recreate_surface(device_layer.AdbDevice("device"), None, "1080x2000")
        self.assertFalse(recreation.override_was_active)
        self.assertEqual(recreation.hand_angle, 1.0)

    def test_collect_failures_flags_an_override_that_never_took_effect(self) -> None:
        outcome = device_smoke.SmokeOutcome(
            initial_reset_confirmed=True,
            measurement=None,
            recreation=device_smoke.SurfaceRecreation(
                override_was_active=False, restore_was_verified=True, hand_angle=1.0
            ),
            warnings=[],
        )
        failures = device_smoke.collect_failures(outcome, restored=True)
        self.assertIn("display-size override was not active as requested", failures)

    @patch.object(device_layer, "run_adb")
    def test_capture_frame_rejects_a_truncated_payload(self, run_adb: MagicMock) -> None:
        run_adb.return_value = struct.pack("<IIII", 2, 2, 1, 0) + bytes(15)
        with self.assertRaisesRegex(RuntimeError, "Unexpected screencap payload size"):
            device_layer.capture_frame("device")

    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=(None, None))
    @patch.object(device_layer, "select_target_serial", return_value="device")
    @patch.object(device_layer, "run_adb", return_value=b"")
    def test_an_unreadable_display_size_stops_the_run_before_any_mutation(
        self, _run_adb: MagicMock, _select: MagicMock, _size: MagicMock, _screen: MagicMock
    ) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            device_smoke.read_baseline(None)
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("could not read the physical display size", error_output.getvalue())

    @patch.object(device_layer, "read_keyguard_locked", return_value=True)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", None))
    @patch.object(device_layer, "select_target_serial", return_value="device")
    @patch.object(device_layer, "run_adb", return_value=b"")
    def test_a_locked_keyguard_stops_the_run_before_any_mutation(
        self, run_adb: MagicMock, _select: MagicMock, _size: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            device_smoke.read_baseline(None)
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("device is locked", error_output.getvalue())
        # read_baseline only reads device state before failing; it never wakes, dismisses the
        # keyguard, or navigates home. A locked device must never be mutated on the way to refusal.
        self.assertFalse(
            any("KEYCODE_WAKEUP" in call_args.args[0] for call_args in run_adb.call_args_list),
        )

    @patch.object(device_layer, "read_keyguard_locked", return_value=None)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", None))
    @patch.object(device_layer, "select_target_serial", return_value="device")
    @patch.object(device_layer, "run_adb", return_value=b"")
    def test_an_unreadable_keyguard_state_stops_the_run_before_any_mutation(
        self, _run_adb: MagicMock, _select: MagicMock, _size: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            device_smoke.read_baseline(None)
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("could not read the keyguard state", error_output.getvalue())

    @patch.object(device_layer, "read_keyguard_locked", return_value=True)
    @patch.object(device_layer, "read_screen_on", return_value=True)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", None))
    @patch.object(device_layer, "run_adb")
    def test_a_keyguard_that_reappears_fails_restore_even_when_everything_else_matches(
        self, _run_adb: MagicMock, _size: MagicMock, _screen: MagicMock, _keyguard: MagicMock
    ) -> None:
        with (
            patch.object(device_layer, "count_service_log_messages", side_effect=[0, 1]),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
        ):
            self.assertFalse(device_smoke.restore_device(device_layer.AdbDevice("device"), None, screen_was_on=True))
        self.assertIn("not confirmed unlocked", error_output.getvalue())

    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "detect_hand_angle", return_value=0.0)
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=False)
    @patch.object(device_layer, "run_adb")
    def test_an_unconfirmed_30m_offset_is_not_measured(
        self, _run_adb: MagicMock, _send: MagicMock, _angle: MagicMock, _capture: MagicMock
    ) -> None:
        # The shared confirmation never accepts the offset broadcast, so the measurement must come back
        # unmeasurable rather than reporting a zero-delta advance against an unconfirmed clock.
        with contextlib.redirect_stderr(io.StringIO()) as error_output:
            measurement = device_smoke.step_time_travel(device_layer.AdbDevice("device"))
        self.assertIsNone(measurement)
        self.assertIn("offset was not confirmed", error_output.getvalue())

    @patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16)))
    @patch.object(device_layer, "detect_hand_angle", return_value=0.0)
    @patch.object(device_layer, "send_debug_clock_broadcast", return_value=True)
    @patch.object(device_layer, "run_adb")
    def test_the_smoke_offset_uses_the_shared_broadcast_confirmation(
        self, _run_adb: MagicMock, broadcast: MagicMock, _angle: MagicMock, _capture: MagicMock
    ) -> None:
        """The offset must go through the shared retry-based confirmation, not a private single read."""
        with contextlib.redirect_stdout(io.StringIO()):
            measurement = device_smoke.step_time_travel(device_layer.AdbDevice("device"))
        broadcast.assert_called_once_with(
            "device", ["--el", "offset_minutes", "30"], "Debug clock offset set to 1800000ms"
        )
        self.assertIsNotNone(measurement)

    def test_a_failed_wake_skips_the_reset_and_dependent_steps(self) -> None:
        """A wake that is not confirmed must stop the step before the reset and the navigation."""
        with (
            patch.object(device_layer, "wake_screen", return_value=False) as wake,
            patch.object(device_layer.AdbDevice, "dismiss_keyguard") as dismiss,
            patch.object(device_layer.AdbDevice, "show_home") as home,
            patch.object(device_layer, "confirm_virtual_clock_reset") as reset,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
        ):
            confirmed = device_smoke.step_reset_clock_and_show_home(device_layer.AdbDevice("device"))
        self.assertFalse(confirmed)
        wake.assert_called_once_with("device")
        dismiss.assert_not_called()
        home.assert_not_called()
        reset.assert_not_called()
        self.assertIn("wake was not confirmed", error_output.getvalue())

    @patch.object(device_smoke, "step_recreate_surface")
    @patch.object(device_smoke, "step_time_travel")
    @patch.object(device_smoke, "step_reset_clock_and_show_home", return_value=False)
    @patch.object(device_smoke, "restore_device", return_value=True)
    @patch.object(device_smoke, "scan_renderer_log", return_value=[])
    @patch.object(device_smoke, "read_baseline")
    def test_an_unconfirmed_initial_reset_skips_time_travel_and_recreation_but_still_restores(
        self,
        read_baseline: MagicMock,
        _scan: MagicMock,
        restore_device: MagicMock,
        _reset: MagicMock,
        step_time_travel: MagicMock,
        step_recreate_surface: MagicMock,
    ) -> None:
        read_baseline.return_value = device_smoke.SmokeBaseline(
            serial="device",
            log_start="10-06 12:00:00.000",
            physical_size="1080x2408",
            size_override=None,
            screen_was_on=True,
        )
        with (
            patch.object(sys, "argv", ["device_smoke.py"]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as exit_error,
        ):
            device_smoke.main()
        self.assertEqual(exit_error.exception.code, 1)
        step_time_travel.assert_not_called()
        step_recreate_surface.assert_not_called()
        restore_device.assert_called_once()

    @patch.object(device_layer, "read_screen_on", return_value=None)
    @patch.object(device_layer, "read_display_size", return_value=("1080x2408", None))
    @patch.object(device_layer, "select_target_serial", return_value="device")
    @patch.object(device_layer, "run_adb", return_value=b"")
    def test_an_unreadable_screen_state_stops_the_run_before_any_mutation(
        self, _run_adb: MagicMock, _select: MagicMock, _size: MagicMock, _screen: MagicMock
    ) -> None:
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as error_output,
            self.assertRaises(SystemExit) as exit_error,
        ):
            device_smoke.read_baseline(None)
        self.assertEqual(exit_error.exception.code, 1)
        self.assertIn("could not read the initial screen state", error_output.getvalue())

    def test_an_empty_log_scan_is_reported_as_inconclusive_not_as_zero_warnings(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            device_smoke.finish_run([], log_scan_inconclusive=True)
        self.assertIn("inconclusive", output.getvalue())

    def test_finish_run_exits_nonzero_when_a_check_failed(self) -> None:
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as exit_error,
        ):
            device_smoke.finish_run(["a failed check"], log_scan_inconclusive=True)
        self.assertEqual(exit_error.exception.code, 1)


class PaletteProbeTest(unittest.TestCase):
    """The civil-scale rim probe that classifies the visible palette."""

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

    def test_surface_phase_verifies_restore_before_capturing(self) -> None:
        for original in (None, "720x1280"):
            for readback in (("1080x2408", original), ("1080x2408", "1080x2000"), (None, None)):
                with self.subTest(original=original, readback=readback):
                    self.check_surface_restore(original, readback)

    def test_surface_failure_survives_successful_final_cleanup(self) -> None:
        for readback in (("1080x2408", "1080x2000"), (None, None)):
            with self.subTest(readback=readback):
                self.check_surface_failure_through_main(device_smoke, readback)

    def check_surface_failure_through_main(self, module: ModuleType, readback: tuple[str | None, str | None]) -> None:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["harness"]))
            stack.enter_context(patch.object(device_layer, "run_adb"))
            stack.enter_context(patch.object(device_smoke.time, "sleep"))
            stack.enter_context(
                patch.object(device_layer, "read_display_size", side_effect=[("1080x2408", "1080x2000"), readback])
            )
            stack.enter_context(patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))))
            stack.enter_context(patch.object(device_layer, "detect_hand_angle", return_value=1.0))
            restore = stack.enter_context(patch.object(module, "restore_device", return_value=True))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            error_output = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            smoke_baseline = device_smoke.SmokeBaseline(
                serial="device", log_start="date", physical_size="1080x2408", size_override=None, screen_was_on=True
            )
            stack.enter_context(patch.object(module, "read_baseline", return_value=smoke_baseline))
            stack.enter_context(patch.object(module, "step_reset_clock_and_show_home", return_value=True))
            stack.enter_context(
                patch.object(module, "step_time_travel", return_value=device_smoke.AdvanceMeasurement(7.5, 0.0))
            )
            stack.enter_context(patch.object(module, "scan_renderer_log", return_value=[]))
            with self.assertRaises(SystemExit) as exit_error:
                module.main()
            self.assertEqual(exit_error.exception.code, 1)
            self.assertIn("not verified after surface recreation restore", error_output.getvalue())
            restore.assert_called_once()

    def check_surface_restore(self, original: str | None, readback: tuple[str | None, str | None]) -> None:
        failures: list[str] = []
        verified = readback[0] is not None and readback[1] == original
        with (
            patch.object(device_layer, "read_display_size", side_effect=[("1080x2408", "1080x2000"), readback]),
            patch.object(device_layer, "run_adb") as run_adb,
            patch.object(device_layer, "capture_frame", return_value=(2, 2, bytes(16))) as capture,
            patch.object(device_layer, "detect_hand_angle", return_value=1.0),
            patch.object(device_smoke.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            recreation = device_smoke.step_recreate_surface(device_layer.AdbDevice("device"), original, "1080x2000")
            self.assertEqual(recreation.restore_was_verified, verified)
            outcome = device_smoke.SmokeOutcome(
                initial_reset_confirmed=True,
                measurement=device_smoke.AdvanceMeasurement(7.5, 0.0),
                recreation=recreation,
                warnings=[],
            )
            failures = device_smoke.collect_failures(outcome, restored=True)
            self.assertEqual(bool(failures), not verified)
            self.assertEqual(capture.call_count, int(verified))
            self.assertEqual(run_adb.call_args.args[0], device_layer.size_restore_command(original))


class SmokeRendererLogTest(unittest.TestCase):
    """Collection failures preserve cleanup, measured rows, and a nonzero verdict."""

    def test_collection_errors_fail_the_run_after_cleanup(self) -> None:
        for error in (subprocess.TimeoutExpired("adb", 1), subprocess.CalledProcessError(1, "adb"), OSError("offline")):
            with self.subTest(error=type(error).__name__):
                output, errors = self.check_log_outcome(error, restored=True)
                self.assertIn("FAILED: renderer log collection failed", output)
                self.assertIn("FAIL: renderer log collection failed", errors)
                self.assertNotIn("inconclusive", output.lower())

    def test_successful_empty_scan_is_inconclusive(self) -> None:
        output, errors = self.check_log_outcome(None, restored=True)
        self.assertIn("Smoke test passed; renderer log scan was inconclusive.", output)
        self.assertEqual(errors, "")

    def test_collection_failure_retains_a_cleanup_failure(self) -> None:
        output, errors = self.check_log_outcome(OSError("offline"), restored=False)
        self.assertIn("FAILED: renderer log collection failed", output)
        self.assertIn("device state was not fully restored", errors)

    def check_log_outcome(self, error: BaseException | None, *, restored: bool) -> tuple[str, str]:
        baseline = device_smoke.SmokeBaseline("device", "date", "1080x2408", None, screen_was_on=True)
        recreation = device_smoke.SurfaceRecreation(override_was_active=True, restore_was_verified=True, hand_angle=1.0)
        with (
            patch.object(sys, "argv", ["device_smoke.py"]),
            patch.object(device_smoke, "read_baseline", return_value=baseline),
            patch.object(device_smoke, "step_reset_clock_and_show_home", return_value=True),
            patch.object(device_smoke, "step_time_travel", return_value=device_smoke.AdvanceMeasurement(7.5, 0.0)),
            patch.object(device_smoke, "step_recreate_surface", return_value=recreation),
            patch.object(device_smoke, "restore_device", return_value=restored) as restore,
            patch.object(device_layer, "run_adb", side_effect=error, return_value=b"--------- beginning of main\n"),
            contextlib.redirect_stdout(io.StringIO()) as output,
            contextlib.redirect_stderr(io.StringIO()) as errors,
        ):
            if error is not None or not restored:
                with self.assertRaises(SystemExit) as exit_error:
                    device_smoke.main()
                self.assertEqual(exit_error.exception.code, 1)
            else:
                device_smoke.main()
        restore.assert_called_once()
        self.assertEqual(restore.call_args.args[0].serial, "device")
        self.assertIsNone(restore.call_args.args[1])
        self.assertEqual(restore.call_args.kwargs, {"screen_was_on": True})
        self.assertIn("Hand advanced 7.500°", output.getvalue())
        self.assertIn("hand drawn afterwards", output.getvalue())
        return output.getvalue(), errors.getvalue()


if __name__ == "__main__":
    unittest.main()
