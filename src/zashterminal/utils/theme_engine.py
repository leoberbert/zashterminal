# zashterminal/utils/theme_engine.py
"""
Theme Engine for generating dynamic application CSS based on color schemes.
"""

from typing import Any, Dict

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gtk


class ThemeEngine:
    """Generates CSS for the application based on color scheme parameters."""

    @staticmethod
    def _supports_modern_css() -> bool:
        """Best-effort CSS feature gate (css vars + color-mix in GtkCssProvider)."""
        try:
            return (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 16)
        except Exception:
            return False

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        value = (color or "").strip()
        if value.startswith("#"):
            value = value[1:]
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        if len(value) != 6:
            return (0, 0, 0)
        try:
            return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return (0, 0, 0)

    @classmethod
    def _mix_hex(cls, first: str, second: str, first_ratio: float) -> str:
        r1, g1, b1 = cls._hex_to_rgb(first)
        r2, g2, b2 = cls._hex_to_rgb(second)
        p = max(0.0, min(1.0, first_ratio))
        q = 1.0 - p
        r = round(r1 * p + r2 * q)
        g = round(g1 * p + g2 * q)
        b = round(b1 * p + b2 * q)
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _relative_luminance(color: str) -> float:
        def lin(v: int) -> float:
            c = v / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = ThemeEngine._hex_to_rgb(color)
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    @classmethod
    def contrast_ratio(cls, first: str, second: str) -> float:
        low, high = sorted((cls._relative_luminance(first), cls._relative_luminance(second)))
        return (high + 0.05) / (low + 0.05)

    @classmethod
    def ensure_contrast(cls, color: str, backgrounds: list, target: float) -> str:
        """Nudge ``color`` toward black or white until it reads on every background.

        Used for interface text only (never the terminal's own colors), so a
        soft scheme such as Solarized Light keeps its hue but stays legible in
        side panels, menus and captions.
        """
        if not color or not color.startswith("#") or len(color) != 7:
            return color

        def worst(c: str) -> float:
            return min(cls.contrast_ratio(c, bg) for bg in backgrounds)

        if worst(color) >= target:
            return color
        # Pull toward the extreme that contrasts best with the backgrounds
        mean_lum = sum(cls._relative_luminance(bg) for bg in backgrounds) / len(backgrounds)
        pole = "#000000" if mean_lum > 0.179 else "#ffffff"
        for step in range(1, 21):
            candidate = cls._mix_hex(pole, color, step * 0.05)
            if worst(candidate) >= target:
                return candidate
        return pole

    @staticmethod
    def _alpha(color: str, alpha: float) -> str:
        alpha = max(0.0, min(1.0, alpha))
        return f"alpha({color}, {alpha:.3f})"

    @staticmethod
    def get_theme_params(
        scheme: Dict[str, Any], transparency: int = 0, min_luminance: float = 0.05
    ) -> Dict[str, Any]:
        """Extract and compute theme parameters from color scheme."""
        bg_color = scheme.get("background", "#000000")
        header_bg_color = scheme.get("headerbar_background", bg_color)
        # Interface text/accent are adjusted for legibility; the terminal itself
        # keeps the scheme's exact colors.
        fg_color = ThemeEngine.ensure_contrast(
            scheme.get("foreground", "#ffffff"), [bg_color, header_bg_color], 9.0
        )
        accent = scheme.get("accent")
        if accent:
            accent = ThemeEngine.ensure_contrast(accent, [bg_color, header_bg_color], 3.5)

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
            "min_luminance": min_luminance,
            "is_dark_theme": is_dark_theme,
            "accent": accent,
            "palette": list(scheme.get("palette", [])),
            "accent_foreground": scheme.get("selection_foreground", "#ffffff"),
        }

    @classmethod
    def generate_app_css(cls, params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Generates the full application CSS string."""
        css_parts = [
            cls._get_brand_css(params, gtk_theme_name),
            cls._get_root_vars_css(params, gtk_theme_name),
            cls._get_headerbar_css(params, gtk_theme_name),
            cls._get_tabs_css(params, gtk_theme_name),
            cls._get_chrome_css(params, gtk_theme_name),
        ]
        return "".join(css_parts)

    @classmethod
    def _get_brand_css(cls, params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Re-point the Aurora brand colors at the active terminal/wallpaper palette.

        Static defaults live in window.css; other themes keep the brand identity.
        """
        if gtk_theme_name != "terminal":
            return ""
        palette = params.get("palette") or []
        accent = params.get("accent") or (palette[4] if len(palette) > 4 else None)
        if not accent:
            return ""
        second = palette[6] if len(palette) > 6 else cls._mix_hex(accent, params["fg_color"], 0.7)
        if second.lower() == accent.lower():
            second = cls._mix_hex(accent, params["fg_color"], 0.7)
        backgrounds = [params["bg_color"], params["header_bg_color"]]
        accent = cls.ensure_contrast(accent, backgrounds, 3.5)
        second = cls.ensure_contrast(second, backgrounds, 3.5)
        alert = cls.ensure_contrast(
            palette[3] if len(palette) > 3 else "#e66100",
            [params["bg_color"], params["header_bg_color"]],
            3.5,
        )

        def lum(color: str) -> float:
            chans = [
                v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4
                for v in cls._hex_to_rgb(color)
            ]
            return 0.2126 * chans[0] + 0.7152 * chans[1] + 0.0722 * chans[2]

        # Text on the brand gradient: pick the color that reads best on both stops
        avg = (lum(accent) + lum(second)) / 2
        on_brand = "#000000" if (avg + 0.05) / 0.05 > 1.05 / (avg + 0.05) else "#ffffff"
        css = (
            f"@define-color zash_brand {accent};"
            f"@define-color zash_brand_2 {second};"
            f"@define-color zash_brand_fg {on_brand};"
            f"@define-color zash_alert {alert};"
        )
        # Monitor panel series follow the terminal palette (ANSI red, green,
        # yellow, magenta, cyan); missing entries keep the window.css defaults.
        for name, index in (
            ("crit", 1),
            ("net", 2),
            ("warn", 3),
            ("mem", 5),
            ("lat", 6),
        ):
            if len(palette) > index:
                css += f"@define-color zash_metric_{name} {palette[index]};"
        if len(palette) > 2:
            css += f"@define-color zash_metric_disk {palette[2]};"
        return css

    @staticmethod
    def _get_root_vars_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        if gtk_theme_name != "terminal":
            return ""

        if params["luminance"] < params.get("min_luminance", 0.05):
            return ""

        fg = params["fg_color"]
        bg = params["bg_color"]
        header_bg = params["header_bg_color"]
        accent_css = ""
        if params.get("accent"):
            accent_css = f"--accent-bg-color: {params['accent']}; --accent-color: {params['accent']}; --accent-fg-color: {params['accent_foreground']};"

        if not ThemeEngine._supports_modern_css():
            header_shade = ThemeEngine._mix_hex(header_bg, "#000000", 0.93)
            card_bg = ThemeEngine._mix_hex(bg, "#ffffff", 0.95)
            legacy_accent = ""
            if params.get("accent"):
                legacy_accent = f"@define-color accent_bg_color {params['accent']}; @define-color accent_color {params['accent']}; @define-color accent_fg_color {params['accent_foreground']};"
            return f"""
            {legacy_accent}
            window,
            .background {{
                background-color: {bg};
                color: {fg};
            }}

            popover.zashterminal-popover,
            popover.sidebar-popover {{
                background-color: transparent;
                color: {fg};
            }}

            popover.zashterminal-popover > contents,
            popover.sidebar-popover > contents,
            popover.zashterminal-popover > arrow,
            popover.sidebar-popover > arrow {{
                background-color: {bg};
                color: {fg};
            }}

            popover.zashterminal-popover listview,
            popover.sidebar-popover listview,
            popover.zashterminal-popover scrolledwindow,
            popover.sidebar-popover scrolledwindow {{
                background-color: transparent;
            }}

            .card,
            preferencesgroup > box {{
                background-color: {card_bg};
                color: {fg};
            }}

            headerbar.main-header-bar,
            .main-header-bar {{
                background-color: {header_bg};
                color: {fg};
                box-shadow: inset 0 -1px {header_shade};
            }}
            """

        return f"""
        :root {{
            {accent_css}
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
        if ThemeEngine._supports_modern_css():
            bg_css_value = f"color-mix(in srgb, {base_bg} {opacity_percent}%, transparent)"
        else:
            bg_css_value = ThemeEngine._alpha(base_bg, opacity_percent / 100.0)

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
            active_bg = (
                f"color-mix(in srgb, {fg}, transparent 78%)"
                if ThemeEngine._supports_modern_css()
                else ThemeEngine._alpha(fg, 0.22)
            )
            return f"""
            .scrolled-tab-bar viewport box .horizontal.active {{
                background-color: {active_bg};
            }}
            """
        return ""

    @staticmethod
    def _get_chrome_css(params: Dict[str, Any], gtk_theme_name: str) -> str:
        """Paint the rail, sessions panel, tab strip and status bar like the terminal.

        They share the header bar's color (and its transparency) so the whole
        window reads as one surface instead of tinted side panels.
        """
        if gtk_theme_name != "terminal":
            return ""
        base = params["header_bg_color"]
        opacity = 100 - params["user_transparency"]
        if opacity >= 100:
            bg = base
        elif ThemeEngine._supports_modern_css():
            bg = f"color-mix(in srgb, {base} {opacity}%, transparent)"
        else:
            bg = ThemeEngine._alpha(base, opacity / 100.0)
        fg = params["fg_color"]
        return f"""
        .zash-rail,
        .sidebar-container,
        .zash-tabstrip,
        .zash-statusbar {{
            background-color: {bg};
            background-image: none;
            color: {fg};
        }}
        """
