"""Flatpak command construction for the Command Manager."""

from __future__ import annotations

import unittest

from zashterminal.data.command_manager_models import (
    build_flatpak_command,
    get_builtin_commands,
)
from zashterminal.utils.translation_utils import _


class FlatpakCommandTests(unittest.TestCase):
    def test_builtin_flatpak_is_registered(self):
        command = next(cmd for cmd in get_builtin_commands() if cmd.id == "builtin_flatpak")
        self.assertEqual(command.category, _("System"))
        self.assertEqual(
            [field.id for field in command.form_fields],
            ["action", "application", "remote", "user_scope", "assume_yes"],
        )

    def test_list_installed(self):
        self.assertEqual(build_flatpak_command({"action": "list"}), "flatpak list")

    def test_user_scope_is_a_global_flag(self):
        command = build_flatpak_command({"action": "list --app", "user_scope": True})
        self.assertEqual(command, "flatpak --user list --app")

    def test_install_with_remote_and_confirmation(self):
        command = build_flatpak_command({
            "action": "install",
            "application": "org.mozilla.firefox",
            "remote": "flathub",
            "assume_yes": True,
        })
        self.assertEqual(
            command,
            "flatpak -y install flathub org.mozilla.firefox",
        )

    def test_update_one_app_for_the_user(self):
        command = build_flatpak_command({
            "action": "update",
            "application": "org.mozilla.firefox",
            "user_scope": True,
            "assume_yes": True,
        })
        self.assertEqual(command, "flatpak --user -y update org.mozilla.firefox")

    def test_update_all_without_application(self):
        self.assertEqual(build_flatpak_command({"action": "update"}), "flatpak update")

    def test_search_quotes_the_query(self):
        command = build_flatpak_command({
            "action": "search",
            "application": "video editor",
        })
        self.assertEqual(command, "flatpak search 'video editor'")

    def test_assume_yes_is_ignored_for_queries(self):
        command = build_flatpak_command({
            "action": "info",
            "application": "org.mozilla.firefox",
            "assume_yes": True,
        })
        self.assertEqual(command, "flatpak info org.mozilla.firefox")

    def test_add_flathub_remote(self):
        command = build_flatpak_command({
            "action": "remote-add-flathub",
            "user_scope": True,
        })
        self.assertEqual(
            command,
            "flatpak --user remote-add --if-not-exists flathub "
            "https://dl.flathub.org/repo/flathub.flatpakrepo",
        )

    def test_browse_remote_and_remove_unused(self):
        self.assertEqual(
            build_flatpak_command({"action": "remote-ls", "remote": "flathub"}),
            "flatpak remote-ls --app flathub",
        )
        self.assertEqual(
            build_flatpak_command({"action": "remote-ls"}),
            "flatpak remote-ls --app",
        )
        self.assertEqual(
            build_flatpak_command({"action": "uninstall --unused", "assume_yes": True}),
            "flatpak -y uninstall --unused",
        )

    def test_empty_values_list_installed_apps(self):
        self.assertEqual(build_flatpak_command(None), "flatpak list")
        self.assertEqual(build_flatpak_command({"action": "   "}), "flatpak list")

    def test_install_omits_blank_remote_or_application(self):
        self.assertEqual(
            build_flatpak_command({"action": "install", "application": "org.foo.Bar"}),
            "flatpak install org.foo.Bar",
        )
        self.assertEqual(
            build_flatpak_command({"action": "install", "remote": "flathub"}),
            "flatpak install flathub",
        )
        self.assertEqual(build_flatpak_command({"action": "install"}), "flatpak install")

    def test_uninstall_update_and_run_refs(self):
        self.assertEqual(
            build_flatpak_command({"action": "uninstall", "application": "org.foo.Bar"}),
            "flatpak uninstall org.foo.Bar",
        )
        self.assertEqual(build_flatpak_command({"action": "uninstall"}), "flatpak uninstall")
        self.assertEqual(build_flatpak_command({"action": "run"}), "flatpak run")
        self.assertEqual(
            build_flatpak_command({
                "action": "override --show",
                "application": "org.foo.Bar",
            }),
            "flatpak override --show org.foo.Bar",
        )

    def test_quotes_remote_names_with_spaces(self):
        command = build_flatpak_command({
            "action": "install",
            "remote": "my remote",
            "application": "org.foo.Bar",
        })
        self.assertEqual(command, "flatpak install 'my remote' org.foo.Bar")

    def test_queries_do_not_receive_confirmation_flag(self):
        command = build_flatpak_command({
            "action": "repair",
            "assume_yes": True,
            "user_scope": False,
        })
        self.assertEqual(command, "flatpak repair")
        self.assertEqual(
            build_flatpak_command({"action": "history"}),
            "flatpak history",
        )


if __name__ == "__main__":
    unittest.main()
