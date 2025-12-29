# zashterminal/utils/tooltip_helper.py
"""
Tooltip helper for showing helpful explanations on UI elements.
Provides a simple way to add custom tooltips with fade animation to any GTK widget.
Replaces the default GTK tooltip system with a more visually appealing popover-based approach.

On X11 with compositor, the popover-based approach can cause segfaults, so we
fall back to native GTK tooltips on X11 backends.
"""

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

if TYPE_CHECKING:
    from ..settings.manager import SettingsManager


def _is_x11_backend() -> bool:
    """Check if we're running on X11 backend (not Wayland)."""
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return False
        # Check display type name - X11 displays have "X11" in the type name
        display_type = type(display).__name__
        return "X11" in display_type or "Gdk.X11Display" in str(type(display))
    except Exception:
        # If we can't determine, assume not X11 to preserve original behavior
        return False


# Singleton instance
_tooltip_helper_instance: "TooltipHelper | None" = None
_app_instance = None


def get_tooltip_helper() -> "TooltipHelper":
    """
    Get the global TooltipHelper instance.

    Returns:
        The singleton TooltipHelper instance.
    """
    global _tooltip_helper_instance
    if _tooltip_helper_instance is None:
        _tooltip_helper_instance = TooltipHelper()
    return _tooltip_helper_instance


def init_tooltip_helper(
    settings_manager: "SettingsManager" = None, app=None
) -> "TooltipHelper":
    """
    Initialize the global TooltipHelper with a settings manager.

    Should be called once during application startup with the settings manager.

    Args:
        settings_manager: The settings manager for checking tooltip preferences.
        app: The Gtk.Application instance for looking up keyboard shortcuts.

    Returns:
        The initialized TooltipHelper instance.
    """
    global _tooltip_helper_instance, _app_instance
    _app_instance = app
    _tooltip_helper_instance = TooltipHelper(settings_manager, app)
    return _tooltip_helper_instance


class TooltipHelper:
    """
    Manages a single, reusable Gtk.Popover to display custom tooltips.

    Uses a singleton popover to prevent state conflicts. The animation is handled
    by CSS classes, and the fade-in is reliably triggered by hooking into the
    popover's "map" signal. This avoids race conditions with the GTK renderer.

    On X11 with compositor, uses native GTK tooltips to avoid segfaults.

    Usage:
        tooltip_helper = TooltipHelper(settings_manager)
        tooltip_helper.add_tooltip(widget, "My tooltip text")
    """

    def __init__(self, settings_manager=None, app=None):
        """
        Initialize the tooltip helper.

        Args:
            settings_manager: Optional settings manager to check if tooltips are enabled.
            app: Optional Gtk.Application for looking up keyboard shortcuts.
        """
        self.settings_manager = settings_manager
        self.app = app

        # Detect X11 backend - use native tooltips to avoid segfaults
        self._use_native_tooltips = _is_x11_backend()

        # State machine variables
        self.active_widget = None
        self.show_timer_id = None
        self._is_cleaning_up = False
        self._suppressed = False  # When True, tooltips are temporarily suppressed

        # Only create popover-based tooltip if not on X11
        if not self._use_native_tooltips:
            # The single, reusable popover
            self.popover = Gtk.Popover()
            self.popover.set_autohide(False)
            # Modern tooltip design without arrow - cleaner UI (inspired by Linear, Figma)
            self.popover.set_has_arrow(False)
            self.popover.set_position(Gtk.PositionType.TOP)
            # Generous vertical offset for visual breathing room
            self.popover.set_offset(0, -12)

            self.label = Gtk.Label(
                wrap=True,
                max_width_chars=45,
                margin_start=16,
                margin_end=16,
                margin_top=10,
                margin_bottom=10,
                halign=Gtk.Align.CENTER,
            )
            self.popover.set_child(self.label)

            # CSS for tooltip animation is now loaded globally from components.css
            # Classes: .tooltip-popover, .tooltip-popover.visible
            self.popover.add_css_class("tooltip-popover")

            # Connect to the "map" signal to trigger the fade-in animation
            self.popover.connect("map", self._on_popover_map)
        else:
            self.popover = None
            self.label = None

        # Separate provider for color styling (can be updated dynamically)
        self._color_css_provider = None

    def update_colors(
        self,
        bg_color: str = None,
        fg_color: str = None,
        use_terminal_theme: bool = False,
    ):
        """
        Update tooltip colors to match the application theme.

        On X11, this is a no-op since we use native GTK tooltips.

        Args:
            bg_color: Background color hex (e.g., "#1e1e1e")
            fg_color: Foreground/text color hex (e.g., "#ffffff")
            use_terminal_theme: If True and settings_manager is available,
                               use terminal color scheme colors.
        """
        # Skip color updates on X11 - using native tooltips
        if self._use_native_tooltips:
            return

        if use_terminal_theme and self.settings_manager:
            gtk_theme = self.settings_manager.get("gtk_theme", "")
            if gtk_theme == "terminal":
                scheme = self.settings_manager.get_color_scheme_data()
                # Use headerbar_background for the actual tooltip color
                bg_color = scheme.get(
                    "headerbar_background", scheme.get("background", "#1e1e1e")
                )
                fg_color = scheme.get("foreground", "#ffffff")
            else:
                # For non-terminal themes, detect colors from GTK/Adwaita theme
                try:
                    style_manager = Adw.StyleManager.get_default()
                    is_dark = style_manager.get_dark()
                    if is_dark:
                        # Dark theme colors (Adwaita dark)
                        # Use darker base to compensate for _adjust_tooltip_background lightening
                        bg_color = "#1a1a1a"
                        fg_color = "#ffffff"
                    else:
                        # Light theme colors (Adwaita light)
                        bg_color = "#fafafa"
                        fg_color = "#2e2e2e"
                except Exception:
                    # Fallback to dark theme defaults
                    bg_color = "#2a2a2a"
                    fg_color = "#ffffff"

        # Skip if no colors specified and not using terminal theme mode
        if not bg_color and not fg_color:
            return

        # Adjust tooltip background to be subtly different from window bg
        tooltip_bg = bg_color
        is_dark_theme = False
        if bg_color:
            tooltip_bg = self._adjust_tooltip_background(bg_color)
            # Detect if original window bg is dark theme
            try:
                hex_val = bg_color.lstrip("#")
                r = int(hex_val[0:2], 16)
                g = int(hex_val[2:4], 16)
                b = int(hex_val[4:6], 16)
                luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
                is_dark_theme = luminance < 0.5
            except (ValueError, IndexError):
                pass

        # Set subtle border color based on theme
        # Just enough contrast to define the tooltip edge
        if is_dark_theme:
            # Subtle light border for dark theme
            border_color = "#707070"
        else:
            # Subtle darker border for light theme
            border_color = "#a0a0a0"

        # Build CSS with maximum specificity selectors
        # GTK4 CSS - only use supported properties
        css_parts = ["popover.tooltip-popover > contents {"]
        if tooltip_bg:
            css_parts.append(f"    background-color: {tooltip_bg};")
            css_parts.append("    background-image: none;")
        if fg_color:
            css_parts.append(f"    color: {fg_color};")
        # Use solid 1px border with hex color for maximum compatibility
        css_parts.append(f"    border: 1px solid {border_color};")
        css_parts.append("    border-radius: 8px;")
        css_parts.append("}")

        # Also style the label
        if fg_color:
            css_parts.append(f"popover.tooltip-popover label {{ color: {fg_color}; }}")

        css = "\n".join(css_parts)

        # Get display - required for CSS provider operations
        display = Gdk.Display.get_default()
        if not display:
            return

        # Remove existing color provider if any
        if self._color_css_provider:
            try:
                Gtk.StyleContext.remove_provider_for_display(
                    display, self._color_css_provider
                )
            except Exception:
                pass
            self._color_css_provider = None

        # Add new color provider with highest priority
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        try:
            # Use APPLICATION priority which is higher than USER
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 100,
            )
            self._color_css_provider = provider
        except Exception:
            pass

    def _adjust_tooltip_background(self, bg_color: str) -> str:
        """
        Adjust the tooltip background color to be visually distinct from window.

        For dark themes (luminance < 0.5), lighten the color moderately.
        For light themes (luminance >= 0.5), darken the color moderately.

        Args:
            bg_color: The base background color hex (e.g., "#1e1e1e")

        Returns:
            Adjusted color hex string.
        """
        try:
            hex_val = bg_color.lstrip("#")
            r = int(hex_val[0:2], 16)
            g = int(hex_val[2:4], 16)
            b = int(hex_val[4:6], 16)

            # Calculate luminance
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255

            if luminance < 0.5:
                # Dark theme - lighten moderately for subtle but visible difference
                # Reduced from 110 to 50 for more subtle appearance
                adjustment = 50
                r = min(255, r + adjustment)
                g = min(255, g + adjustment)
                b = min(255, b + adjustment)
            else:
                # Light theme - darken moderately
                adjustment = 30
                r = max(0, r - adjustment)
                g = max(0, g - adjustment)
                b = max(0, b - adjustment)

            return f"#{r:02x}{g:02x}{b:02x}"
        except (ValueError, IndexError):
            return bg_color

    def _on_popover_map(self, popover):
        """
        Triggered when the popover is mapped (shown).
        Adds the 'visible' CSS class to initiate the fade-in animation.
        """

        def add_visible_class():
            # Verify popover is still valid and mapped before adding CSS class
            # This prevents segfaults on X11 when popover is destroyed
            # between the timer being set and this callback executing
            try:
                if popover and popover.is_visible():
                    popover.add_css_class("visible")
            except Exception:
                pass
            return False

        # A small delay ensures the CSS transition triggers correctly
        GLib.timeout_add(10, add_visible_class)

    def is_enabled(self) -> bool:
        """Check if tooltips are enabled in settings."""
        if self.settings_manager is None:
            return True
        return self.settings_manager.get("show_tooltips", True)

    def _get_shortcut_label(self, action_name: str) -> str | None:
        """
        Get the human-readable label for a keyboard shortcut.

        Args:
            action_name: The action name (e.g., "toggle-search", "new-local-tab")

        Returns:
            The shortcut label (e.g., "Ctrl+Shift+F") or None if no shortcut.
        """
        if not self.app:
            return None

        # Import here to avoid circular dependency
        from ..helpers import accelerator_to_label

        # Try both win. and app. prefixes
        for prefix in ("win", "app"):
            full_action = f"{prefix}.{action_name}"
            accels = self.app.get_accels_for_action(full_action)
            if accels:
                return accelerator_to_label(accels[0])
        return None

    def add_tooltip_with_shortcut(
        self,
        widget: Gtk.Widget,
        tooltip_text: str,
        action_name: str,
    ) -> None:
        """
        Add a tooltip that includes the keyboard shortcut for an action.

        The shortcut is dynamically looked up, so if the user changes it,
        the tooltip will automatically reflect the new shortcut.

        Args:
            widget: The GTK widget to add the tooltip to.
            tooltip_text: The base tooltip text.
            action_name: The action name to look up shortcut for (e.g., "toggle-search").
        """
        if not tooltip_text:
            return

        # Build tooltip text with shortcut
        shortcut = self._get_shortcut_label(action_name)
        if shortcut:
            full_text = f"{tooltip_text} ({shortcut})"
        else:
            full_text = tooltip_text

        # On X11, use native GTK tooltip to avoid segfaults
        if self._use_native_tooltips:
            widget.set_tooltip_text(full_text)
            return

        # Store base text and action name for dynamic lookup
        widget._custom_tooltip_base_text = tooltip_text
        widget._custom_tooltip_action = action_name
        widget._custom_tooltip_text = full_text

        # Clear any existing default tooltip
        widget.set_tooltip_text(None)

        # Add motion controller for enter/leave events
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("enter", self._on_enter_with_shortcut, widget)
        motion_controller.connect("leave", self._on_leave)
        widget.add_controller(motion_controller)

    def add_tooltip(self, widget: Gtk.Widget, tooltip_text: str) -> None:
        """
        Connects a widget to the tooltip management system with custom text.

        This replaces the widget's default tooltip with a custom animated popover.
        The widget's existing tooltip_text property will be cleared.
        On X11 backends, uses native GTK tooltips to avoid segfaults.

        Args:
            widget: The GTK widget to add the tooltip to.
            tooltip_text: The text to display in the tooltip.
        """
        if not tooltip_text:
            return

        # On X11, use native GTK tooltip to avoid segfaults
        if self._use_native_tooltips:
            widget.set_tooltip_text(tooltip_text)
            return

        # Store tooltip text on the widget
        widget._custom_tooltip_text = tooltip_text

        # Clear any existing default tooltip
        widget.set_tooltip_text(None)

        # Add motion controller for enter/leave events
        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect("enter", self._on_enter, widget)
        motion_controller.connect("leave", self._on_leave)
        widget.add_controller(motion_controller)

    def _clear_timer(self):
        """Clear any pending show timer."""
        if self.show_timer_id:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None

    def _on_enter(self, controller, x, y, widget):
        """Handle mouse entering a widget with a tooltip."""
        if self._is_cleaning_up:
            return

        if not self.is_enabled():
            return

        # If suppressed, ignore this enter event
        if self._suppressed:
            return

        if self.active_widget == widget:
            return

        self._clear_timer()
        self._hide_tooltip()

        self.active_widget = widget
        # Show tooltip after 350ms delay
        self.show_timer_id = GLib.timeout_add(350, self._show_tooltip)

    def _on_enter_with_shortcut(self, controller, x, y, widget):
        """Handle mouse entering a widget with a dynamic shortcut tooltip."""
        if self._is_cleaning_up:
            return

        if not self.is_enabled():
            return

        # If suppressed, ignore this enter event
        if self._suppressed:
            return

        if self.active_widget == widget:
            return

        # Update tooltip text with current shortcut before showing
        base_text = getattr(widget, "_custom_tooltip_base_text", "")
        action_name = getattr(widget, "_custom_tooltip_action", None)

        if base_text and action_name:
            shortcut = self._get_shortcut_label(action_name)
            if shortcut:
                widget._custom_tooltip_text = f"{base_text} ({shortcut})"
            else:
                widget._custom_tooltip_text = base_text

        self._clear_timer()
        self._hide_tooltip()

        self.active_widget = widget
        # Show tooltip after 350ms delay
        self.show_timer_id = GLib.timeout_add(350, self._show_tooltip)

    def _on_leave(self, controller):
        """Handle mouse leaving a widget with a tooltip."""
        if self._is_cleaning_up:
            return

        self._clear_timer()

        # Clear suppression when mouse leaves
        self._suppressed = False

        if self.active_widget:
            self._hide_tooltip(animate=True)
            self.active_widget = None

    def _show_tooltip(self) -> bool:
        """Show the tooltip popover for the active widget."""
        if self._is_cleaning_up:
            return GLib.SOURCE_REMOVE

        # On X11 with native tooltips, popover is None - nothing to do
        if self.popover is None:
            self.show_timer_id = None
            return GLib.SOURCE_REMOVE

        # Don't show if suppressed
        if self._suppressed:
            self.show_timer_id = None
            return GLib.SOURCE_REMOVE

        if not self.active_widget:
            return GLib.SOURCE_REMOVE

        # Verify the widget is still valid before accessing it
        # This prevents segfaults on X11 when widget is destroyed
        # between the timer being set and this callback executing
        try:
            if not self.active_widget.get_realized():
                self.active_widget = None
                self.show_timer_id = None
                return GLib.SOURCE_REMOVE
            # Additional check: ensure widget has a valid root window
            if self.active_widget.get_root() is None:
                self.active_widget = None
                self.show_timer_id = None
                return GLib.SOURCE_REMOVE
        except Exception:
            # Widget may have been destroyed
            self.active_widget = None
            self.show_timer_id = None
            return GLib.SOURCE_REMOVE

        tooltip_text = getattr(self.active_widget, "_custom_tooltip_text", None)
        if not tooltip_text:
            return GLib.SOURCE_REMOVE

        # Configure and show the popover
        self.label.set_text(tooltip_text)

        # Ensure popover is unparented before setting new parent
        if self.popover.get_parent():
            try:
                self.popover.unparent()
            except Exception:
                pass

        try:
            self.popover.set_parent(self.active_widget)
            self.popover.popup()
        except Exception:
            # Popover operation failed - cleanup and return
            self.active_widget = None
            self.show_timer_id = None
            return GLib.SOURCE_REMOVE

        self.show_timer_id = None
        return GLib.SOURCE_REMOVE

    def _hide_tooltip(self, animate: bool = False):
        """
        Hide the tooltip popover.

        Args:
            animate: If True, wait for fade-out animation before cleanup.
        """
        if self._is_cleaning_up:
            return

        # On X11 with native tooltips, popover is None - nothing to do
        if self.popover is None:
            return

        # Safe check for popover visibility - wrap in try/except
        # to handle case where popover may have been destroyed
        try:
            is_visible = self.popover.is_visible()
        except Exception:
            is_visible = False

        if not is_visible:
            # Still ensure unparenting even if not visible
            try:
                if self.popover.get_parent():
                    self.popover.unparent()
            except Exception:
                pass
            return

        def do_cleanup():
            if self._is_cleaning_up:
                return GLib.SOURCE_REMOVE
            if self.popover is None:
                return GLib.SOURCE_REMOVE
            try:
                self.popover.popdown()
            except Exception:
                pass
            try:
                if self.popover.get_parent():
                    self.popover.unparent()
            except Exception:
                pass
            return GLib.SOURCE_REMOVE

        # Trigger fade-out animation by removing .visible class
        try:
            self.popover.remove_css_class("visible")
        except Exception:
            pass

        if animate:
            # Wait for animation to finish before cleaning up
            GLib.timeout_add(200, do_cleanup)
        else:
            do_cleanup()

    def hide(self):
        """
        Force hide any visible tooltip immediately.
        Also suppresses the tooltip from reappearing until mouse leaves.
        """
        self._clear_timer()
        self._suppressed = True

        # Only try to hide popover if it exists (not on X11)
        if self.popover is not None:
            self._hide_tooltip(animate=False)

        self.active_widget = None
