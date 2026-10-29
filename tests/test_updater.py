"""Unit tests for UpdateManager (no live GitHub calls).

Run with: PYTHONPATH=src python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zashterminal.utils.updater import (
    UpdateManager,
    _is_remote_newer,
    _normalize_version,
    allow_main_version_fallback,
    is_arch_family,
    is_auto_update_disabled_by_env,
)


class VersionCompareTests(unittest.TestCase):
    def test_normalize_strips_v_prefix(self):
        self.assertEqual(_normalize_version("v0.8.16"), "0.8.16")

    def test_remote_newer(self):
        self.assertTrue(_is_remote_newer("0.8.10", "0.8.16"))
        self.assertFalse(_is_remote_newer("0.8.16", "0.8.10"))
        self.assertFalse(_is_remote_newer("0.8.10", "0.8.10"))


class DistroAndEnvTests(unittest.TestCase):
    def test_arch_family(self):
        self.assertTrue(is_arch_family({"ID": "arch"}))
        self.assertTrue(is_arch_family({"ID": "cachyos", "ID_LIKE": "arch"}))
        self.assertTrue(is_arch_family({"ID": "manjaro", "ID_LIKE": "arch"}))
        self.assertFalse(is_arch_family({"ID": "fedora"}))
        self.assertFalse(is_arch_family({"ID": "ubuntu", "ID_LIKE": "debian"}))

    def test_disable_env(self):
        self.assertTrue(is_auto_update_disabled_by_env({"ZASHTERMINAL_DISABLE_AUTO_UPDATE": "1"}))
        self.assertTrue(is_auto_update_disabled_by_env({"ZASHTERMINAL_DISABLE_AUTO_UPDATE": "true"}))
        self.assertFalse(is_auto_update_disabled_by_env({}))

    def test_main_fallback_opt_in(self):
        self.assertFalse(allow_main_version_fallback({}))
        self.assertTrue(
            allow_main_version_fallback({"ZASHTERMINAL_UPDATE_ALLOW_MAIN": "1"})
        )


class UpdateManagerParseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "update_state.json"
        self.manager = UpdateManager(state_file=self.state_file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_release_payload(self):
        parsed = self.manager._parse_release_payload({"tag_name": "v0.9.0"})
        self.assertEqual(parsed["version"], "0.9.0")
        self.assertEqual(parsed["source"], "github-release")

    def test_parse_tags_prefers_first_semver(self):
        parsed = self.manager._parse_tags_payload(
            [{"name": "nightly"}, {"name": "v0.8.16"}, {"name": "v0.8.10"}]
        )
        self.assertEqual(parsed["version"], "0.8.16")
        self.assertEqual(parsed["ref"], "v0.8.16")
        self.assertEqual(parsed["source"], "github-tag")

    def test_parse_raw_config(self):
        text = 'class AppConstants:\n    APP_VERSION = "0.8.16"\n'
        parsed = self.manager._parse_raw_config_version(text)
        self.assertEqual(parsed["version"], "0.8.16")
        self.assertEqual(parsed["ref"], "main")


class UpdateManagerCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_file = Path(self.tmp.name) / "update_state.json"
        self.manager = UpdateManager(state_file=self.state_file)

    def tearDown(self):
        self.tmp.cleanup()

    def test_disabled_by_setting(self):
        result = self.manager.check_for_updates("0.8.10", enabled=False)
        self.assertEqual(result["reason"], "disabled-by-setting")

    def test_disabled_by_env(self):
        with mock.patch.dict(
            "os.environ", {"ZASHTERMINAL_DISABLE_AUTO_UPDATE": "1"}, clear=False
        ):
            result = self.manager.check_for_updates("0.8.10", enabled=True)
        self.assertEqual(result["reason"], "disabled-by-env")

    def test_fetch_order_release_then_tags_skips_main_by_default(self):
        release_resp = mock.Mock(ok=False, status_code=404)
        tags_resp = mock.Mock(ok=True)
        tags_resp.json.return_value = [{"name": "v0.8.16"}]
        main_resp = mock.Mock(ok=True)
        main_resp.text = 'APP_VERSION = "0.9.99"'

        with mock.patch(
            "zashterminal.utils.updater.requests.get",
            side_effect=[release_resp, tags_resp, main_resp],
        ) as get_mock:
            remote = self.manager._fetch_remote_version(allow_main_fallback=False)

        self.assertEqual(remote["version"], "0.8.16")
        self.assertEqual(remote["source"], "github-tag")
        self.assertEqual(get_mock.call_count, 2)

    def test_no_tags_skips_main_unless_opted_in(self):
        release_resp = mock.Mock(ok=False, status_code=404)
        tags_resp = mock.Mock(ok=True)
        tags_resp.json.return_value = [{"name": "nightly"}]
        main_resp = mock.Mock(ok=True)
        main_resp.text = 'APP_VERSION = "0.9.99"'

        with mock.patch(
            "zashterminal.utils.updater.requests.get",
            side_effect=[release_resp, tags_resp, main_resp],
        ) as get_mock:
            remote = self.manager._fetch_remote_version(allow_main_fallback=False)

        self.assertIsNone(remote)
        self.assertEqual(get_mock.call_count, 2)

        with mock.patch(
            "zashterminal.utils.updater.requests.get",
            side_effect=[release_resp, tags_resp, main_resp],
        ) as get_mock:
            remote = self.manager._fetch_remote_version(allow_main_fallback=True)

        self.assertEqual(remote["version"], "0.9.99")
        self.assertEqual(remote["source"], "github-main")
        self.assertEqual(get_mock.call_count, 3)

    def test_default_allow_main_reads_env_and_tags_http_error(self):
        """Cover None→env resolution and non-OK Tags API branches."""
        release_resp = mock.Mock(ok=False, status_code=404)
        tags_resp = mock.Mock(ok=False, status_code=500)
        main_resp = mock.Mock(ok=True)
        main_resp.text = 'APP_VERSION = "0.9.99"'

        with mock.patch.dict(
            "os.environ", {"ZASHTERMINAL_UPDATE_ALLOW_MAIN": "1"}, clear=False
        ):
            with mock.patch(
                "zashterminal.utils.updater.requests.get",
                side_effect=[release_resp, tags_resp, main_resp],
            ) as get_mock:
                # allow_main_fallback=None → allow_main_version_fallback()
                remote = self.manager._fetch_remote_version()

        self.assertEqual(remote["version"], "0.9.99")
        self.assertEqual(remote["source"], "github-main")
        self.assertEqual(get_mock.call_count, 3)

        with mock.patch(
            "zashterminal.utils.updater.requests.get",
            side_effect=[release_resp, tags_resp],
        ) as get_mock:
            remote = self.manager._fetch_remote_version(allow_main_fallback=False)

        self.assertIsNone(remote)
        self.assertEqual(get_mock.call_count, 2)

    def test_check_writes_state_and_detects_update(self):
        with mock.patch.object(
            self.manager,
            "_fetch_remote_version",
            return_value={
                "version": "0.8.16",
                "ref": "v0.8.16",
                "source": "github-tag",
            },
        ):
            result = self.manager.check_for_updates("0.8.10", enabled=True)

        self.assertTrue(result["update_available"])
        self.assertTrue(result["checked"])
        state = json.loads(self.state_file.read_text())
        self.assertEqual(state["last_remote_version"], "0.8.16")

    def test_has_prompted_allows_newer_remote(self):
        self.manager.mark_prompted_today("0.8.10")
        self.assertTrue(self.manager.has_prompted_today("0.8.10"))
        self.assertFalse(self.manager.has_prompted_today("0.8.16"))


class BuildUpdateCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = UpdateManager(
            state_file=Path(self.tmp.name) / "update_state.json"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_nixos_command(self):
        cmd = self.manager.build_update_command(
            "v0.8.16", os_release={"ID": "nixos"}
        )
        self.assertIn("nix", cmd)
        self.assertIn("zashterminal", cmd)

    def test_arch_uses_yay_when_available(self):
        cmd = self.manager.build_update_command(
            "v0.8.16",
            os_release={"ID": "cachyos", "ID_LIKE": "arch"},
            which=lambda name: "/usr/bin/yay" if name == "yay" else None,
        )
        self.assertIn("yay", cmd)
        self.assertIn("zashterminal", cmd)
        self.assertNotIn("install.sh", cmd)

    def test_arch_uses_paru_when_yay_missing(self):
        cmd = self.manager.build_update_command(
            "main",
            os_release={"ID": "arch"},
            which=lambda name: "/usr/bin/paru" if name == "paru" else None,
        )
        self.assertIn("paru", cmd)

    def test_arch_falls_back_to_install_sh(self):
        cmd = self.manager.build_update_command(
            "main",
            os_release={"ID": "arch"},
            which=lambda _name: None,
        )
        self.assertIn("install.sh", cmd)

    def test_fedora_uses_install_sh(self):
        cmd = self.manager.build_update_command(
            "main", os_release={"ID": "fedora"}
        )
        self.assertIn("install.sh", cmd)


if __name__ == "__main__":
    unittest.main()
