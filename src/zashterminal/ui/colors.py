# zashterminal/ui/colors.py
"""
Centralized color definitions and utilities for Zashterminal.

This module consolidates all color-related constants and utilities that were
previously scattered across multiple files:
- ANSI color mappings (from settings/highlights.py)
- Color options for UI dropdowns (from ui/dialogs/highlight_dialog.py)
- Syntax highlighting colors (from ui/widgets/bash_text_view.py, regex_text_view.py)

Usage:
    from ..ui.colors import (
        ANSI_COLOR_MAP,
        ANSI_MODIFIERS,
        get_foreground_color_options,
        get_background_color_options,
        get_text_effect_options,
        get_syntax_colors,
        map_palette_to_syntax,
    )
"""

from typing import Dict, List, Tuple

from ..utils.translation_utils import _

# =============================================================================
# ANSI Color Constants
# =============================================================================

# Mapping of logical color names to ANSI color indices (0-15)
# Standard ANSI: 0-7, Bright: 8-15
ANSI_COLOR_MAP: Dict[str, int] = {
    "black": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
    "bright_black": 8,
    "bright_red": 9,
    "bright_green": 10,
    "bright_yellow": 11,
    "bright_blue": 12,
    "bright_magenta": 13,
    "bright_cyan": 14,
    "bright_white": 15,
}

# Reverse mapping: index to color name
ANSI_INDEX_TO_NAME: Dict[int, str] = {v: k for k, v in ANSI_COLOR_MAP.items()}

# ANSI SGR modifier codes
ANSI_MODIFIERS: Dict[str, str] = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "blink": "5",
    "reverse": "7",
    "strikethrough": "9",
}


# =============================================================================
# UI Color Options (for dropdowns/selectors)
# =============================================================================

def get_foreground_color_options() -> List[Tuple[str, str]]:
    """
    Get foreground color options for UI dropdowns.
    
    Returns:
        List of (color_id, localized_label) tuples.
    """
    return [
        # Standard ANSI
        ("black", _("Black")),
        ("red", _("Red")),
        ("green", _("Green")),
        ("yellow", _("Yellow")),
        ("blue", _("Blue")),
        ("magenta", _("Magenta")),
        ("cyan", _("Cyan")),
        ("white", _("White")),
        # Bright variants
        ("bright_black", _("Bright Black")),
        ("bright_red", _("Bright Red")),
        ("bright_green", _("Bright Green")),
        ("bright_yellow", _("Bright Yellow")),
        ("bright_blue", _("Bright Blue")),
        ("bright_magenta", _("Bright Magenta")),
        ("bright_cyan", _("Bright Cyan")),
        ("bright_white", _("Bright White")),
        # Theme colors
        ("foreground", _("Foreground")),
    ]


def get_background_color_options() -> List[Tuple[str, str]]:
    """
    Get background color options for UI dropdowns.
    Uses 'on_' prefix for ANSI mapping compatibility.
    
    Returns:
        List of (color_id, localized_label) tuples.
    """
    return [
        ("", _("Default")),
        ("on_black", _("Black")),
        ("on_red", _("Red")),
        ("on_green", _("Green")),
        ("on_yellow", _("Yellow")),
        ("on_blue", _("Blue")),
        ("on_magenta", _("Magenta")),
        ("on_cyan", _("Cyan")),
        ("on_white", _("White")),
        ("on_bright_black", _("Bright Black")),
        ("on_bright_red", _("Bright Red")),
        ("on_bright_green", _("Bright Green")),
        ("on_bright_yellow", _("Bright Yellow")),
        ("on_bright_blue", _("Bright Blue")),
        ("on_bright_magenta", _("Bright Magenta")),
        ("on_bright_cyan", _("Bright Cyan")),
        ("on_bright_white", _("Bright White")),
    ]


def get_text_effect_options() -> List[Tuple[str, str, str]]:
    """
    Get text effect options for toggle buttons.
    
    Returns:
        List of (effect_id, localized_label, icon_name) tuples.
    """
    return [
        ("bold", _("Bold"), "format-text-bold-symbolic"),
        ("italic", _("Italic"), "format-text-italic-symbolic"),
        ("underline", _("Underline"), "format-text-underline-symbolic"),
        ("strikethrough", _("Strikethrough"), "format-text-strikethrough-symbolic"),
        ("dim", _("Dim/Faint"), "weather-clear-night-symbolic"),
        ("blink", _("Blink"), "alarm-symbolic"),
    ]


# =============================================================================
# Syntax Highlighting Colors
# =============================================================================

# Dark mode syntax colors (brighter for visibility on dark backgrounds)
SYNTAX_DARK_COLORS: Dict[str, str] = {
    # Bash/shell tokens
    "keyword": "#729fcf",        # Blue
    "builtin": "#8ae234",        # Green
    "command": "#8ae234",        # Green
    "string": "#e9b96e",         # Orange
    "string_single": "#e9b96e",  # Orange
    "backtick": "#daa520",       # Goldenrod
    "comment": "#888a85",        # Gray
    "variable": "#ad7fa8",       # Purple
    "special_var": "#ff69b4",    # Hot pink
    "operator": "#fcaf3e",       # Yellow/Orange
    "number": "#f4d03f",         # Yellow
    "path": "#87ceeb",           # Sky blue
    "function": "#dda0dd",       # Plum
    "redirect": "#fcaf3e",       # Yellow/Orange
    "pipe": "#fcaf3e",           # Yellow/Orange
    "flag": "#98d8c8",           # Mint
    "escape": "#deb887",         # Burlywood
    "substitution": "#daa520",   # Goldenrod
    "brace": "#20b2aa",          # Light sea green
    # Regex tokens
    "bracket": "#729fcf",        # Blue - character class brackets []
    "group": "#ad7fa8",          # Purple - grouping ()
    "quantifier": "#8ae234",     # Green - quantifiers *, +, ?, {}
    "anchor": "#fcaf3e",         # Orange - anchors ^, $, \b, etc.
    "special": "#ff69b4",        # Pink - special characters \.
    "range": "#87ceeb",          # Sky blue - range operator -
}

# Light mode syntax colors (darker for contrast on light backgrounds)
SYNTAX_LIGHT_COLORS: Dict[str, str] = {
    # Bash/shell tokens
    "keyword": "#1a5fb4",        # Dark blue
    "builtin": "#26a269",        # Dark green
    "command": "#26a269",        # Dark green
    "string": "#9c6100",         # Dark orange
    "string_single": "#9c6100",  # Dark orange
    "backtick": "#8b6914",       # Dark goldenrod
    "comment": "#5c5c5c",        # Dark gray
    "variable": "#813d9c",       # Dark purple
    "special_var": "#c01c28",    # Dark red
    "operator": "#9c5400",       # Dark orange
    "number": "#8b6914",         # Dark yellow/brown
    "path": "#1a5fb4",           # Dark blue
    "function": "#813d9c",       # Dark purple
    "redirect": "#9c5400",       # Dark orange
    "pipe": "#9c5400",           # Dark orange
    "flag": "#1a8171",           # Teal
    "escape": "#8b6914",         # Dark brown
    "substitution": "#8b6914",   # Dark goldenrod
    "brace": "#1a8171",          # Teal
    # Regex tokens
    "bracket": "#1a5fb4",        # Dark blue
    "group": "#813d9c",          # Dark purple
    "quantifier": "#26a269",     # Dark green
    "anchor": "#9c5400",         # Dark orange
    "special": "#c01c28",        # Dark red
    "range": "#1a5fb4",          # Dark blue
}


def get_syntax_colors(is_dark: bool) -> Dict[str, str]:
    """
    Get syntax highlighting colors for light or dark mode.
    
    Args:
        is_dark: True for dark mode, False for light mode.
        
    Returns:
        Dictionary mapping token types to hex colors.
    """
    return SYNTAX_DARK_COLORS.copy() if is_dark else SYNTAX_LIGHT_COLORS.copy()


def map_palette_to_syntax(palette: List[str]) -> Dict[str, str]:
    """
    Map a terminal color palette (16 colors) to syntax token types.
    
    This creates theme-aware syntax colors by mapping palette indices
    to semantic token types based on common terminal color conventions.
    
    Args:
        palette: List of 16 hex color strings (ANSI palette).
        
    Returns:
        Dictionary mapping token types to hex colors from the palette.
    """
    if not palette or len(palette) < 8:
        # Fall back to dark mode defaults if palette is invalid
        return SYNTAX_DARK_COLORS.copy()

    # Map palette indices to syntax tokens
    # Uses semantic relationships: blue=keywords, green=strings/commands, etc.
    return {
        # Primary mappings (standard ANSI indices)
        "keyword": palette[4] if len(palette) > 4 else "#729fcf",      # Blue
        "builtin": palette[2] if len(palette) > 2 else "#8ae234",      # Green
        "command": palette[2] if len(palette) > 2 else "#8ae234",      # Green
        "string": palette[3] if len(palette) > 3 else "#e9b96e",       # Yellow
        "string_single": palette[3] if len(palette) > 3 else "#e9b96e",
        "comment": palette[8] if len(palette) > 8 else "#888a85",      # Bright black
        "variable": palette[5] if len(palette) > 5 else "#ad7fa8",     # Magenta
        "special_var": palette[13] if len(palette) > 13 else "#ff69b4", # Bright magenta
        "operator": palette[11] if len(palette) > 11 else "#fcaf3e",   # Bright yellow
        "number": palette[11] if len(palette) > 11 else "#f4d03f",     # Bright yellow
        "path": palette[12] if len(palette) > 12 else "#87ceeb",       # Bright blue
        "function": palette[5] if len(palette) > 5 else "#dda0dd",     # Magenta
        "redirect": palette[3] if len(palette) > 3 else "#fcaf3e",     # Yellow
        "pipe": palette[3] if len(palette) > 3 else "#fcaf3e",         # Yellow
        "flag": palette[6] if len(palette) > 6 else "#98d8c8",         # Cyan
        "escape": palette[11] if len(palette) > 11 else "#deb887",     # Bright yellow
        "substitution": palette[11] if len(palette) > 11 else "#daa520",
        "brace": palette[6] if len(palette) > 6 else "#20b2aa",        # Cyan
        "backtick": palette[11] if len(palette) > 11 else "#daa520",   # Bright yellow
        # Regex-specific tokens
        "bracket": palette[4] if len(palette) > 4 else "#729fcf",      # Blue
        "group": palette[5] if len(palette) > 5 else "#ad7fa8",        # Magenta
        "quantifier": palette[2] if len(palette) > 2 else "#8ae234",   # Green
        "anchor": palette[3] if len(palette) > 3 else "#fcaf3e",       # Yellow
        "special": palette[1] if len(palette) > 1 else "#ff69b4",      # Red
        "range": palette[12] if len(palette) > 12 else "#87ceeb",      # Bright blue
    }


# =============================================================================
# Color Resolution Utilities
# =============================================================================

def resolve_color_to_hex(
    color_name: str,
    palette: List[str],
    foreground: str = "#ffffff",
    background: str = "#000000",
) -> str:
    """
    Resolve a logical color name to a hex color string.
    
    Args:
        color_name: Logical name like "red", "bright_cyan", "foreground"
        palette: Terminal color palette (16 hex colors)
        foreground: Theme foreground color
        background: Theme background color
        
    Returns:
        Hex color string like "#ff5555"
    """
    if not color_name:
        return "#ffffff"

    # Parse modifiers (e.g., "bold red" -> base = "red")
    parts = color_name.lower().split()
    base_color = parts[-1] if parts else "white"

    # Special theme colors
    if base_color == "foreground":
        return foreground
    if base_color == "background":
        return background

    # ANSI color mapping
    if base_color in ANSI_COLOR_MAP:
        idx = ANSI_COLOR_MAP[base_color]
        if idx < len(palette):
            return palette[idx]

    # Already a hex color?
    if base_color.startswith("#"):
        return base_color

    # Fallback
    return "#ffffff"


def resolve_color_to_ansi_code(color_name: str) -> str:
    """
    Resolve a logical color name to an ANSI escape sequence.
    
    Supports modifiers and background colors:
    - "bold red" -> ESC[1;31m
    - "red on_green" -> ESC[31;42m
    - "bold underline cyan on_black" -> ESC[1;4;36;40m
    
    Args:
        color_name: Logical color name with optional modifiers
        
    Returns:
        ANSI escape sequence string
    """
    if not color_name:
        return ""

    parts = color_name.lower().split()
    modifiers: List[str] = []
    fg_code: str = ""
    bg_code: str = ""
    base_color: str = "white"

    for part in parts:
        if part in ANSI_MODIFIERS:
            modifiers.append(ANSI_MODIFIERS[part])
        elif part.startswith("on_"):
            bg_color = part[3:]  # Strip "on_" prefix
            if bg_color in ANSI_COLOR_MAP:
                idx = ANSI_COLOR_MAP[bg_color]
                bg_code = str(40 + idx) if idx < 8 else str(100 + idx - 8)
        else:
            base_color = part

    # Map foreground color
    if base_color in ANSI_COLOR_MAP:
        idx = ANSI_COLOR_MAP[base_color]
        fg_code = str(30 + idx) if idx < 8 else str(90 + idx - 8)
    elif base_color not in ("foreground", "background", "cursor", "none", "default"):
        fg_code = "37"  # Default white

    # Build ANSI sequence
    codes = modifiers.copy()
    if fg_code:
        codes.append(fg_code)
    if bg_code:
        codes.append(bg_code)

    if not codes:
        return ""

    return f"\033[{';'.join(codes)}m"
