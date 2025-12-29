"""
SSH Error Banner - Non-blocking UI for SSH connection errors.

This widget provides an inline banner that appears within the terminal tab,
allowing users to handle connection errors without blocking the UI.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gdk, GObject
from typing import Optional, Callable, TYPE_CHECKING
from dataclasses import dataclass
from enum import Enum, auto

from zashterminal.utils.logger import get_logger
from zashterminal.utils.translation_utils import _

if TYPE_CHECKING:
    from zashterminal.sessions.models import SessionItem

logger = get_logger("zashterminal.ui.ssh_error_banner")

# Static CSS for default/fallback styling (Adwaita compatible)
# This ensures the banner always has proper styling even without custom theme
_DEFAULT_CSS = """
.ssh-error-banner-container {
    background-color: @window_bg_color;
}
.ssh-error-banner {
    background-color: @card_bg_color;
    border-radius: 6px;
    border: 1px solid alpha(@error_color, 0.4);
    padding: 6px 10px;
    margin: 2px 4px;
}
.ssh-error-banner .heading {
    font-weight: 600;
    color: @error_color;
}
.ssh-error-banner .warning-icon {
    color: @warning_color;
}
.options-panel {
    background-color: @card_bg_color;
    border-radius: 6px;
    border: 1px solid alpha(@borders, 0.5);
    padding: 6px 12px;
    margin: 0 4px 4px 4px;
}
"""

# Apply default CSS once when module loads
_default_css_provider = None

def _ensure_default_css():
    """Ensure default CSS is applied for fallback styling."""
    global _default_css_provider
    if _default_css_provider is None:
        _default_css_provider = Gtk.CssProvider()
        _default_css_provider.load_from_string(_DEFAULT_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            _default_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION - 1  # Lower priority than custom themes
        )


class BannerAction(Enum):
    """Actions that can be taken from the banner."""
    RETRY = auto()
    AUTO_RECONNECT = auto()
    CLOSE = auto()
    DISMISS = auto()
    EDIT_SESSION = auto()
    FIX_HOST_KEY = auto()


@dataclass
class BannerConfig:
    """Configuration for auto-reconnect settings."""
    duration_mins: int = 5
    interval_secs: int = 10
    timeout_secs: int = 30


class SSHErrorBanner(Gtk.Box):
    """
    Non-blocking banner for SSH connection errors.
    
    This widget is designed to be embedded in a terminal pane header
    or as an overlay, providing quick actions without modal dialogs.
    
    Features:
    - Inline display within the terminal area
    - Quick action buttons (Retry, Auto-Reconnect, Close)
    - Expandable options panel for auto-reconnect configuration
    - Smooth animations for show/hide
    - Non-blocking - user can switch tabs freely
    """

    __gtype_name__ = "SSHErrorBanner"

    # Signals
    __gsignals__ = {
        "action-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        "dismissed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        session_name: str,
        error_message: str = "",
        session: Optional["SessionItem"] = None,
        terminal_id: Optional[int] = None,
        is_auth_error: bool = False,
        is_host_key_error: bool = False,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Ensure default CSS is applied for fallback styling
        _ensure_default_css()

        self.logger = get_logger("zashterminal.ui.ssh_error_banner")

        self._session_name = session_name
        self._error_message = error_message
        self._session = session
        self._terminal_id = terminal_id
        self._config = BannerConfig()
        self._expanded = False
        self._on_action_callback: Optional[Callable] = None
        self._is_auth_error = is_auth_error
        self._is_host_key_error = is_host_key_error

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Build the banner UI."""
        # Main banner container - HORIZONTAL layout (compact)
        self._main_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            hexpand=True,
        )
        self._main_box.add_css_class("ssh-error-banner")
        self._main_box.set_margin_start(4)
        self._main_box.set_margin_end(4)
        self._main_box.set_margin_top(2)
        self._main_box.set_margin_bottom(2)

        # Warning icon
        warning_icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        warning_icon.add_css_class("warning-icon")
        warning_icon.set_valign(Gtk.Align.CENTER)
        self._main_box.append(warning_icon)

        # Message area - vertical box with title and detail
        message_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        message_box.set_hexpand(True)
        message_box.set_valign(Gtk.Align.CENTER)

        # Title - different for auth errors
        if self._is_auth_error:
            title_label = Gtk.Label(label=_("Authentication Failed"))
        else:
            title_label = Gtk.Label(label=_("Connection Failed"))
        title_label.add_css_class("heading")
        title_label.set_halign(Gtk.Align.START)
        message_box.append(title_label)

        # Session name and error - use Gtk.Inscription for proper wrapping
        if self._error_message:
            detail_text = f"{self._session_name}: {self._error_message}"
        else:
            detail_text = self._session_name

        # Gtk.Inscription is designed for text that needs to wrap in complex layouts
        detail_inscription = Gtk.Inscription()
        detail_inscription.set_text(detail_text)
        detail_inscription.set_nat_chars(15)  # Compact natural width
        detail_inscription.set_min_chars(5)   # Very small minimum for narrow screens
        detail_inscription.set_min_lines(1)
        detail_inscription.set_nat_lines(4)   # Allow up to 4 lines when narrow
        detail_inscription.set_xalign(0)
        detail_inscription.set_hexpand(True)
        detail_inscription.add_css_class("dim-label")
        message_box.append(detail_inscription)

        self._main_box.append(message_box)

        # Action buttons - on the right side of the banner
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_valign(Gtk.Align.CENTER)

        if self._is_auth_error:
            # For authentication errors, show Edit Session button
            edit_btn = Gtk.Button(label=_("Edit Session"))
            edit_btn.add_css_class("suggested-action")
            edit_btn.add_css_class("compact-button")
            edit_btn.connect("clicked", self._on_edit_session_clicked)
            edit_btn.set_tooltip_text(_("Edit session credentials"))
            button_box.append(edit_btn)
        elif self._is_host_key_error:
            # For host key errors, show Fix Host Key and Close buttons
            fix_btn = Gtk.Button(label=_("Fix Host Key"))
            fix_btn.add_css_class("suggested-action")
            fix_btn.add_css_class("compact-button")
            fix_btn.connect("clicked", self._on_fix_host_key_clicked)
            fix_btn.set_tooltip_text(_("Remove old host key from known_hosts"))
            button_box.append(fix_btn)
        else:
            # For network errors, show Retry and Auto Reconnect buttons
            retry_btn = Gtk.Button(label=_("Retry"))
            retry_btn.add_css_class("suggested-action")
            retry_btn.add_css_class("compact-button")
            retry_btn.connect("clicked", self._on_retry_clicked)
            retry_btn.set_tooltip_text(_("Retry connection with extended timeout"))
            button_box.append(retry_btn)

            # Auto-Reconnect button with dropdown
            auto_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

            auto_btn = Gtk.Button(label=_("Auto Reconnect"))
            auto_btn.add_css_class("compact-button")
            auto_btn.connect("clicked", self._on_auto_reconnect_clicked)
            auto_btn.set_tooltip_text(_("Start automatic reconnection attempts"))
            auto_btn_box.append(auto_btn)

            # Options dropdown toggle
            options_btn = Gtk.Button()
            options_btn.set_icon_name("pan-down-symbolic")
            options_btn.add_css_class("flat")
            options_btn.add_css_class("circular")
            options_btn.connect("clicked", self._toggle_options)
            options_btn.set_tooltip_text(_("Configure auto-reconnect options"))
            self._options_toggle_btn = options_btn
            auto_btn_box.append(options_btn)

            button_box.append(auto_btn_box)

        self._main_box.append(button_box)

        self.append(self._main_box)

        # Options panel (initially hidden) - only for network errors
        self._options_revealer = Gtk.Revealer()
        self._options_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._options_revealer.set_transition_duration(200)

        if not self._is_auth_error:
            self._create_options_panel()

        self.append(self._options_revealer)

    def _create_options_panel(self) -> None:
        """Create the expandable options panel for auto-reconnect configuration."""
        # Main container - vertical to allow wrapping of the flowbox
        options_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
        )
        options_container.add_css_class("options-panel")
        options_container.set_margin_start(8)
        options_container.set_margin_end(8)
        options_container.set_margin_bottom(6)

        # Use FlowBox for wrapping when space is limited
        options_flow = Gtk.FlowBox()
        options_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        options_flow.set_homogeneous(False)
        options_flow.set_min_children_per_line(1)
        options_flow.set_max_children_per_line(6)
        options_flow.set_row_spacing(6)
        options_flow.set_column_spacing(12)
        options_flow.set_valign(Gtk.Align.CENTER)

        # Info label
        info_label = Gtk.Label(label=_("Auto-reconnect settings:"))
        info_label.add_css_class("dim-label")
        options_flow.append(info_label)

        # Duration setting
        duration_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        duration_label = Gtk.Label(label=_("Duration"))
        duration_label.add_css_class("dim-label")
        duration_box.append(duration_label)

        self._duration_spin = Gtk.SpinButton.new_with_range(1, 60, 1)
        self._duration_spin.set_value(self._config.duration_mins)
        self._duration_spin.set_width_chars(2)
        self._duration_spin.connect("value-changed", self._on_duration_changed)
        duration_box.append(self._duration_spin)

        duration_unit = Gtk.Label(label=_("min"))
        duration_unit.add_css_class("dim-label")
        duration_box.append(duration_unit)

        options_flow.append(duration_box)

        # Interval setting
        interval_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        interval_label = Gtk.Label(label=_("Interval"))
        interval_label.add_css_class("dim-label")
        interval_box.append(interval_label)

        self._interval_spin = Gtk.SpinButton.new_with_range(5, 120, 5)
        self._interval_spin.set_value(self._config.interval_secs)
        self._interval_spin.set_width_chars(2)
        self._interval_spin.connect("value-changed", self._on_interval_changed)
        interval_box.append(self._interval_spin)

        interval_unit = Gtk.Label(label=_("sec"))
        interval_unit.add_css_class("dim-label")
        interval_box.append(interval_unit)

        options_flow.append(interval_box)

        # Timeout setting
        timeout_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        timeout_label = Gtk.Label(label=_("Timeout"))
        timeout_label.add_css_class("dim-label")
        timeout_box.append(timeout_label)

        self._timeout_spin = Gtk.SpinButton.new_with_range(10, 300, 10)
        self._timeout_spin.set_value(self._config.timeout_secs)
        self._timeout_spin.set_width_chars(2)
        self._timeout_spin.connect("value-changed", self._on_timeout_changed)
        timeout_box.append(self._timeout_spin)

        timeout_unit = Gtk.Label(label=_("sec"))
        timeout_unit.add_css_class("dim-label")
        timeout_box.append(timeout_unit)

        options_flow.append(timeout_box)

        # Quick start button
        start_btn = Gtk.Button(label=_("Start"))
        start_btn.add_css_class("suggested-action")
        start_btn.add_css_class("compact-button")
        start_btn.connect("clicked", self._on_auto_reconnect_with_config)
        start_btn.set_tooltip_text(_("Start auto-reconnect with these settings"))
        options_flow.append(start_btn)

        options_container.append(options_flow)
        self._options_revealer.set_child(options_container)

    def _toggle_options(self, _button: Gtk.Button) -> None:
        """Toggle the options panel visibility."""
        self._expanded = not self._expanded
        self._options_revealer.set_reveal_child(self._expanded)

        # Update icon direction
        icon_name = "pan-up-symbolic" if self._expanded else "pan-down-symbolic"
        self._options_toggle_btn.set_icon_name(icon_name)

    def _on_retry_clicked(self, _button: Gtk.Button) -> None:
        """Handle retry button click."""
        self.emit("action-requested", "retry", {
            "session": self._session,
            "terminal_id": self._terminal_id,
            "timeout": self._config.timeout_secs,
        })
        if self._on_action_callback:
            self._on_action_callback(BannerAction.RETRY, self._terminal_id, {
                "timeout": self._config.timeout_secs,
            })

    def _on_auto_reconnect_clicked(self, _button: Gtk.Button) -> None:
        """Handle auto-reconnect button click with default config."""
        self._do_auto_reconnect()

    def _on_auto_reconnect_with_config(self, _button: Gtk.Button) -> None:
        """Handle auto-reconnect with custom config from options panel."""
        self._do_auto_reconnect()
        # Collapse options panel
        self._expanded = False
        self._options_revealer.set_reveal_child(False)
        self._options_toggle_btn.set_icon_name("pan-down-symbolic")

    def _do_auto_reconnect(self) -> None:
        """Execute auto-reconnect action."""
        self.emit("action-requested", "auto_reconnect", {
            "session": self._session,
            "terminal_id": self._terminal_id,
            "duration_mins": self._config.duration_mins,
            "interval_secs": self._config.interval_secs,
            "timeout_secs": self._config.timeout_secs,
        })
        if self._on_action_callback:
            self._on_action_callback(BannerAction.AUTO_RECONNECT, self._terminal_id, {
                "duration_mins": self._config.duration_mins,
                "interval_secs": self._config.interval_secs,
                "timeout_secs": self._config.timeout_secs,
            })

    def _on_close_clicked(self, _button: Gtk.Button) -> None:
        """Handle close button click."""
        self.emit("action-requested", "close", {
            "session": self._session,
            "terminal_id": self._terminal_id,
        })
        if self._on_action_callback:
            self._on_action_callback(BannerAction.CLOSE, self._terminal_id, {})

    def _on_edit_session_clicked(self, _button: Gtk.Button) -> None:
        """Handle edit session button click for authentication errors."""
        self.emit("action-requested", "edit_session", {
            "session": self._session,
            "terminal_id": self._terminal_id,
        })
        if self._on_action_callback:
            self._on_action_callback(BannerAction.EDIT_SESSION, self._terminal_id, {})

    def _on_fix_host_key_clicked(self, _button: Gtk.Button) -> None:
        """Handle fix host key button click for host key verification errors."""
        self.emit("action-requested", "fix_host_key", {
            "session": self._session,
            "terminal_id": self._terminal_id,
        })
        if self._on_action_callback:
            self._on_action_callback(BannerAction.FIX_HOST_KEY, self._terminal_id, {})

    def _on_duration_changed(self, spin: Gtk.SpinButton) -> None:
        """Update duration config."""
        self._config.duration_mins = int(spin.get_value())

    def _on_interval_changed(self, spin: Gtk.SpinButton) -> None:
        """Update interval config."""
        self._config.interval_secs = int(spin.get_value())

    def _on_timeout_changed(self, spin: Gtk.SpinButton) -> None:
        """Update timeout config."""
        self._config.timeout_secs = int(spin.get_value())

    def set_action_callback(self, callback: Callable) -> None:
        """Set callback for banner actions."""
        self._on_action_callback = callback

    def get_terminal_id(self) -> Optional[int]:
        """Get the terminal ID associated with this banner."""
        return self._terminal_id

    def get_session(self) -> Optional["SessionItem"]:
        """Get the session associated with this banner."""
        return self._session

    def update_error_message(self, message: str) -> None:
        """Update the error message displayed."""
        self._error_message = message
        # Would need to rebuild the message label - for now just log
        self.logger.debug(f"Error message updated: {message}")


class SSHErrorBannerManager:
    """
    Manages SSHErrorBanner instances across multiple terminals.
    
    This manager keeps track of banners and provides a centralized way
    to create, show, hide, and remove them.
    """

    _instance: Optional["SSHErrorBannerManager"] = None

    @classmethod
    def get_instance(cls) -> "SSHErrorBannerManager":
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.logger = get_logger("zashterminal.ui.ssh_error_banner_manager")
        self._banners: dict[int, SSHErrorBanner] = {}  # terminal_id -> banner
        self._on_action_callback: Optional[Callable] = None

    def set_action_callback(self, callback: Callable) -> None:
        """
        Set global callback for all banner actions.
        
        Callback signature: callback(action: BannerAction, terminal_id: int, config: dict)
        """
        self._on_action_callback = callback

    def create_banner(
        self,
        session_name: str,
        error_message: str,
        session: Optional["SessionItem"],
        terminal_id: int,
        is_auth_error: bool = False,
    ) -> SSHErrorBanner:
        """Create a new banner for a failed connection."""
        # Remove existing banner for this terminal if any
        if terminal_id in self._banners:
            self.remove_banner(terminal_id)

        banner = SSHErrorBanner(
            session_name=session_name,
            error_message=error_message,
            session=session,
            terminal_id=terminal_id,
            is_auth_error=is_auth_error,
        )

        if self._on_action_callback:
            banner.set_action_callback(self._on_action_callback)

        self._banners[terminal_id] = banner

        self.logger.info(f"Created banner for terminal {terminal_id}: {session_name}")

        return banner

    def get_banner(self, terminal_id: int) -> Optional[SSHErrorBanner]:
        """Get banner for a specific terminal."""
        return self._banners.get(terminal_id)

    def remove_banner(self, terminal_id: int) -> bool:
        """Remove and destroy a banner."""
        if terminal_id in self._banners:
            banner = self._banners.pop(terminal_id)
            parent = banner.get_parent()
            if parent:
                parent.remove(banner)
            self.logger.info(f"Removed banner for terminal {terminal_id}")
            return True
        return False

    def has_banner(self, terminal_id: int) -> bool:
        """Check if a banner exists for a terminal."""
        return terminal_id in self._banners

    def get_all_banners(self) -> list[SSHErrorBanner]:
        """Get all active banners."""
        return list(self._banners.values())

    def get_banner_count(self) -> int:
        """Get count of active banners."""
        return len(self._banners)

    def clear_all(self) -> None:
        """Remove all banners."""
        for terminal_id in list(self._banners.keys()):
            self.remove_banner(terminal_id)


def get_ssh_error_banner_manager() -> SSHErrorBannerManager:
    """Get the global SSHErrorBannerManager instance."""
    return SSHErrorBannerManager.get_instance()
