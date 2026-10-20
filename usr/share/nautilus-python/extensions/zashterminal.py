# -*- coding: utf-8 -*-

"""
Zashterminal - Nautilus Extension
Adds context menu options to open Zashterminal. Supports local paths,
GVFS mounts, and direct remote SSH connections.
"""

import gettext
import os
import shutil
import subprocess
from urllib.parse import urlparse

# Import 'gi' and explicitly require GTK and Nautilus versions.
import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GObject, Nautilus

# --- Internationalization (i18n) Setup ---
APP_NAME = "zashterminal"

try:
    gettext.bindtextdomain(APP_NAME, "/usr/share/locale")
    gettext.textdomain(APP_NAME)
except Exception as e:
    print(f"Zashterminal Extension: Could not set up localization: {e}")

_ = gettext.gettext

# --- Constants ---
TERMINAL_EXECUTABLE = shutil.which("zashterminal")
REMOTE_URI_SCHEMES = {"sftp", "ssh"}


class ZashterminalExtension(GObject.GObject, Nautilus.MenuProvider):
    """
    Provides context menu items for opening directories in Zashterminal.
    """

    def _launch(self, command: list[str]):
        """
        Launches a command, ensuring the environment is passed for Wayland focus.
        """
        if not TERMINAL_EXECUTABLE:
            print("Zashterminal Error: 'zashterminal' executable not found.")
            return
        try:
            # The key is 'env=os.environ'. This passes the XDG_ACTIVATION_TOKEN
            # to the new process, allowing it to gain focus on Wayland.
            subprocess.Popen(command, env=os.environ)
        except Exception as e:
            print(f"Error launching '{' '.join(command)}': {e}")

    def _get_local_path(self, file_info: Nautilus.FileInfo) -> str | None:
        """
        Safely gets a local filesystem path from a FileInfo object.
        """
        try:
            return Gio.File.new_for_uri(file_info.get_uri()).get_path()
        except Exception:
            return None

    def _launch_local_session(self, menu_item: Nautilus.MenuItem, files: list[Nautilus.FileInfo]):
        """
        Opens Zashterminal in the specified local or GVFS directory path.
        """
        file = files[0]
        local_path = self._get_local_path(file)
        if not local_path:
            print(f"Zashterminal Error: Could not get local path for {file.get_uri()}")
            return
        
        cmd = [TERMINAL_EXECUTABLE, "--working-directory", local_path]
        self._launch(cmd)

    def _launch_remote_ssh_session(self, menu_item: Nautilus.MenuItem, files: list[Nautilus.FileInfo]):
        """
        Parses a remote URI and launches Zashterminal with a direct SSH connection.
        """
        file = files[0]
        uri = file.get_uri()
        try:
            parsed_uri = urlparse(uri)
            hostname = parsed_uri.hostname
            if not hostname:
                raise ValueError("Hostname is missing from the URI.")

            target = hostname
            if parsed_uri.username:
                target = f"{parsed_uri.username}@{hostname}"

            if parsed_uri.port:
                target = f"{target}:{parsed_uri.port}"

            if parsed_uri.path and parsed_uri.path != "/":
                target = f"{target}{parsed_uri.path}"

            cmd = [TERMINAL_EXECUTABLE, "--ssh", target]
            self._launch(cmd)
            print(f"Zashterminal: Launched SSH session to {target}")

        except Exception as e:
            print(f"Error parsing URI '{uri}' or launching SSH session: {e}")

    def _get_menu_items(self, files: list[Nautilus.FileInfo]) -> list[Nautilus.MenuItem]:
        """
        Core logic for generating menu items based on the file type.
        """
        if not TERMINAL_EXECUTABLE or len(files) != 1:
            return []

        file = files[0]
        if not file.is_directory():
            return []

        is_remote = file.get_uri_scheme() in REMOTE_URI_SCHEMES
        local_path = self._get_local_path(file)
        menu_items = []

        # Case 1: Remote location (e.g., sftp://)
        if is_remote:
            # Option A: Open a direct SSH connection
            ssh_item = Nautilus.MenuItem(
                name="Zashterminal::OpenRemoteSSH",
                label=_("Open in Zashterminal (SSH)"),
                tip=_("Connect to {} via a new SSH session").format(file.get_uri()),
            )
            ssh_item.connect("activate", self._launch_remote_ssh_session, files)
            menu_items.append(ssh_item)

            # Option B: Open the local GVFS mount point, if it exists
            if local_path:
                gvfs_item = Nautilus.MenuItem(
                    name="Zashterminal::OpenGVFS",
                    label=_("Open in Zashterminal"),
                    tip=_("Open the local mount point {}").format(local_path),
                )
                gvfs_item.connect("activate", self._launch_local_session, files)
                menu_items.append(gvfs_item)

        # Case 2: Local location (file://)
        elif local_path:
            local_item = Nautilus.MenuItem(
                name="Zashterminal::OpenLocal",
                label=_("Open in Zashterminal"),
                tip=_("Open {} in Zashterminal").format(file.get_name()),
            )
            local_item.connect("activate", self._launch_local_session, files)
            menu_items.append(local_item)

        return menu_items

    def get_file_items(self, files: list[Nautilus.FileInfo]) -> list[Nautilus.MenuItem]:
        """Returns menu items for a single selected directory."""
        return self._get_menu_items(files)

    def get_background_items(self, current_folder: Nautilus.FileInfo) -> list[Nautilus.MenuItem]:
        """Returns menu items for the background of the current directory."""
        return self._get_menu_items([current_folder])
