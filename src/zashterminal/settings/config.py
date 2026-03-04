# zashterminal/settings/config.py

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

# Note: GTK/Pango imports are done lazily in functions that need them
# to avoid slow startup when only ConfigPaths is needed
#
#
#

try:
    from ..utils.exceptions import ConfigError, ErrorSeverity
    from ..utils.logger import get_logger
    from ..utils.platform import get_config_directory

    UTILS_AVAILABLE = True
except ImportError:
    UTILS_AVAILABLE = False
    get_config_directory = None


# Regex pattern for detecting common shell prompt terminators.
# Used to split the prompt from the user's command in VTE screen scraping.
# Includes: $ # % > ➜ ❯ ❮ › » ▶ λ ∴ ⟩ ⟫ ⮞ → ➤ ➔ ⇒
# These cover bash, zsh, fish, Oh My Zsh, Starship, Powerlevel10k, etc.
PROMPT_TERMINATOR_PATTERN = re.compile(r"[\$#%>➜❯ᐅ❮›»▶λ∴⟩⟫⮞→➤➔⇒]\s?")


class AppConstants:
    """Application metadata and identification constants."""

    APP_ID = "org.leoberbert.zashterminal"
    APP_TITLE = "Terminal Zash"
    APP_VERSION = "0.5.5"
    DEVELOPER_NAME = "Leonardo Berbert"
    DEVELOPER_TEAM = ["Leonardo Berbert"]
    COPYRIGHT = "© 2025 Leonardo Berbert"
    WEBSITE = "https://github.com/leoberbert/zashterminal/"
    ISSUE_URL = "https://github.com/leoberbert/zashterminal/issues"


class ConfigPaths:
    """Platform-aware configuration paths for Linux."""

    def __init__(self):
        self.logger = get_logger("zashterminal.config.paths") if UTILS_AVAILABLE else None
        self._setup_paths()

    def _setup_paths(self):
        try:
            if UTILS_AVAILABLE and (config_dir := get_config_directory()):
                self.CONFIG_DIR = config_dir
            else:
                self.CONFIG_DIR = self._get_legacy_config_dir()

            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            self.SESSIONS_FILE = self.CONFIG_DIR / "sessions.json"
            self.SETTINGS_FILE = self.CONFIG_DIR / "settings.json"
            self.STATE_FILE = self.CONFIG_DIR / "session_state.json"
            self.LAYOUT_DIR = self.CONFIG_DIR / "layouts"
            self.CACHE_DIR = self._get_cache_directory()
            self.LOG_DIR = self.CONFIG_DIR / "logs"
            self.BACKUP_DIR = (
                self.CONFIG_DIR / "backups"
            )  # Directory for manual backups

            for directory in [
                self.CACHE_DIR,
                self.LOG_DIR,
                self.LAYOUT_DIR,
                self.BACKUP_DIR,
            ]:
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    if self.logger:
                        self.logger.warning(
                            f"Failed to create directory {directory}: {e}"
                        )
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to initialize config paths: {e}")
            self._use_fallback_paths()

    def _get_legacy_config_dir(self) -> Path:
        if xdg_config := os.environ.get("XDG_CONFIG_HOME"):
            return Path(xdg_config) / "zashterminal"
        return Path.home() / ".config" / "zashterminal"

    def _get_cache_directory(self) -> Path:
        if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
            return Path(xdg_cache) / "zashterminal"
        return Path.home() / ".cache" / "zashterminal"

    def _use_fallback_paths(self):
        home = Path.home()
        self.CONFIG_DIR = home / ".config" / "zashterminal"
        self.SESSIONS_FILE = self.CONFIG_DIR / "sessions.json"
        self.SETTINGS_FILE = self.CONFIG_DIR / "settings.json"
        self.STATE_FILE = self.CONFIG_DIR / "session_state.json"
        self.LAYOUT_DIR = self.CONFIG_DIR / "layouts"
        self.CACHE_DIR = home / ".cache" / "zashterminal"
        self.LOG_DIR = self.CONFIG_DIR / "logs"
        self.BACKUP_DIR = self.CONFIG_DIR / "backups"


class DefaultSettings:
    """Default application settings."""

    @staticmethod
    @lru_cache(maxsize=1)
    def get_available_default_font() -> str:
        """
        Detects the first available monospace font on the system.

        Tests fonts in priority order and returns the first one that exists.
        Falls back to generic 'Monospace' if none are found.

        Returns:
            str: Font description string (e.g., "Ubuntu Mono 12")
        """
        # Font priority list: Nerd Fonts -> Popular distro fonts -> Universal fallback
        font_candidates = [
            "Noto Mono Nerd Font Medium 12",
            "JetBrains Mono 12",
            "Ubuntu Mono 12",
            "DejaVu Sans Mono 12",
            "Liberation Mono 12",
            "Source Code Pro 12",
            "Monospace 10",  # Generic fallback - always available
        ]

        try:
            # Lazy import GTK/Pango only when actually needed
            import gi

            gi.require_version("Pango", "1.0")
            from gi.repository import Pango

            # Get list of all available font families on the system
            import cairo
            from gi.repository import PangoCairo

            # Create a temporary surface to get font map
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            context = cairo.Context(surface)
            pango_context = PangoCairo.create_context(context)
            font_map = pango_context.get_font_map()

            # Get all available font families
            available_families = set()
            for family in font_map.list_families():
                family_name = family.get_name().lower()
                available_families.add(family_name)

            # Test each candidate font
            for font_string in font_candidates:
                try:
                    font_desc = Pango.FontDescription.from_string(font_string)
                    family_name = font_desc.get_family()

                    if family_name:
                        # Check if font family exists in system
                        family_lower = family_name.lower()
                        if family_lower in available_families:
                            return font_string
                except Exception:
                    continue

            # Ultimate fallback if all checks fail
            return "Monospace 10"

        except Exception:
            # If any import/context creation fails, return safe default
            return "Monospace 10"

    @staticmethod
    @lru_cache(maxsize=1)
    def get_defaults() -> Dict[str, Any]:
        return {
            # General Appearance
            "gtk_theme": "terminal",
            "color_scheme": 8,
            "transparency": 16,
            "headerbar_transparency": 12,
            "font": DefaultSettings.get_available_default_font(),
            "line_spacing": 1.0,
            "bold_is_bright": False,
            "tab_alignment": "center",
            # Window State
            "window_width": 1200,
            "window_height": 700,
            "window_maximized": False,
            "remember_window_state": True,
            # Controls visibility of window control buttons when maximized
            # "auto" - detect from button-layout (hide if empty/only colon)
            # "always" - always hide buttons when maximized (for KDE Plasma Active Window Control)
            # "never" - never hide buttons (default GNOME-like behavior)
            "hide_headerbar_buttons_when_maximized": "auto",
            # Behavior
            "sidebar_visible": False,
            "auto_hide_sidebar": True,
            "sidebar_width": 300,  # Default sidebar width in pixels
            "file_manager_height": 250,  # Default file manager height in pixels
            "scroll_on_output": True,  # Enables smart scrolling
            "scroll_on_keystroke": True,
            "scroll_on_insert": True,  # Scroll to bottom on paste
            "mouse_autohide": True,
            "cursor_blink": 0,
            "new_instance_behavior": "new_tab",
            "use_login_shell": False,
            "session_restore_policy": "never",
            # VTE Features
            "scrollback_lines": 10000,
            "mouse_scroll_sensitivity": 30.0,
            "touchpad_scroll_sensitivity": 30.0,
            "cursor_shape": 0,
            "bidi_enabled": False,
            "enable_shaping": False,  # For Arabic text shaping
            "sixel_enabled": True,
            "text_blink_mode": 0,
            "accessibility_enabled": True,
            # Compatibility & Advanced
            "backspace_binding": 0,
            "delete_binding": 0,
            "cjk_ambiguous_width": 1,
            "word_char_exceptions": "-_.:/~",  # For word selection on double-click
            "ssh_control_persist_duration": 60,  # Duration in seconds for SSH connection multiplexing
            # Logging Settings
            "log_to_file": False,
            "console_log_level": "ERROR",
            # Remote Editing
            "use_system_tmp_for_edit": False,
            "clear_remote_edit_files_on_exit": True,
            # AI Assistant
            "ai_assistant_enabled": False,
            "ai_assistant_provider": "groq",
            "ai_assistant_model": "llama-3.1-8b-instant",
            "ai_assistant_api_key": "",
            "ai_openrouter_site_url": "",
            "ai_openrouter_site_name": "",
            "ai_local_base_url": "http://localhost:11434/v1",
            "ai_custom_quick_prompts": [],  # List of {"emoji": "🔧", "text": "prompt"}
            # Search Settings
            "search_case_sensitive": False,
            "search_use_regex": False,
            # Highlighting Settings
            "ignored_highlight_commands": [
                "grep",
                "egrep",
                "fgrep",
                "rg",
                "rga",
                "awk",
                "sed",
                "sd",
                "bat",
                "ls",
                "git",
                "vim",
                "nano",
                "nvim",
                "emacs",
                "htop",
                "btop",
                "top",
                "less",
                "more",
                "man",
                "info",
                "diff",
                "colordiff",
                "delta",
                "jq",
                "yq",
                "grc",
            ],
            # Cat Output Colorization Settings
            # When enabled, applies Pygments syntax highlighting to cat command output
            "cat_colorization_enabled": True,
            # Theme mode: "auto" (detects background), or "manual" (uses selected theme)
            "cat_theme_mode": "auto",
            # Pygments theme for dark backgrounds (used when mode is "auto" and bg is dark)
            "cat_dark_theme": "blinds-dark",
            # Pygments theme for light backgrounds (used when mode is "auto" and bg is light)
            "cat_light_theme": "blinds-light",
            # Legacy setting - now only used if mode is "manual"
            "pygments_theme": "monokai",
            # Shell Input Syntax Highlighting (experimental)
            # When enabled, applies Pygments syntax highlighting to shell commands as you type
            "shell_input_highlighting_enabled": False,
            # Theme mode: "auto" (detects background), or "manual" (uses selected theme)
            "shell_input_theme_mode": "auto",
            # Pygments theme for dark backgrounds (used when mode is "auto" and bg is dark)
            "shell_input_dark_theme": "blinds-dark",
            # Pygments theme for light backgrounds (used when mode is "auto" and bg is light)
            "shell_input_light_theme": "blinds-light",
            # Legacy setting kept for backwards compatibility - now only used if mode is "manual"
            "shell_input_pygments_theme": "monokai",
            # Icon Theme Strategy: "zashterminal" (bundled) or "system"
            # Using Zashterminal Icons by default speeds up GTK4 startup
            "icon_theme_strategy": "zashterminal",
            # Shortcuts
            "shortcuts": {
                "new-local-tab": "<Control><Shift>t",
                "close-tab": "<Control><Shift>w",
                "copy": "<Control><Shift>Insert",
                "paste": "<Control><Shift>v",
                "select-all": "<Control><Shift>a",
                "clear-session": "<Control><Shift>l",
                "preferences": "<Control><Shift>comma",
                "quit": "<Control><Shift>q",
                "new-window": "<Control><Shift>n",
                "toggle-sidebar": "<Control><Shift>h",
                "show-command-manager": "<Control><Shift>m",
                "zoom-in": "<Control>plus",
                "zoom-out": "<Control>minus",
                "zoom-reset": "<Control>0",
                "split-horizontal": "<Control><Shift>parenleft",
                "split-vertical": "<Control><Shift>parenright",
                "close-pane": "<Control><Shift>k",
                "next-tab": "<Alt>Page_Down",
                "previous-tab": "<Alt>Page_Up",
                "toggle-file-manager": "<Control><Shift>e",
                "toggle-search": "<Control><Shift>f",
                "toggle-broadcast": "<Control><Shift>b",
                "ai-assistant": "<Control><Shift>i",
            },
        }


class ColorSchemes:
    """Terminal color schemes."""

    @staticmethod
    @lru_cache(maxsize=1)
    def get_schemes() -> Dict[str, Dict[str, Any]]:
        return {
            "system_default": {
                "name": "System Default",
                "foreground": "#ffffff",
                "background": "#000000",
                "headerbar_background": "#1a1a1a",  # MODIFIED
                "cursor": "#ffffff",
                "palette": [
                    "#000000",
                    "#cc0000",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#75507b",
                    "#06989a",
                    "#d3d7cf",
                    "#555753",
                    "#ef2929",
                    "#8ae234",
                    "#fce94f",
                    "#729fcf",
                    "#ad7fa8",
                    "#34e2e2",
                    "#eeeeec",
                ],
            },
            "light": {
                "name": "Light",
                "foreground": "#000000",
                "background": "#ffffff",
                "headerbar_background": "#f0f0f0",  # MODIFIED
                "cursor": "#000000",
                "palette": [
                    "#000000",
                    "#cc0000",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#75507b",
                    "#06989a",
                    "#555753",
                    "#888a85",
                    "#ef2929",
                    "#8ae234",
                    "#fce94f",
                    "#729fcf",
                    "#ad7fa8",
                    "#34e2e2",
                    "#eeeeec",
                ],
            },
            "dark": {
                "name": "Dark",
                "foreground": "#ffffff",
                "background": "#1c1c1c",
                "headerbar_background": "#2a2a2a",  # MODIFIED
                "cursor": "#ffffff",
                "palette": [
                    "#000000",
                    "#cc0000",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#75507b",
                    "#06989a",
                    "#d3d7cf",
                    "#555753",
                    "#ef2929",
                    "#8ae234",
                    "#fce94f",
                    "#729fcf",
                    "#ad7fa8",
                    "#34e2e2",
                    "#eeeeec",
                ],
            },
            "solarized_light": {
                "name": "Solarized Light",
                "foreground": "#657b83",
                "background": "#fdf6e3",
                "headerbar_background": "#eee8d5",  # MODIFIED
                "cursor": "#657b83",
                "palette": [
                    "#073642",
                    "#dc322f",
                    "#859900",
                    "#b58900",
                    "#268bd2",
                    "#d33682",
                    "#2aa198",
                    "#eee8d5",
                    "#002b36",
                    "#cb4b16",
                    "#586e75",
                    "#657b83",
                    "#839496",
                    "#6c71c4",
                    "#93a1a1",
                    "#fdf6e3",
                ],
            },
            "solarized_dark": {
                "name": "Solarized Dark",
                "foreground": "#839496",
                "background": "#002b36",
                "headerbar_background": "#073642",  # MODIFIED
                "cursor": "#839496",
                "palette": [
                    "#073642",
                    "#dc322f",
                    "#859900",
                    "#b58900",
                    "#268bd2",
                    "#d33682",
                    "#2aa198",
                    "#eee8d5",
                    "#002b36",
                    "#cb4b16",
                    "#586e75",
                    "#657b83",
                    "#839496",
                    "#6c71c4",
                    "#93a1a1",
                    "#fdf6e3",
                ],
            },
            "monokai": {
                "name": "Monokai",
                "foreground": "#f8f8f2",
                "background": "#272822",
                "headerbar_background": "#3a3b34",  # MODIFIED
                "cursor": "#f8f8f2",
                "palette": [
                    "#272822",
                    "#f92672",
                    "#a6e22e",
                    "#f4bf75",
                    "#66d9ef",
                    "#ae81ff",
                    "#a1efe4",
                    "#f8f8f2",
                    "#75715e",
                    "#f92672",
                    "#a6e22e",
                    "#f4bf75",
                    "#66d9ef",
                    "#ae81ff",
                    "#a1efe4",
                    "#f9f8f5",
                ],
            },
            "dracula": {
                "name": "Dracula",
                "foreground": "#f8f8f2",
                "background": "#282a36",
                "headerbar_background": "#3a3c4e",  # MODIFIED
                "cursor": "#f8f8f2",
                "palette": [
                    "#000000",
                    "#ff5555",
                    "#50fa7b",
                    "#f1fa8c",
                    "#bd93f9",
                    "#ff79c6",
                    "#8be9fd",
                    "#bfbfbf",
                    "#4d4d4d",
                    "#ff6e67",
                    "#5af78e",
                    "#f4f99d",
                    "#caa9fa",
                    "#ff92d0",
                    "#9aedfe",
                    "#e6e6e6",
                ],
            },
            "nord": {
                "name": "Nord",
                "foreground": "#d8dee9",
                "background": "#2e3440",
                "headerbar_background": "#3b4252",  # MODIFIED
                "cursor": "#d8dee9",
                "palette": [
                    "#3b4252",
                    "#bf616a",
                    "#a3be8c",
                    "#ebcb8b",
                    "#81a1c1",
                    "#b48ead",
                    "#88c0d0",
                    "#e5e9f0",
                    "#4c566a",
                    "#bf616a",
                    "#a3be8c",
                    "#ebcb8b",
                    "#81a1c1",
                    "#b48ead",
                    "#8fbcbb",
                    "#eceff4",
                ],
            },
            "tokyonight": {
                "name": "Tokyo Night",
                "foreground": "#c0caf5",
                "background": "#1a1b26",
                "headerbar_background": "#1f2335",
                "cursor": "#c0caf5",
                "palette": [
                    "#15161e",
                    "#f7768e",
                    "#9ece6a",
                    "#e0af68",
                    "#7aa2f7",
                    "#bb9af7",
                    "#7dcfff",
                    "#a9b1d6",
                    "#414868",
                    "#f7768e",
                    "#9ece6a",
                    "#e0af68",
                    "#7aa2f7",
                    "#bb9af7",
                    "#7dcfff",
                    "#c0caf5",
                ],
            },
            "catppuccin": {
                "name": "Catppuccin Mocha",
                "foreground": "#f5e0dc",
                "background": "#1e1e2e",
                "headerbar_background": "#181825",
                "cursor": "#f5e0dc",
                "palette": [
                    "#45475a",
                    "#f38ba8",
                    "#a6e3a1",
                    "#f9e2af",
                    "#89b4fa",
                    "#f5c2e7",
                    "#94e2d5",
                    "#bac2de",
                    "#585b70",
                    "#f38ba8",
                    "#a6e3a1",
                    "#f9e2af",
                    "#89b4fa",
                    "#f5c2e7",
                    "#94e2d5",
                    "#cdd6f4",
                ],
            },
            "rose_pine": {
                "name": "Rosé Pine",
                "foreground": "#e0def4",
                "background": "#191724",
                "headerbar_background": "#1f1d2e",
                "cursor": "#ebbcba",
                "palette": [
                    "#26233a",
                    "#eb6f92",
                    "#9ccfd8",
                    "#f6c177",
                    "#31748f",
                    "#c4a7e7",
                    "#ebbcba",
                    "#e0def4",
                    "#6e6a86",
                    "#eb6f92",
                    "#9ccfd8",
                    "#f6c177",
                    "#31748f",
                    "#c4a7e7",
                    "#ebbcba",
                    "#e0def4",
                ],
            },
            "pink_light": {
                "name": "Pink Light",
                "foreground": "#4a2040",
                "background": "#ffe4ec",
                "headerbar_background": "#ffd0dd",
                "cursor": "#d63384",
                "palette": [
                    "#4a2040",
                    "#d63384",
                    "#198754",
                    "#cc7a00",
                    "#0d6efd",
                    "#8b5cf6",
                    "#0dcaf0",
                    "#6c757d",
                    "#5c3d52",
                    "#e83e8c",
                    "#20c997",
                    "#ffc107",
                    "#6ea8fe",
                    "#a78bfa",
                    "#6edff6",
                    "#495057",
                ],
            },
            "gruvbox_dark_hard": {
                "name": "Gruvbox Dark Hard",
                "foreground": "#ebdbb2",
                "background": "#1d2021",
                "headerbar_background": "#282828",
                "cursor": "#ebdbb2",
                "palette": [
                    "#1d2021",
                    "#cc241d",
                    "#98971a",
                    "#d79921",
                    "#458588",
                    "#b16286",
                    "#689d6a",
                    "#a89984",
                    "#928374",
                    "#fb4934",
                    "#b8bb26",
                    "#fabd2f",
                    "#83a598",
                    "#d3869b",
                    "#8ec07c",
                    "#ebdbb2",
                ],
            },
            "everforest_dark": {
                "name": "Everforest Dark",
                "foreground": "#d3c6aa",
                "background": "#2b3339",
                "headerbar_background": "#323c41",
                "cursor": "#d3c6aa",
                "palette": [
                    "#4b565c",
                    "#e67e80",
                    "#a7c080",
                    "#dbbc7f",
                    "#7fbbb3",
                    "#d699b6",
                    "#83c092",
                    "#d3c6aa",
                    "#5c6a72",
                    "#f85552",
                    "#8da101",
                    "#dfa000",
                    "#3a94c5",
                    "#df69ba",
                    "#35a77c",
                    "#e4e1cd",
                ],
            },
            "kanagawa_wave": {
                "name": "Kanagawa Wave",
                "foreground": "#dcd7ba",
                "background": "#1f1f28",
                "headerbar_background": "#2a2a37",
                "cursor": "#c8c093",
                "palette": [
                    "#090618",
                    "#c34043",
                    "#76946a",
                    "#c0a36e",
                    "#7e9cd8",
                    "#957fb8",
                    "#6a9589",
                    "#c8c093",
                    "#727169",
                    "#e82424",
                    "#98bb6c",
                    "#e6c384",
                    "#7fb4ca",
                    "#938aa9",
                    "#7aa89f",
                    "#dcd7ba",
                ],
            },
            "onedark_pro": {
                "name": "One Dark Pro",
                "foreground": "#abb2bf",
                "background": "#282c34",
                "headerbar_background": "#2f343f",
                "cursor": "#abb2bf",
                "palette": [
                    "#282c34",
                    "#e06c75",
                    "#98c379",
                    "#e5c07b",
                    "#61afef",
                    "#c678dd",
                    "#56b6c2",
                    "#dcdfe4",
                    "#5c6370",
                    "#e06c75",
                    "#98c379",
                    "#e5c07b",
                    "#61afef",
                    "#c678dd",
                    "#56b6c2",
                    "#ffffff",
                ],
            },
            "ayu_mirage": {
                "name": "Ayu Mirage",
                "foreground": "#cccac2",
                "background": "#1f2430",
                "headerbar_background": "#252b3b",
                "cursor": "#ffcc66",
                "palette": [
                    "#191e2a",
                    "#ff6666",
                    "#b8cc52",
                    "#ffcc66",
                    "#73d0ff",
                    "#d4bfff",
                    "#95e6cb",
                    "#e6e1cf",
                    "#707a8c",
                    "#ff6666",
                    "#b8cc52",
                    "#ffd173",
                    "#73d0ff",
                    "#dfbfff",
                    "#95e6cb",
                    "#f3f4f5",
                ],
            },
            "oxocarbon_dark": {
                "name": "Oxocarbon Dark",
                "foreground": "#dde1e6",
                "background": "#161616",
                "headerbar_background": "#222222",
                "cursor": "#78a9ff",
                "palette": [
                    "#161616",
                    "#ee5396",
                    "#42be65",
                    "#ffe97b",
                    "#78a9ff",
                    "#be95ff",
                    "#33b1ff",
                    "#f2f4f8",
                    "#525252",
                    "#ff7eb6",
                    "#42be65",
                    "#ffe97b",
                    "#78a9ff",
                    "#be95ff",
                    "#82cfff",
                    "#ffffff",
                ],
            },
            "gruvbox_light": {
                "name": "Gruvbox Light",
                "foreground": "#3c3836",
                "background": "#fbf1c7",
                "headerbar_background": "#ebdbb2",
                "cursor": "#3c3836",
                "palette": [
                    "#fbf1c7",
                    "#cc241d",
                    "#98971a",
                    "#d79921",
                    "#458588",
                    "#b16286",
                    "#689d6a",
                    "#7c6f64",
                    "#928374",
                    "#9d0006",
                    "#79740e",
                    "#b57614",
                    "#076678",
                    "#8f3f71",
                    "#427b58",
                    "#3c3836",
                ],
            },
            "everforest_light": {
                "name": "Everforest Light",
                "foreground": "#5c6a72",
                "background": "#fdf6e3",
                "headerbar_background": "#f3ead3",
                "cursor": "#5c6a72",
                "palette": [
                    "#f3ead3",
                    "#f85552",
                    "#8da101",
                    "#dfa000",
                    "#3a94c5",
                    "#df69ba",
                    "#35a77c",
                    "#5c6a72",
                    "#a6b0a0",
                    "#e66868",
                    "#93b259",
                    "#d8a657",
                    "#5f9ea0",
                    "#d699b6",
                    "#83c092",
                    "#4f5b58",
                ],
            },
            "catppuccin_latte": {
                "name": "Catppuccin Latte",
                "foreground": "#4c4f69",
                "background": "#eff1f5",
                "headerbar_background": "#e6e9ef",
                "cursor": "#dc8a78",
                "palette": [
                    "#5c5f77",
                    "#d20f39",
                    "#40a02b",
                    "#df8e1d",
                    "#1e66f5",
                    "#ea76cb",
                    "#179299",
                    "#acb0be",
                    "#6c6f85",
                    "#d20f39",
                    "#40a02b",
                    "#df8e1d",
                    "#1e66f5",
                    "#ea76cb",
                    "#179299",
                    "#4c4f69",
                ],
            },
            "github_dark": {
                "name": "GitHub Dark",
                "foreground": "#c9d1d9",
                "background": "#0d1117",
                "headerbar_background": "#161b22",
                "cursor": "#58a6ff",
                "palette": [
                    "#484f58",
                    "#ff7b72",
                    "#3fb950",
                    "#d29922",
                    "#58a6ff",
                    "#bc8cff",
                    "#39c5cf",
                    "#b1bac4",
                    "#6e7681",
                    "#ffa198",
                    "#56d364",
                    "#e3b341",
                    "#79c0ff",
                    "#d2a8ff",
                    "#56d4dd",
                    "#f0f6fc",
                ],
            },
            "github_light": {
                "name": "GitHub Light",
                "foreground": "#24292f",
                "background": "#ffffff",
                "headerbar_background": "#f6f8fa",
                "cursor": "#0969da",
                "palette": [
                    "#24292f",
                    "#cf222e",
                    "#1a7f37",
                    "#9a6700",
                    "#0969da",
                    "#8250df",
                    "#1b7c83",
                    "#57606a",
                    "#6e7781",
                    "#a40e26",
                    "#1f883d",
                    "#bf8700",
                    "#218bff",
                    "#a475f9",
                    "#3192aa",
                    "#ffffff",
                ],
            },
            "material_ocean": {
                "name": "Material Ocean",
                "foreground": "#c8d3f5",
                "background": "#0f111a",
                "headerbar_background": "#191b24",
                "cursor": "#82aaff",
                "palette": [
                    "#1b1d2b",
                    "#ff757f",
                    "#c3e88d",
                    "#ffc777",
                    "#82aaff",
                    "#c099ff",
                    "#86e1fc",
                    "#828bb8",
                    "#444a73",
                    "#ff8b92",
                    "#ddffa7",
                    "#ffd8ab",
                    "#9ab8ff",
                    "#caabff",
                    "#b2ebff",
                    "#c8d3f5",
                ],
            },
            "palenight": {
                "name": "Palenight",
                "foreground": "#a6accd",
                "background": "#292d3e",
                "headerbar_background": "#32374d",
                "cursor": "#ffcc00",
                "palette": [
                    "#292d3e",
                    "#f07178",
                    "#c3e88d",
                    "#ffcb6b",
                    "#82aaff",
                    "#c792ea",
                    "#89ddff",
                    "#d0d0d0",
                    "#434758",
                    "#ff8b92",
                    "#ddffa7",
                    "#ffe585",
                    "#9ab8ff",
                    "#d6acff",
                    "#a3f7ff",
                    "#ffffff",
                ],
            },
            "night_owl": {
                "name": "Night Owl",
                "foreground": "#d6deeb",
                "background": "#011627",
                "headerbar_background": "#0b253a",
                "cursor": "#80a4c2",
                "palette": [
                    "#011627",
                    "#ef5350",
                    "#22da6e",
                    "#c5e478",
                    "#82aaff",
                    "#c792ea",
                    "#21c7a8",
                    "#ffffff",
                    "#575656",
                    "#ef5350",
                    "#22da6e",
                    "#ffeb95",
                    "#82aaff",
                    "#c792ea",
                    "#7fdbca",
                    "#ffffff",
                ],
            },
            "moonfly": {
                "name": "Moonfly",
                "foreground": "#bdbbc0",
                "background": "#080808",
                "headerbar_background": "#121121",
                "cursor": "#9e9ecb",
                "palette": [
                    "#323437",
                    "#ff5454",
                    "#8cc85f",
                    "#e3c78a",
                    "#80a0ff",
                    "#cf87e8",
                    "#79dac8",
                    "#c6c6c6",
                    "#949494",
                    "#ff5189",
                    "#36c692",
                    "#bfbf97",
                    "#74b2ff",
                    "#ae81ff",
                    "#85dc85",
                    "#e4e4e4",
                ],
            },
            "horizon_dark": {
                "name": "Horizon Dark",
                "foreground": "#cbced0",
                "background": "#1c1e26",
                "headerbar_background": "#232530",
                "cursor": "#f9cec3",
                "palette": [
                    "#16161c",
                    "#e95678",
                    "#29d398",
                    "#fab795",
                    "#26bbd9",
                    "#ee64ac",
                    "#59e3e3",
                    "#d5d8da",
                    "#5b5858",
                    "#ec6a88",
                    "#3fdaa4",
                    "#fbc3a7",
                    "#3fc4de",
                    "#f075b5",
                    "#6be6e6",
                    "#d5d8da",
                ],
            },
            "everforest": {
                "name": "Everforest Green",
                "foreground": "#ffffff",
                "background": "#2f383a",
                "headerbar_background": "#283335",
                "cursor": "#ffffff",
                "palette": [
                    "#000000",
                    "#ed333b",
                    "#4e9a06",
                    "#c4a000",
                    "#3465a4",
                    "#7fe07b",
                    "#06989a",
                    "#d3d7cf",
                    "#555753",
                    "#f66151",
                    "#8ae234",
                    "#fce94f",
                    "#57e389",
                    "#8bbd80",
                    "#2ec27e",
                    "#eeeeec",
                ],
            },
            "vscode_dark_plus": {
                "name": "VS Code Dark+",
                "foreground": "#d4d4d4",
                "background": "#1e1e1e",
                "headerbar_background": "#252526",
                "cursor": "#aeafad",
                "palette": [
                    "#000000",
                    "#cd3131",
                    "#0dbc79",
                    "#e5e510",
                    "#2472c8",
                    "#bc3fbc",
                    "#11a8cd",
                    "#e5e5e5",
                    "#666666",
                    "#f14c4c",
                    "#23d18b",
                    "#f5f543",
                    "#3b8eea",
                    "#d670d6",
                    "#29b8db",
                    "#e5e5e5",
                ],
            },
        }


class ColorSchemeMap:
    """Maps combobox indices to color scheme names."""

    SCHEME_ORDER = [
        "ayu_mirage",
        "catppuccin",
        "catppuccin_latte",
        "dark",
        "dracula",
        "everforest",
        "everforest_dark",
        "everforest_light",
        "github_dark",
        "github_light",
        "gruvbox_dark_hard",
        "gruvbox_light",
        "horizon_dark",
        "kanagawa_wave",
        "light",
        "material_ocean",
        "monokai",
        "moonfly",
        "night_owl",
        "nord",
        "onedark_pro",
        "oxocarbon_dark",
        "palenight",
        "pink_light",
        "rose_pine",
        "solarized_dark",
        "solarized_light",
        "system_default",
        "tokyonight",
        "vscode_dark_plus",
    ]

    @classmethod
    def get_schemes_list(cls) -> List[str]:
        return cls.SCHEME_ORDER.copy()


_config_paths = None


def get_config_paths() -> ConfigPaths:
    """Get global configuration paths instance."""
    global _config_paths
    if _config_paths is None:
        _config_paths = ConfigPaths()
    return _config_paths


def initialize_configuration():
    """Initialize configuration system with validation."""
    logger = get_logger("zashterminal.config") if UTILS_AVAILABLE else None
    try:
        if logger:
            logger.info(
                f"Initializing {AppConstants.APP_TITLE} v{AppConstants.APP_VERSION}"
            )
        paths = get_config_paths()
        if logger:
            logger.info(f"Configuration directory: {paths.CONFIG_DIR}")
    except Exception as e:
        error_msg = f"Configuration initialization failed: {e}"
        if logger:
            logger.critical(error_msg)
        raise ConfigError(
            error_msg,
            severity=ErrorSeverity.CRITICAL,
            user_message="Application initialization failed",
        )


try:
    initialize_configuration()
except Exception as e:
    print(f"WARNING: Configuration initialization failed: {e}")

APP_ID = AppConstants.APP_ID
APP_TITLE = AppConstants.APP_TITLE
APP_VERSION = AppConstants.APP_VERSION
DEVELOPER_NAME = AppConstants.DEVELOPER_NAME
DEVELOPER_TEAM = AppConstants.DEVELOPER_TEAM
COPYRIGHT = AppConstants.COPYRIGHT
WEBSITE = AppConstants.WEBSITE
ISSUE_URL = AppConstants.ISSUE_URL

try:
    _paths = get_config_paths()
    CONFIG_DIR = str(_paths.CONFIG_DIR)
    SESSIONS_FILE = str(_paths.SESSIONS_FILE)
    SETTINGS_FILE = str(_paths.SETTINGS_FILE)
    STATE_FILE = str(_paths.STATE_FILE)
    LAYOUT_DIR = str(_paths.LAYOUT_DIR)
    BACKUP_DIR = str(_paths.BACKUP_DIR)
except Exception:
    CONFIG_DIR = os.path.expanduser("~/.config/zashterminal")
    SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
    SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
    STATE_FILE = os.path.join(CONFIG_DIR, "session_state.json")
    LAYOUT_DIR = os.path.join(CONFIG_DIR, "layouts")
    BACKUP_DIR = os.path.join(CONFIG_DIR, "backups")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(LAYOUT_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
