# zashterminal/settings/highlights.py
"""
Smart Syntax Highlighting Manager for terminal output.

This module provides regex-based coloring rules that can be applied
to terminal output to highlight important patterns like IPs, errors,
dates, and other technical information.

Features:
- Layered configuration: System rules (read-only) + User rules (customizable)
- Theme-aware colors: Uses logical color names that map to active theme palette
- Multi-group regex: Supports coloring different capture groups differently
- Context-aware highlighting based on foreground process

Color names supported:
- ANSI colors: black, red, green, yellow, blue, magenta, cyan, white
- Bright variants: bright_black, bright_red, bright_green, etc.
- Theme colors: foreground, background, cursor
- Modifiers: bold, italic, underline (can be combined: "bold red")
"""

import json
import re
import threading
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple

import gi

gi.require_version("GObject", "2.0")
from gi.repository import GObject

from ..utils.logger import get_logger, log_error_with_context
from ..utils.security import ensure_secure_file_permissions
from .config import ColorSchemeMap, ColorSchemes, get_config_paths

# Mapping of logical color names to ANSI color indices (0-15)
# Standard ANSI: 0-7, Bright: 8-15
# NOTE: Also defined in ui/colors.py for UI components
ANSI_COLOR_MAP = {
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

# ANSI modifier codes
ANSI_MODIFIERS = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "blink": "5",
    "reverse": "7",
    "strikethrough": "9",
}


@dataclass(slots=True)
class HighlightRule:
    """
    Represents a single syntax highlighting rule with multi-group support.

    The `colors` list maps to regex capture groups:
    - colors[0] applies to group(1)
    - colors[1] applies to group(2)
    - etc.

    If a pattern has no capture groups, colors[0] is applied to the entire match.

    The `action` field controls processing after a match:
    - "next" (default): Continue processing other rules on this line
    - "stop": Stop processing further rules for this line after a match
    """

    name: str
    pattern: str
    colors: List[Optional[str]]  # List of color names for each capture group
    enabled: bool = True
    description: str = ""
    comment: str = ""  # Optional comment for documentation
    action: str = "next"  # "next" or "stop" - controls rule processing flow

    def __post_init__(self):
        """Ensure colors list has at least one default color and validate action."""
        if not self.colors:
            self.colors = ["white"]
        # Normalize action to valid values
        if self.action not in ("next", "stop"):
            self.action = "next"

    def to_dict(self) -> Dict[str, Any]:
        """Convert rule to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "pattern": self.pattern,
            "colors": self.colors,
            "enabled": self.enabled,
        }
        if self.description:
            result["description"] = self.description
        if self.comment:
            result["comment"] = self.comment
        # Only include action if not default
        if self.action != "next":
            result["action"] = self.action
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HighlightRule":
        """
        Create rule from dictionary.

        Expected JSON format:
        {
            "name": "rule_name",
            "pattern": "regex_pattern",
            "colors": ["red", "green"],  # Required: list of colors
            "enabled": true,
            "description": "optional",
            "comment": "optional",
            "action": "next"  # Optional: "next" (default) or "stop"
        }
        """
        colors = data.get("colors", [])
        if not colors:
            colors = ["white"]

        action = data.get("action", "next")
        if action not in ("next", "stop"):
            action = "next"

        return cls(
            name=data.get("name", ""),
            pattern=data.get("pattern", ""),
            colors=colors,
            enabled=data.get("enabled", True),
            description=data.get("description", ""),
            comment=data.get("comment", ""),
            action=action,
        )

    def is_valid(self) -> bool:
        """Check if the rule has a valid regex pattern."""
        if not self.pattern:
            return False
        try:
            re.compile(self.pattern)
            return True
        except re.error:
            return False


@dataclass(slots=True)
class HighlightContext:
    """
    Represents a command-specific highlighting context.

    A context contains rules specific to a command (e.g., ping, docker, df)
    that are applied instead of global rules when that command is running.

    Set use_global_rules=True to also include global rules alongside context rules.
    """

    command_name: str
    triggers: List[str] = field(default_factory=list)  # Commands that activate this context
    rules: List[HighlightRule] = field(default_factory=list)
    enabled: bool = True
    description: str = ""
    use_global_rules: bool = False  # Whether to include global rules with context rules

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for JSON serialization."""
        return {
            "name": self.command_name,
            "triggers": self.triggers,
            "rules": [rule.to_dict() for rule in self.rules],
            "enabled": self.enabled,
            "description": self.description,
            "use_global_rules": self.use_global_rules,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HighlightContext":
        """Create context from dictionary."""
        rules = [
            HighlightRule.from_dict(rule_data)
            for rule_data in data.get("rules", [])
        ]
        # Support both "name" and "command_name" keys
        name = data.get("name") or data.get("command_name", "")
        triggers = data.get("triggers", [name] if name else [])

        return cls(
            command_name=name,
            triggers=triggers,
            rules=rules,
            enabled=data.get("enabled", True),
            description=data.get("description", ""),
            use_global_rules=data.get("use_global_rules", False),
        )


@dataclass(slots=True)
class HighlightConfig:
    """Configuration for the highlighting system."""

    enabled_for_local: bool = False
    enabled_for_ssh: bool = False
    context_aware_enabled: bool = True
    global_rules: List[HighlightRule] = field(default_factory=list)
    contexts: Dict[str, HighlightContext] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for JSON serialization."""
        return {
            "enabled_for_local": self.enabled_for_local,
            "enabled_for_ssh": self.enabled_for_ssh,
            "context_aware_enabled": self.context_aware_enabled,
            "global_rules": [rule.to_dict() for rule in self.global_rules],
            "contexts": {name: ctx.to_dict() for name, ctx in self.contexts.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HighlightConfig":
        """Create config from dictionary."""
        # Support both "global_rules" and legacy "rules"
        rules_data = data.get("global_rules", data.get("rules", []))
        rules = [
            HighlightRule.from_dict(rule_data)
            for rule_data in rules_data
        ]
        contexts = {
            name: HighlightContext.from_dict(ctx_data)
            for name, ctx_data in data.get("contexts", {}).items()
        }
        return cls(
            enabled_for_local=data.get("enabled_for_local", False),
            enabled_for_ssh=data.get("enabled_for_ssh", False),
            context_aware_enabled=data.get("context_aware_enabled", True),
            global_rules=rules,
            contexts=contexts,
        )


class HighlightManager(GObject.GObject):
    """
    Manages syntax highlighting rules for terminal output.

    Implements a "Layered Configuration Loading" strategy:
    1. System Layer: Read-only JSON files from package data (updated with app)
    2. User Layer: JSON files from ~/.config/zashterminal/highlights/ (user overrides)

    If a user has a custom JSON for a context, it completely overrides the system one.

    Signals:
        rules-changed: Emitted when rules are added, removed, or modified.
        context-changed: Emitted when the active context changes.
    """

    __gsignals__ = {
        "rules-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "context-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, config_path: Optional[Path] = None, settings_manager=None):
        """
        Initialize the HighlightManager.

        Args:
            config_path: Optional custom path for user highlights directory.
            settings_manager: Optional SettingsManager for theme-aware colors.
        """
        GObject.GObject.__init__(self)
        self.logger = get_logger("zashterminal.settings.highlights")
        self._config_paths = get_config_paths()
        self._settings_manager = settings_manager

        # User highlights directory
        self._user_highlights_dir = config_path or (self._config_paths.CONFIG_DIR / "highlights")
        self._user_config_file = self._config_paths.CONFIG_DIR / "highlights_settings.json"

        # Configuration state
        self._config: HighlightConfig = HighlightConfig()
        self._compiled_pattern: Optional[Pattern] = None
        self._pattern_dirty = True
        self._lock = threading.RLock()

        # Trigger to context mapping (built from loaded contexts)
        self._trigger_map: Dict[str, str] = {}

        # Cache for resolved colors per theme
        self._color_cache: Dict[str, Dict[str, str]] = {}
        self._current_theme_name: str = ""

        # Load configuration
        self._load_layered_config()
        self.logger.info("HighlightManager initialized with layered config")

    def _get_system_highlights_path(self) -> Optional[Path]:
        """Get path to system highlight JSON files."""
        try:
            # Try to get path from package resources
            if hasattr(resources, 'files'):
                # Python 3.9+
                pkg_path = resources.files('zashterminal.data.highlights')
                if hasattr(pkg_path, '_path'):
                    return Path(pkg_path._path)
                # Fallback: construct path relative to this module
                return Path(__file__).parent.parent / "data" / "highlights"
            else:
                # Older Python - use pkg_resources style
                return Path(__file__).parent.parent / "data" / "highlights"
        except Exception as e:
            self.logger.warning(f"Could not locate system highlights: {e}")
            return Path(__file__).parent.parent / "data" / "highlights"

    def _load_layered_config(self) -> None:
        """Load configuration using layered approach."""
        with self._lock:
            try:
                # 1. Load user settings (enabled flags, disabled rule names)
                self._load_user_settings()

                # 2. Load system highlight rules (read-only base)
                system_contexts = self._load_system_highlights()

                # 3. Load user highlight rules (overrides)
                user_contexts = self._load_user_highlights()

                # 4. Merge: user overrides system
                merged_contexts = {**system_contexts, **user_contexts}
                self._config.contexts = merged_contexts

                # 5. Load global rules from system "global.json"
                global_ctx = merged_contexts.get("global")
                if global_ctx:
                    self._config.global_rules = global_ctx.rules
                    # Remove global from contexts as it's stored separately
                    del self._config.contexts["global"]

                # 6. Apply disabled states to global rules from user settings
                if hasattr(self, '_disabled_global_rules') and self._disabled_global_rules:
                    for rule in self._config.global_rules:
                        if rule.name in self._disabled_global_rules:
                            rule.enabled = False

                # 6b. Apply disabled states to contexts from user settings
                if hasattr(self, "_disabled_contexts") and self._disabled_contexts:
                    for ctx_name in self._disabled_contexts:
                        if ctx_name in self._config.contexts:
                            self._config.contexts[ctx_name].enabled = False

                # 7. Build trigger map
                self._build_trigger_map()

                self._pattern_dirty = True
                self.logger.info(
                    f"Loaded {len(self._config.contexts)} contexts, "
                    f"{len(self._config.global_rules)} global rules"
                )

            except Exception as e:
                self.logger.error(f"Failed to load layered config: {e}")
                log_error_with_context(e, "loading layered config", "zashterminal.highlights")
                self._create_default_config()

    def _load_user_settings(self) -> None:
        """Load user settings (enabled flags, disabled global rules, and disabled contexts)."""
        self._disabled_global_rules: set = set()  # Store for applying after rules load
        self._disabled_contexts: set = set()  # Store for applying after contexts load
        try:
            if self._user_config_file.exists():
                with open(self._user_config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._config.enabled_for_local = data.get("enabled_for_local", False)
                self._config.enabled_for_ssh = data.get("enabled_for_ssh", False)
                self._config.context_aware_enabled = data.get("context_aware_enabled", True)
                # Store disabled global rule names for later application
                self._disabled_global_rules = set(data.get("disabled_global_rules", []))
                # Store disabled context names for later application
                self._disabled_contexts = set(data.get("disabled_contexts", []))
        except Exception as e:
            self.logger.warning(f"Failed to load user settings: {e}")

    def _load_system_highlights(self) -> Dict[str, HighlightContext]:
        """Load highlight rules from system package data."""
        contexts = {}
        system_path = self._get_system_highlights_path()

        if not system_path or not system_path.exists():
            self.logger.warning(f"System highlights path not found: {system_path}")
            return contexts

        try:
            for json_file in system_path.glob("*.json"):
                try:
                    ctx = self._load_context_from_file(json_file)
                    if ctx:
                        contexts[ctx.command_name] = ctx
                        self.logger.debug(f"Loaded system context: {ctx.command_name}")
                except Exception as e:
                    self.logger.warning(f"Failed to load system highlight {json_file}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to scan system highlights: {e}")

        return contexts

    def _load_user_highlights(self) -> Dict[str, HighlightContext]:
        """Load highlight rules from user config directory."""
        contexts = {}

        if not self._user_highlights_dir.exists():
            return contexts

        try:
            for json_file in self._user_highlights_dir.glob("*.json"):
                try:
                    ctx = self._load_context_from_file(json_file)
                    if ctx:
                        contexts[ctx.command_name] = ctx
                        self.logger.debug(f"Loaded user context: {ctx.command_name}")
                except Exception as e:
                    self.logger.warning(f"Failed to load user highlight {json_file}: {e}")
        except Exception as e:
            self.logger.error(f"Failed to scan user highlights: {e}")

        return contexts

    def _load_context_from_file(self, file_path: Path) -> Optional[HighlightContext]:
        """Load a highlight context from a JSON file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return HighlightContext.from_dict(data)
        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in {file_path}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to load {file_path}: {e}")
            return None

    def _build_trigger_map(self) -> None:
        """Build mapping from trigger commands to context names."""
        self._trigger_map.clear()
        for ctx_name, ctx in self._config.contexts.items():
            for trigger in ctx.triggers:
                self._trigger_map[trigger.lower()] = ctx_name

    def _create_default_config(self) -> None:
        """Create minimal default configuration if all loading fails."""
        self._config = HighlightConfig(
            enabled_for_local=False,
            enabled_for_ssh=False,
            context_aware_enabled=True,
            global_rules=[],
            contexts={},
        )
        self._pattern_dirty = True

    def save_config(self) -> None:
        """Save user settings and any user-modified contexts."""
        with self._lock:
            try:
                # Save user settings
                self._save_user_settings()

                self.logger.info("Saved highlight configuration")
            except Exception as e:
                self.logger.error(f"Failed to save highlight config: {e}")
                log_error_with_context(e, "saving highlight config", "zashterminal.highlights")

    def _save_user_settings(self) -> None:
        """Save user settings (enabled flags, disabled global rules, and disabled contexts)."""
        try:
            self._user_config_file.parent.mkdir(parents=True, exist_ok=True)

            # Collect names of disabled global rules
            disabled_global_rules = [
                rule.name for rule in self._config.global_rules
                if not rule.enabled
            ]

            # Collect names of disabled contexts
            disabled_contexts = [
                ctx_name
                for ctx_name, ctx in self._config.contexts.items()
                if not ctx.enabled
            ]

            settings = {
                "enabled_for_local": self._config.enabled_for_local,
                "enabled_for_ssh": self._config.enabled_for_ssh,
                "context_aware_enabled": self._config.context_aware_enabled,
                "disabled_global_rules": disabled_global_rules,
                "disabled_contexts": disabled_contexts,
            }

            temp_file = self._user_config_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)

            temp_file.replace(self._user_config_file)

            try:
                ensure_secure_file_permissions(str(self._user_config_file))
            except Exception as e:
                self.logger.warning(f"Failed to set secure permissions: {e}")

        except Exception as e:
            self.logger.error(f"Failed to save user settings: {e}")

    def save_context_to_user(self, context: HighlightContext) -> None:
        """Save a context to user highlights directory (creates override)."""
        with self._lock:
            try:
                self._user_highlights_dir.mkdir(parents=True, exist_ok=True)

                file_path = self._user_highlights_dir / f"{context.command_name}.json"
                temp_file = file_path.with_suffix(".tmp")

                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(context.to_dict(), f, indent=2, ensure_ascii=False)

                temp_file.replace(file_path)

                try:
                    ensure_secure_file_permissions(str(file_path))
                except Exception as e:
                    self.logger.warning(f"Failed to set secure permissions: {e}")

                # Update in-memory config
                self._config.contexts[context.command_name] = context
                self._build_trigger_map()
                self._pattern_dirty = True

                self.logger.info(f"Saved user context: {context.command_name}")

            except Exception as e:
                self.logger.error(f"Failed to save context {context.command_name}: {e}")

    def save_global_rules_to_user(self) -> None:
        """
        Save global rules to user highlights directory as global.json.

        This creates a user override for the system global.json file,
        persisting any modifications made to global rules.
        """
        with self._lock:
            try:
                self._user_highlights_dir.mkdir(parents=True, exist_ok=True)

                file_path = self._user_highlights_dir / "global.json"
                temp_file = file_path.with_suffix(".tmp")

                # Create a context-like structure for global rules
                global_data = {
                    "name": "global",
                    "triggers": [],
                    "rules": [rule.to_dict() for rule in self._config.global_rules],
                    "enabled": True,
                    "description": "Global highlight rules applied to all terminal output",
                    "use_global_rules": False,
                }

                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(global_data, f, indent=2, ensure_ascii=False)

                temp_file.replace(file_path)

                try:
                    ensure_secure_file_permissions(str(file_path))
                except Exception as e:
                    self.logger.warning(f"Failed to set secure permissions: {e}")

                self._pattern_dirty = True
                self.logger.info(
                    f"Saved {len(self._config.global_rules)} global rules to user directory"
                )

            except Exception as e:
                self.logger.error(f"Failed to save global rules: {e}")
                log_error_with_context(e, "saving global rules", "zashterminal.highlights")

    def delete_user_context(self, command_name: str) -> bool:
        """Delete a user context override (reverts to system version if exists)."""
        with self._lock:
            try:
                file_path = self._user_highlights_dir / f"{command_name}.json"
                if file_path.exists():
                    file_path.unlink()
                    self.logger.info(f"Deleted user context: {command_name}")

                    # Reload to get system version back
                    self._load_layered_config()
                    self.emit("rules-changed")
                    return True
                return False
            except Exception as e:
                self.logger.error(f"Failed to delete context {command_name}: {e}")
                return False

    def has_user_context_override(self, command_name: str) -> bool:
        """Check if a context has a user override file."""
        file_path = self._user_highlights_dir / f"{command_name}.json"
        return file_path.exists()

    # =========================================================================
    # Color Resolution Methods
    # =========================================================================

    def set_settings_manager(self, settings_manager) -> None:
        """Set the settings manager for theme-aware color resolution."""
        self._settings_manager = settings_manager
        self._color_cache.clear()  # Invalidate cache

    def get_current_theme_palette(self) -> Dict[str, str]:
        """Get the current theme's color palette."""
        if not self._settings_manager:
            self._current_theme_name = "default"
            return self._get_default_palette()

        try:
            # Get current scheme index
            scheme_index = self._settings_manager.get("color_scheme", 0)
            scheme_order = ColorSchemeMap.SCHEME_ORDER

            if 0 <= scheme_index < len(scheme_order):
                scheme_name = scheme_order[scheme_index]
            else:
                scheme_name = "dracula"

            # Update current theme name for cache invalidation
            self._current_theme_name = scheme_name

            schemes = ColorSchemes.get_schemes()
            if scheme_name in schemes:
                scheme = schemes[scheme_name]
                return {
                    "foreground": scheme["foreground"],
                    "background": scheme["background"],
                    "cursor": scheme.get("cursor", scheme["foreground"]),
                    "palette": scheme["palette"],
                }
        except Exception as e:
            self.logger.warning(f"Failed to get theme palette: {e}")

        self._current_theme_name = "default"
        return self._get_default_palette()

    def _get_default_palette(self) -> Dict[str, str]:
        """Get default Dracula-inspired palette."""
        return {
            "foreground": "#f8f8f2",
            "background": "#282a36",
            "cursor": "#f8f8f2",
            "palette": [
                "#000000", "#ff5555", "#50fa7b", "#f1fa8c",
                "#bd93f9", "#ff79c6", "#8be9fd", "#bfbfbf",
                "#4d4d4d", "#ff6e67", "#5af78e", "#f4f99d",
                "#caa9fa", "#ff92d0", "#9aedfe", "#e6e6e6",
            ],
        }

    def resolve_color(self, color_name: str) -> str:
        """
        Resolve a logical color name to a hex color string.

        Args:
            color_name: Logical name like "red", "bold green", "bright_cyan"

        Returns:
            Hex color string like "#ff5555"
        """
        if not color_name:
            return "#ffffff"

        # Check cache using theme name (fast lookup)
        palette = self.get_current_theme_palette()
        cache_key = self._current_theme_name or "default"

        if cache_key not in self._color_cache:
            self._color_cache[cache_key] = {}

        if color_name in self._color_cache[cache_key]:
            return self._color_cache[cache_key][color_name]

        # Parse color name (may include modifiers like "bold red")
        parts = color_name.lower().split()
        base_color = parts[-1] if parts else "white"

        # Resolve to hex
        hex_color = self._resolve_base_color(base_color, palette)

        self._color_cache[cache_key][color_name] = hex_color
        return hex_color

    def _resolve_base_color(self, color_name: str, palette: Dict[str, str]) -> str:
        """Resolve a base color name to hex using theme palette."""
        # Special theme colors
        if color_name == "foreground":
            return palette.get("foreground", "#ffffff")
        if color_name == "background":
            return palette.get("background", "#000000")
        if color_name == "cursor":
            return palette.get("cursor", "#ffffff")

        # ANSI color mapping
        if color_name in ANSI_COLOR_MAP:
            idx = ANSI_COLOR_MAP[color_name]
            theme_palette = palette.get("palette", [])
            if idx < len(theme_palette):
                return theme_palette[idx]

        # Already a hex color?
        if color_name.startswith("#"):
            return color_name

        # Fallback to white
        return "#ffffff"

    def resolve_color_to_ansi(self, color_name: str) -> str:
        """
        Resolve a logical color name to ANSI escape sequence.

        Uses standard ANSI color indices (30-37, 90-97) so the terminal
        automatically applies the active color scheme's palette.

        Supports:
        - Modifiers: "bold red", "underline green", etc.
        - Background colors: "on_red", "on_bright_blue", etc.
        - Combined: "bold red on_yellow" (bold red text on yellow background)

        Args:
            color_name: Logical name like "red", "bold green on_blue", "bright_cyan"

        Returns:
            ANSI escape sequence like "\033[1;31;42m" (bold red on green)
        """
        if not color_name:
            return ""

        # Parse modifiers, foreground color, and background color
        parts = color_name.lower().split()
        modifiers = []
        base_color = "white"
        bg_color = None

        for part in parts:
            if part in ANSI_MODIFIERS:
                modifiers.append(ANSI_MODIFIERS[part])
            elif part.startswith("on_"):
                # Background color (e.g., "on_red", "on_bright_blue")
                bg_color = part[3:]  # Strip "on_" prefix
            else:
                base_color = part

        # Map foreground color name to ANSI color code
        # Standard colors: 30-37, Bright colors: 90-97
        fg_code = None
        if base_color in ANSI_COLOR_MAP:
            color_index = ANSI_COLOR_MAP[base_color]
            if color_index < 8:
                # Standard colors: 30-37
                fg_code = str(30 + color_index)
            else:
                # Bright colors: 90-97
                fg_code = str(90 + (color_index - 8))
        elif base_color not in (
            "foreground",
            "background",
            "cursor",
            "none",
            "default",
        ):
            # Unknown color - use default white
            fg_code = "37"

        # Map background color name to ANSI color code
        # Standard colors: 40-47, Bright colors: 100-107
        bg_code = None
        if bg_color and bg_color in ANSI_COLOR_MAP:
            color_index = ANSI_COLOR_MAP[bg_color]
            if color_index < 8:
                # Standard background colors: 40-47
                bg_code = str(40 + color_index)
            else:
                # Bright background colors: 100-107
                bg_code = str(100 + (color_index - 8))

        # Build ANSI sequence: modifiers + foreground + background
        ansi_parts = modifiers.copy()
        if fg_code:
            ansi_parts.append(fg_code)
        if bg_code:
            ansi_parts.append(bg_code)

        if ansi_parts:
            return f"\033[{';'.join(ansi_parts)}m"
        return ""

    # =========================================================================
    # Trigger / Context Methods
    # =========================================================================

    def get_all_triggers(self) -> Set[str]:
        """
        Get all command triggers from loaded contexts.

        Returns:
            Set of command names that should be tracked for context detection.
        """
        with self._lock:
            return set(self._trigger_map.keys())

    def get_context_for_command(self, command: str) -> Optional[str]:
        """
        Get the context name for a given command.

        Args:
            command: Command name (e.g., "ping", "docker")

        Returns:
            Context name if found, None otherwise.
        """
        with self._lock:
            return self._trigger_map.get(command.lower())

    # =========================================================================
    # Property Accessors
    # =========================================================================

    @property
    def enabled_for_local(self) -> bool:
        """Get whether highlighting is enabled for local terminals."""
        return self._config.enabled_for_local

    @enabled_for_local.setter
    def enabled_for_local(self, value: bool) -> None:
        """Set whether highlighting is enabled for local terminals."""
        with self._lock:
            self._config.enabled_for_local = value

    @property
    def enabled_for_ssh(self) -> bool:
        """Get whether highlighting is enabled for SSH sessions."""
        return self._config.enabled_for_ssh

    @enabled_for_ssh.setter
    def enabled_for_ssh(self, value: bool) -> None:
        """Set whether highlighting is enabled for SSH sessions."""
        with self._lock:
            self._config.enabled_for_ssh = value

    @property
    def context_aware_enabled(self) -> bool:
        """Get whether context-aware highlighting is enabled."""
        return self._config.context_aware_enabled

    @context_aware_enabled.setter
    def context_aware_enabled(self, value: bool) -> None:
        """Set whether context-aware highlighting is enabled."""
        with self._lock:
            self._config.context_aware_enabled = value

    @property
    def contexts(self) -> Dict[str, HighlightContext]:
        """Get all highlight contexts."""
        return self._config.contexts.copy()

    @property
    def rules(self) -> List[HighlightRule]:
        """Get list of global highlight rules."""
        return self._config.global_rules.copy()

    # =========================================================================
    # Context Management
    # =========================================================================

    def get_context(self, command_name: str) -> Optional[HighlightContext]:
        """Get a context by command name."""
        with self._lock:
            return self._config.contexts.get(command_name)

    def get_context_names(self) -> List[str]:
        """Get list of all context command names."""
        with self._lock:
            return list(self._config.contexts.keys())

    def add_context(self, context: HighlightContext) -> None:
        """Add or update a highlight context."""
        with self._lock:
            self._config.contexts[context.command_name] = context
            self._build_trigger_map()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def remove_context(self, command_name: str) -> bool:
        """Remove a context by command name."""
        with self._lock:
            if command_name in self._config.contexts:
                del self._config.contexts[command_name]
                self._build_trigger_map()
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def set_context_enabled(self, command_name: str, enabled: bool) -> bool:
        """Enable or disable a context."""
        with self._lock:
            if command_name in self._config.contexts:
                self._config.contexts[command_name].enabled = enabled
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def set_context_use_global_rules(self, command_name: str, use_global: bool) -> bool:
        """Set whether a context should include global rules."""
        with self._lock:
            if command_name in self._config.contexts:
                self._config.contexts[command_name].use_global_rules = use_global
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def get_context_use_global_rules(self, command_name: str) -> bool:
        """Get whether a context includes global rules."""
        with self._lock:
            if command_name in self._config.contexts:
                return self._config.contexts[command_name].use_global_rules
            return False

    # =========================================================================
    # Rule Management
    # =========================================================================

    def get_rules_for_context(self, command_name: str) -> List[HighlightRule]:
        """
        Get rules for a specific context.

        By default, context-specific rules replace global rules.
        If use_global_rules is enabled for the context, global rules are
        included first, followed by context-specific rules.

        Args:
            command_name: The command name to get rules for.

        Returns:
            List of HighlightRule for the context (context-only or global+context).
        """
        with self._lock:
            # Check if we have a context for this command
            if (
                self._config.context_aware_enabled
                and command_name
                and command_name in self._config.contexts
            ):
                ctx = self._config.contexts[command_name]
                if ctx.enabled:
                    # Get context-specific rules
                    context_rules = [r for r in ctx.rules if r.enabled and r.is_valid()]

                    # Check if this context should include global rules
                    if ctx.use_global_rules:
                        # Global rules first, then context rules
                        global_rules = [r for r in self._config.global_rules if r.enabled and r.is_valid()]
                        return global_rules + context_rules
                    else:
                        # Context rules only (new default behavior)
                        return context_rules

            # No context found - use global rules only
            return [r for r in self._config.global_rules if r.enabled and r.is_valid()]

    def get_rule(self, index: int) -> Optional[HighlightRule]:
        """Get a global rule by index."""
        with self._lock:
            if 0 <= index < len(self._config.global_rules):
                return self._config.global_rules[index]
            return None

    def add_rule(self, rule: HighlightRule) -> None:
        """Add a new global highlight rule."""
        with self._lock:
            self._config.global_rules.append(rule)
            self._pattern_dirty = True
        self.emit("rules-changed")

    def update_rule(self, index: int, rule: HighlightRule) -> bool:
        """Update an existing global rule."""
        with self._lock:
            if 0 <= index < len(self._config.global_rules):
                self._config.global_rules[index] = rule
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def remove_rule(self, index: int) -> bool:
        """Remove a global rule."""
        with self._lock:
            if 0 <= index < len(self._config.global_rules):
                del self._config.global_rules[index]
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def set_rule_enabled(self, index: int, enabled: bool) -> bool:
        """Enable or disable a global rule."""
        with self._lock:
            if 0 <= index < len(self._config.global_rules):
                self._config.global_rules[index].enabled = enabled
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    # =========================================================================
    # Context Rule Management
    # =========================================================================

    def add_rule_to_context(self, command_name: str, rule: HighlightRule) -> bool:
        """Add a rule to a specific context."""
        with self._lock:
            if command_name in self._config.contexts:
                self._config.contexts[command_name].rules.append(rule)
                self._pattern_dirty = True
                self.emit("rules-changed")
                return True
            return False

    def update_context_rule(self, command_name: str, index: int, rule: HighlightRule) -> bool:
        """Update a rule in a specific context."""
        with self._lock:
            if command_name in self._config.contexts:
                ctx = self._config.contexts[command_name]
                if 0 <= index < len(ctx.rules):
                    ctx.rules[index] = rule
                    self._pattern_dirty = True
                    self.emit("rules-changed")
                    return True
            return False

    def remove_context_rule(self, command_name: str, index: int) -> bool:
        """Remove a rule from a specific context."""
        with self._lock:
            if command_name in self._config.contexts:
                ctx = self._config.contexts[command_name]
                if 0 <= index < len(ctx.rules):
                    del ctx.rules[index]
                    self._pattern_dirty = True
                    self.emit("rules-changed")
                    return True
            return False

    def set_context_rule_enabled(self, command_name: str, index: int, enabled: bool) -> bool:
        """Enable or disable a rule in a specific context."""
        with self._lock:
            if command_name in self._config.contexts:
                ctx = self._config.contexts[command_name]
                if 0 <= index < len(ctx.rules):
                    ctx.rules[index].enabled = enabled
                    self._pattern_dirty = True
                    self.emit("rules-changed")
                    return True
            return False

    def move_context_rule(self, command_name: str, from_index: int, to_index: int) -> bool:
        """Move a rule to a new position in a context's rule list."""
        with self._lock:
            if command_name not in self._config.contexts:
                return False

            ctx = self._config.contexts[command_name]
            if not (0 <= from_index < len(ctx.rules) and 0 <= to_index < len(ctx.rules)):
                return False

            # Remove from old position and insert at new position
            rule = ctx.rules.pop(from_index)
            ctx.rules.insert(to_index, rule)

            self._pattern_dirty = True
            self.emit("rules-changed")
            return True

    # =========================================================================
    # Utilities
    # =========================================================================

    def reset_to_defaults(self) -> None:
        """Reset all rules to default configuration."""
        with self._lock:
            # Delete all user highlight files
            if self._user_highlights_dir.exists():
                for json_file in self._user_highlights_dir.glob("*.json"):
                    try:
                        json_file.unlink()
                    except Exception as e:
                        self.logger.warning(f"Failed to delete {json_file}: {e}")

            # Reload from system
            self._load_layered_config()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def reset_global_rules(self) -> None:
        """Reset only global rules to system defaults (keeps context customizations)."""
        with self._lock:
            # Only delete global.json from user directory
            if self._user_highlights_dir.exists():
                global_file = self._user_highlights_dir / "global.json"
                if global_file.exists():
                    try:
                        global_file.unlink()
                        self.logger.info("Deleted user global.json")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete global.json: {e}")

            # Reload from system
            self._load_layered_config()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def reset_all_contexts(self) -> None:
        """Reset all context customizations to system defaults (keeps global rules)."""
        with self._lock:
            # Delete all user context files except global.json
            if self._user_highlights_dir.exists():
                for json_file in self._user_highlights_dir.glob("*.json"):
                    if json_file.name != "global.json":
                        try:
                            json_file.unlink()
                            self.logger.info(f"Deleted user context: {json_file.name}")
                        except Exception as e:
                            self.logger.warning(f"Failed to delete {json_file}: {e}")

            # Reload from system
            self._load_layered_config()
            self._pattern_dirty = True
        self.emit("rules-changed")

    def validate_pattern(self, pattern: str) -> Tuple[bool, str]:
        """Validate a regex pattern."""
        if not pattern:
            return False, "Pattern cannot be empty"
        try:
            re.compile(pattern)
            return True, ""
        except re.error as e:
            return False, str(e)

    def is_enabled_for_terminal_type(self, terminal_type: str) -> bool:
        """Check if highlighting is enabled for the given terminal type."""
        if terminal_type == "local":
            return self._config.enabled_for_local
        elif terminal_type in ("ssh", "sftp"):
            return self._config.enabled_for_ssh
        return False


# Singleton instance
_highlight_manager: Optional[HighlightManager] = None
_manager_lock = threading.Lock()


def get_highlight_manager() -> HighlightManager:
    """Get the global HighlightManager instance."""
    global _highlight_manager
    if _highlight_manager is None:
        with _manager_lock:
            if _highlight_manager is None:
                _highlight_manager = HighlightManager()
    return _highlight_manager
