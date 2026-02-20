# zashterminal/utils/theme_engine.py
"""
Theme Engine for generating dynamic application CSS based on color schemes.
"""

from typing import Any, Dict

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw


class ThemeEngine:
    """Generates CSS for the application based on color scheme parameters."""

    @staticmethod
    def get_theme_params(
        scheme: Dict[str, Any], transparency: int = 0
    ) -> Dict[str, Any]:
        """Extract and compute theme parameters from color scheme."""
        bg_color = scheme.get("background", "#000000")
        fg_color = scheme.get("foreground", "#ffffff")
        header_bg_color = scheme.get("headerbar_background", bg_color)

        r = int(bg_color[1:3], 16) / 255
        g = int(bg_color[3:5], 16) / 255
        b = int(bg_color[5:7], 16) / 255
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        is_dark_theme = luminance < 0.5

        return {
            "bg_color": bg_color,
            "fg_color": fg_color,
            "header_bg_color": header_bg_color,
            "user_transparency": transparency,
            "luminance": luminance,
            "is_dark_theme": is_dark_theme,
        }

    @classmethod
    def generate_app_css(cls, params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Generates the full application CSS string."""
        css_parts = [
            cls._get_root_vars_css(params, gtk_theme_name),
            cls._get_headerbar_css(params, gtk_theme_name),
            cls._get_tabs_css(params, gtk_theme_name),
        ]
        return "".join(css_parts)

    @staticmethod
    def _get_root_vars_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        if gtk_theme_name != "terminal":
            return ""

        if params["luminance"] < 0.05:
            return ""

        fg = params["fg_color"]
        bg = params["bg_color"]
        header_bg = params["header_bg_color"]

        return f"""
        :root {{
            --window-bg-color: {bg};
            --window-fg-color: {fg};
            --view-bg-color: {bg};
            --view-fg-color: {fg};
            --headerbar-bg-color: {header_bg};
            --headerbar-fg-color: {fg};
            --headerbar-backdrop-color: {header_bg};
            --headerbar-shade-color: color-mix(in srgb, {header_bg}, black 7%);
            --popover-bg-color: {bg};
            --popover-fg-color: {fg};
            --dialog-bg-color: {bg};
            --dialog-fg-color: {fg};
            --card-bg-color: color-mix(in srgb, {bg}, white 5%);
            --card-fg-color: {fg};
            --sidebar-bg-color: {header_bg};
            --sidebar-fg-color: {fg};
        }}

        popover.zashterminal-popover,
        popover.sidebar-popover {{
            background-color: transparent;
            color: var(--popover-fg-color);
        }}

        popover.zashterminal-popover > contents,
        popover.sidebar-popover > contents,
        popover.zashterminal-popover > arrow,
        popover.sidebar-popover > arrow {{
            background-color: var(--popover-bg-color);
            color: inherit;
        }}

        popover.zashterminal-popover listview,
        popover.sidebar-popover listview,
        popover.zashterminal-popover scrolledwindow,
        popover.sidebar-popover scrolledwindow {{
            background-color: transparent;
        }}
        """

    @staticmethod
    def _get_headerbar_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        user_transparency = params["user_transparency"]
        if user_transparency == 0:
            return ""

        if gtk_theme_name == "terminal":
            base_bg = params["header_bg_color"]
        else:
            style_manager = Adw.StyleManager.get_default()
            is_dark = style_manager.get_dark()
            base_bg = "#303030" if is_dark else "#f0f0f0"

        opacity_percent = 100 - user_transparency
        bg_css_value = f"color-mix(in srgb, {base_bg} {opacity_percent}%, transparent)"

        selectors = """
        window headerbar.main-header-bar,
        headerbar.main-header-bar,
        .main-header-bar,
        .terminal-pane .header-bar,
        .top-bar,
        searchbar,
        searchbar > box,
        .command-toolbar
        """

        return f"""
        {selectors} {{
            background-color: {bg_css_value};
            background-image: none;
        }}
        {selectors.replace(",", ":backdrop,")}:backdrop {{
            background-color: {bg_css_value};
            background-image: none;
        }}
        """

    @staticmethod
    def _get_tabs_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        if gtk_theme_name == "terminal":
            fg = params["fg_color"]
            return f"""
            .scrolled-tab-bar viewport box .horizontal.active {{
                background-color: color-mix(in srgb, {fg}, transparent 78%);
            }}
            """
        return ""
