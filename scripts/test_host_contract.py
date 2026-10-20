# SPDX-License-Identifier: Apache-2.0
"""Host-only regression checks for the checked-in Kotlin-to-host contract; no device behavior."""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Final

from host import contract
from host.device import frame as device_frame

CONTRACT_FIELDS: Final = frozenset(
    {
        "debug_action",
        "debug_extras",
        "debug_package_name",
        "display_settings",
        "geometry",
        "location_store",
        "logs",
        "palette",
        "service_class_name",
        "service_name",
    }
)


class HostContractTest(unittest.TestCase):
    """The checked-in JSON loads into frozen sections and rejects malformed input."""

    def test_loaded_contract_matches_checked_in_values(self) -> None:
        self.assertEqual(contract.load_host_contract(), contract.CONTRACT)
        raw = json.loads(contract.CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(frozenset(raw), CONTRACT_FIELDS)
        self.assertEqual(contract.CONTRACT.debug_extras.reset, raw["debug_extras"]["reset"])
        self.assertEqual(
            contract.CONTRACT.geometry.outer_radius,
            raw["geometry"]["outer_radius"],
        )
        self.assertEqual(contract.CONTRACT.palette.dark_rim_rgb, tuple(raw["palette"]["dark_rim_rgb"]))
        self.assertEqual(contract.CONTRACT.location_store.sources, frozenset(raw["location_store"]["sources"]))

    def test_derived_names_follow_the_loaded_contract(self) -> None:
        self.assertEqual(
            contract.SERVICE_NAME,
            f"{contract.PACKAGE_NAME}/{contract.CONTRACT.service_class_name}",
        )
        self.assertEqual(
            contract.LOCATION_PREFS_PATH,
            f"/data/data/{contract.PACKAGE_NAME}/shared_prefs/{contract.CONTRACT.location_store.preferences_name}.xml",
        )

    def test_debug_message_helpers_use_contract_fragments(self) -> None:
        self.assertEqual(
            contract.debug_offset_message(1800000),
            "Debug clock offset set to 1800000ms",
        )
        self.assertEqual(
            contract.debug_fixed_instant_message("2026-06-20T21:15:00Z"),
            "Debug clock fixed to instant: 2026-06-20T21:15:00Z",
        )

    def test_vision_bounds_include_contract_hand_and_rim_colors(self) -> None:
        palette = contract.CONTRACT.palette
        self.assertTrue(device_frame.is_dark_hand_pixel(*palette.dark_hand_rgb))
        self.assertTrue(device_frame.is_light_hand_pixel(*palette.light_hand_rgb))
        self.assertEqual(device_frame.nearest_rim_rgb(palette.dark_rim_rgb), palette.dark_rim_rgb)
        self.assertEqual(device_frame.nearest_rim_rgb(palette.light_rim_rgb), palette.light_rim_rgb)

    def test_load_host_contract_rejects_malformed_json(self) -> None:
        raw = json.loads(contract.CONTRACT_PATH.read_text(encoding="utf-8"))
        bad_palette = {**raw["palette"], "dark_rim_rgb": [28, 44, 300]}
        cases = (
            ("root", [], TypeError, "root"),
            ("empty package", {**raw, "debug_package_name": ""}, ValueError, "debug_package_name"),
            ("bad channel", {**raw, "palette": bad_palette}, ValueError, "dark_rim_rgb"),
            ("bad service", {**raw, "service_name": "other/service"}, ValueError, "service_name"),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, payload, expected, field in cases:
                with self.subTest(name=name):
                    path = Path(tmpdir) / f"{name.replace(' ', '_')}.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(expected, field):
                        contract.load_host_contract(path)


if __name__ == "__main__":
    unittest.main()
