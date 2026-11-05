"""Settings contracts for compositor blur and independent KWin kernel levels."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from zashterminal.settings.manager import SettingsManager, SettingsValidator
from zashterminal.utils.exceptions import ConfigValidationError


class BackgroundBlurSettingTests(unittest.TestCase):
    def test_only_boolean_values_can_be_saved(self):
        manager = SettingsManager.__new__(SettingsManager)
        manager.validator = SettingsValidator()
        for value in (False, True):
            with self.subTest(value=value):
                manager._validate_setting_value("background_blur", value)
                errors = manager.validator.validate_settings_structure(
                    {"color_scheme": 0, "font": "Monospace 10", "shortcuts": {},
                     "background_blur": value}, 30,
                )
                self.assertEqual(errors, [])
        for value in (0, 1, 25, 100, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ConfigValidationError):
                    manager._validate_setting_value("background_blur", value)
                errors = manager.validator.validate_settings_structure(
                    {"color_scheme": 0, "font": "Monospace 10", "shortcuts": {},
                     "background_blur": value}, 30,
                )
                self.assertTrue(any("background_blur" in error for error in errors))

    def test_saved_intensities_migrate_without_changing_transparency(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SettingsManager.__new__(SettingsManager)
            manager.settings_file = Path(directory) / "settings.json"
            manager.logger = Mock()
            manager._defaults = {"background_blur": False}
            for old, enabled in ((0, False), (8, True), (100.0, True), (-1, False), (101, False)):
                with self.subTest(old=old):
                    manager.settings_file.write_text(json.dumps({
                        "background_blur": old, "transparency": 37,
                    }), encoding="utf-8")
                    settings = manager._load_settings_safe()
                    self.assertIs(settings["background_blur"], enabled)
                    self.assertEqual(settings["transparency"], 37)
                    manager.settings_file.write_text(json.dumps(settings), encoding="utf-8")
                    self.assertIs(manager._load_settings_safe()["background_blur"], enabled)

    def test_strength_accepts_only_supported_integer_kernel_levels(self):
        manager = SettingsManager.__new__(SettingsManager)
        manager.validator = SettingsValidator()
        for value in (1, 5, 15):
            manager._validate_setting_value("background_blur_strength", value)
        for value in (0, 16, -1, True, 1.5, "5", None):
            with self.subTest(value=value):
                with self.assertRaises(ConfigValidationError):
                    manager._validate_setting_value("background_blur_strength", value)
                errors = manager.validator.validate_settings_structure(
                    {"color_scheme": 0, "font": "Monospace 10", "shortcuts": {},
                     "background_blur_strength": value}, 30,
                )
                self.assertTrue(any("background_blur_strength" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
