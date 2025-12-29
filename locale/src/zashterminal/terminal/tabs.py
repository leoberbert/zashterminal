# zashterminal/terminal/tabs.py

import re
import threading
import weakref
from typing import TYPE_CHECKING, Callable, List, Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango, Vte

from ..helpers import create_themed_popover_menu
from ..sessions.models import SessionItem
from ..settings.manager import SettingsManager as SettingsManagerType
from ..utils.icons import icon_button, icon_image
from ..utils.logger import get_logger
from ..utils.translation_utils import _
from .manager import TerminalManager

if TYPE_CHECKING:
    from ..filemanager.manager import FileManager

# Pre-compiled pattern for parsing RGBA color strings
_RGBA_COLOR_PATTERN = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+),?.*\)")

# CSS for tab moving visual feedback is now loaded from:
# data/styles/components.css (loaded by window_ui.py at startup)
# Classes: .tab-moving, .tab-bar-move-mode, .tab-drop-target, .tab-drop-left, .tab-drop-right


def _create_terminal_pane(
    terminal: Vte.Terminal,
    title: str,
    on_close_callback: Callable[[Vte.Terminal], None],
    on_move_to_tab_callback: Callable[[Vte.Terminal], None],
    settings_manager: SettingsManagerType,
) -> Adw.ToolbarView:
    """
    Creates a terminal pane using Adw.ToolbarView with a custom header to avoid GTK baseline warnings.
    """
    toolbar_view = Adw.ToolbarView()
    toolbar_view.add_css_class("terminal-pane")

    # Create custom header bar using basic GTK widgets to avoid Adw.HeaderBar baseline issues
    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    header_box.add_css_class("header-bar")
    header_box.set_hexpand(True)
    header_box.set_valign(Gtk.Align.START)

    # MODIFIED: Apply headerbar transparency settings on creation
    settings_manager.apply_headerbar_transparency(header_box)

    # Title label
    title_label = Gtk.Label(label=title, ellipsize=Pango.EllipsizeMode.END, xalign=0.0)
    title_label.set_hexpand(True)
    title_label.set_halign(Gtk.Align.START)
    header_box.append(title_label)

    # Action buttons (using bundled icons)
    move_to_tab_button = icon_button(
        "select-rectangular-symbolic", tooltip=_("Move to New Tab")
    )
    move_to_tab_button.add_css_class("flat")
    move_to_tab_button.connect("clicked", lambda _: on_move_to_tab_callback(terminal))

    close_button = icon_button("window-close-symbolic", tooltip=_("Close Pane"))
    close_button.add_css_class("flat")
    close_button.connect("clicked", lambda _: on_close_callback(terminal))

    header_box.append(move_to_tab_button)
    header_box.append(close_button)

    toolbar_view.add_top_bar(header_box)

    # Main content (the terminal)
    scrolled_window = Gtk.ScrolledWindow(child=terminal)
    scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scrolled_window.set_vexpand(True)
    scrolled_window.set_hexpand(True)
    toolbar_view.set_content(scrolled_window)

    # Attach important widgets for later access
    toolbar_view.terminal = terminal
    toolbar_view.title_label = title_label
    toolbar_view.move_button = move_to_tab_button
    toolbar_view.close_button = close_button
    # MODIFIED: Store a reference to the header box for live updates
    toolbar_view.header_box = header_box

    return toolbar_view


class TabManager:
    def __init__(
        self,
        terminal_manager: TerminalManager,
        on_quit_callback: Callable[[], None],
        on_detach_tab_callback: Callable[[Adw.ViewStackPage], None],
        scrolled_tab_bar: Gtk.ScrolledWindow,
        on_tab_count_changed: Callable[[], None] = None,
    ):
        """
        Initializes the TabManager.

        Args:
            terminal_manager: The central manager for terminal instances.
            on_quit_callback: A function to call when the last tab closes.
            on_detach_tab_callback: A function to call to detach a tab into a new window.
            scrolled_tab_bar: The ScrolledWindow containing the tab bar.
            on_tab_count_changed: A function to call when the number of tabs changes.
        """
        self.logger = get_logger("zashterminal.tabs.manager")
        self.terminal_manager = terminal_manager
        self.on_quit_application = on_quit_callback
        self.on_detach_tab_requested = on_detach_tab_callback
        self.scrolled_tab_bar = scrolled_tab_bar
        self.on_tab_count_changed = on_tab_count_changed

        self.view_stack = Adw.ViewStack()
        self.tab_bar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._update_tab_alignment()
        self.tabs: List[Gtk.Box] = []
        self.pages: weakref.WeakKeyDictionary[Gtk.Box, Adw.ViewStackPage] = (
            weakref.WeakKeyDictionary()
        )
        self.file_managers: weakref.WeakKeyDictionary[
            Adw.ViewStackPage, "FileManager"
        ] = weakref.WeakKeyDictionary()
        self.active_tab: Optional[Gtk.Box] = None
        self._tab_being_moved: Optional[Gtk.Box] = None  # Track tab in move mode
        self._drop_target_tab: Optional[Gtk.Box] = None  # Tab under cursor during move
        self._drop_side: str = (
            "left"  # "left" or "right" - which side of target to drop
        )

        # Set up tab bar for receiving move drop events
        self._setup_tab_bar_move_handlers()

        self._creation_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_focused_terminal = None

        self.terminal_manager.set_terminal_exit_handler(
            self._on_terminal_process_exited
        )
        # MODIFIED: Listen for settings changes to update pane headers live
        self.terminal_manager.settings_manager.add_change_listener(
            self._on_setting_changed
        )
        self.logger.info("Tab manager initialized with custom tab bar")

    def _on_setting_changed(self, key: str, old_value, new_value):
        """Callback for settings changes to update UI elements live."""
        if key == "headerbar_transparency" or key == "gtk_theme":
            self._update_all_pane_headers_transparency()

    def _update_all_pane_headers_transparency(self):
        """Iterates through all panes in all tabs and reapplies transparency."""
        for page in self.pages.values():
            panes = []
            self._find_panes_recursive(page.get_child(), panes)
            for pane in panes:
                if hasattr(pane, "header_box"):
                    self.terminal_manager.settings_manager.apply_headerbar_transparency(
                        pane.header_box
                    )

    def _find_panes_recursive(self, widget, panes_list: List[Adw.ToolbarView]):
        """Recursively find all Adw.ToolbarView panes within a container."""
        if isinstance(widget, Adw.ToolbarView) and hasattr(widget, "terminal"):
            panes_list.append(widget)
            return

        if isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self._find_panes_recursive(start_child, panes_list)
            if end_child := widget.get_end_child():
                self._find_panes_recursive(end_child, panes_list)
            return
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self._find_panes_recursive(child, panes_list)

    def _setup_tab_bar_move_handlers(self):
        """Set up event handlers on the tab bar for tab move operations."""
        # We only need to handle motion on individual tabs, which is done via
        # controllers added in _create_tab_widget. No tab bar level handlers needed
        # since we handle everything at the tab level.

    def _update_move_highlight(self, target_tab: Gtk.Box, side: str):
        """Update the visual highlight for the drop target."""
        self._clear_tab_drop_highlights()
        if target_tab and side:
            self._drop_target_tab = target_tab
            self._drop_side = side
            if side == "left":
                target_tab.add_css_class("tab-drop-left")
            else:
                target_tab.add_css_class("tab-drop-right")

    def _clear_tab_drop_highlights(self):
        """Remove drop target highlights from all tabs."""
        self._drop_target_tab = None
        for tab in self.tabs:
            tab.remove_css_class("tab-drop-target")
            tab.remove_css_class("tab-drop-left")
            tab.remove_css_class("tab-drop-right")

    def _perform_tab_move(self):
        """Perform the actual tab move based on current drop target and side."""
        if not self._tab_being_moved or not self._drop_target_tab:
            return

        moving_tab = self._tab_being_moved
        target_tab = self._drop_target_tab
        side = self._drop_side

        if moving_tab == target_tab:
            return

        # Get current indices
        moving_idx = self.tabs.index(moving_tab)
        target_idx = self.tabs.index(target_tab)

        # Calculate final position
        if side == "left":
            # Insert before target
            new_idx = target_idx
        else:
            # Insert after target
            new_idx = target_idx + 1

        # Adjust if moving from before the target
        if moving_idx < new_idx:
            new_idx -= 1

        # Only move if position actually changes
        if moving_idx == new_idx:
            return

        # Remove from old position
        self.tabs.remove(moving_tab)

        # Insert at new position
        self.tabs.insert(new_idx, moving_tab)

        # Rebuild visual order
        self._rebuild_tab_bar_order()

        self.logger.info(
            f"Tab '{moving_tab.label_widget.get_text()}' moved from {moving_idx} to {new_idx}"
        )

    def cancel_tab_move_if_active(self) -> bool:
        """Cancel the tab move operation if one is active. Returns True if cancelled."""
        if self._tab_being_moved is not None:
            self._cancel_tab_move()
            return True
        return False

    def _update_tab_alignment(self):
        """Updates the tab bar alignment based on the current setting."""
        alignment = self.terminal_manager.settings_manager.get(
            "tab_alignment", "center"
        )
        if alignment == "left":
            self.tab_bar_box.set_halign(Gtk.Align.START)
        else:  # center or any other value defaults to center
            self.tab_bar_box.set_halign(Gtk.Align.CENTER)

    def get_view_stack(self) -> Adw.ViewStack:
        return self.view_stack

    def get_tab_bar(self) -> Gtk.Box:
        return self.tab_bar_box

    def copy_from_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.copy_selection(terminal)
            return True
        return False

    def paste_to_current_terminal(self) -> bool:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.paste_clipboard(terminal)
            return True
        return False

    def select_all_in_current_terminal(self) -> None:
        if terminal := self.get_selected_terminal():
            self.terminal_manager.select_all(terminal)

    def clear_current_terminal(self) -> bool:
        """Reset the active terminal, clearing both screen and scrollback."""
        if terminal := self.get_selected_terminal():
            self.terminal_manager.clear_terminal(terminal)
            return True
        return False

    def create_initial_tab_if_empty(
        self,
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> None:
        if self.get_tab_count() == 0:
            self.create_local_tab(
                working_directory=working_directory,
                execute_command=execute_command,
                close_after_execute=close_after_execute,
            )

    def create_local_tab(
        self,
        session: Optional[SessionItem] = None,
        title: str = "Local",
        working_directory: Optional[str] = None,
        execute_command: Optional[str] = None,
        close_after_execute: bool = False,
    ) -> Optional[Vte.Terminal]:
        if session is None:
            session = SessionItem(name=title, session_type="local")

        # Use session's local_working_directory if not overridden
        effective_working_dir = working_directory
        if effective_working_dir is None and hasattr(
            session, "local_working_directory"
        ):
            effective_working_dir = session.local_working_directory or None

        # Use session's local_startup_command if not overridden
        effective_command = execute_command
        if effective_command is None and hasattr(session, "local_startup_command"):
            effective_command = session.local_startup_command or None

        terminal = self.terminal_manager.create_local_terminal(
            session=session,
            title=session.name,
            working_directory=effective_working_dir,
            execute_command=effective_command,
            close_after_execute=close_after_execute,
        )
        if terminal:
            self._create_tab_for_terminal(terminal, session)
        return terminal

    def create_ssh_tab(
        self, session: SessionItem, initial_command: Optional[str] = None
    ) -> Optional[Vte.Terminal]:
        terminal = self.terminal_manager.create_ssh_terminal(
            session, initial_command=initial_command
        )
        if terminal:
            self._create_tab_for_terminal(terminal, session)
        return terminal

    def create_sftp_tab(self, session: SessionItem) -> Optional[Vte.Terminal]:
        """Creates a new tab with an SFTP terminal for the specified session."""
        terminal = self.terminal_manager.create_sftp_terminal(session)
        if terminal:
            sftp_session = SessionItem.from_dict(session.to_dict())
            sftp_session.name = self._generate_unique_sftp_name(session.name)
            self._create_tab_for_terminal(terminal, sftp_session)
        return terminal

    def _generate_unique_sftp_name(self, base_session_name: str) -> str:
        base_title = f"SFTP-{base_session_name}"
        existing_titles = []
        for tab in self.tabs:
            session_item = getattr(tab, "session_item", None)
            if isinstance(session_item, SessionItem) and session_item.name.startswith(
                base_title
            ):
                existing_titles.append(session_item.name)

        if base_title not in existing_titles:
            return base_title

        suffix = 1
        while True:
            candidate = f"{base_title}({suffix})"
            if candidate not in existing_titles:
                return candidate
            suffix += 1

    def _scroll_to_widget(self, widget: Gtk.Widget) -> bool:
        """Scrolls the tab bar to make the given widget visible."""
        hadjustment = self.scrolled_tab_bar.get_hadjustment()
        if not hadjustment:
            return False

        coords = widget.translate_coordinates(self.scrolled_tab_bar, 0, 0)
        if coords is None:
            return False

        widget_x, _ = coords
        widget_width = widget.get_width()
        viewport_width = self.scrolled_tab_bar.get_width()

        current_scroll_value = hadjustment.get_value()

        if widget_x < 0:
            hadjustment.set_value(current_scroll_value + widget_x)
        elif widget_x + widget_width > viewport_width:
            hadjustment.set_value(
                current_scroll_value + (widget_x + widget_width - viewport_width)
            )

        return False

    def _on_terminal_scroll(self, controller, dx, dy):
        """Handles terminal scroll events to apply custom sensitivity."""
        try:
            terminal = controller.get_widget()
            scrolled_window = terminal.get_parent()

            if not isinstance(scrolled_window, Gtk.ScrolledWindow):
                return Gdk.EVENT_PROPAGATE

            vadjustment = scrolled_window.get_vadjustment()
            if not vadjustment:
                return Gdk.EVENT_PROPAGATE

            event = controller.get_current_event()
            device = event.get_device() if event else None
            source = device.get_source() if device else Gdk.InputSource.MOUSE

            if source == Gdk.InputSource.TOUCHPAD:
                sensitivity_percent = self.terminal_manager.settings_manager.get(
                    "touchpad_scroll_sensitivity", 30.0
                )
                sensitivity_factor = sensitivity_percent / 50.0
            else:
                sensitivity_percent = self.terminal_manager.settings_manager.get(
                    "mouse_scroll_sensitivity", 30.0
                )
                sensitivity_factor = sensitivity_percent / 10.0

            step = vadjustment.get_step_increment()
            scroll_amount = dy * step * sensitivity_factor

            new_value = vadjustment.get_value() + scroll_amount
            vadjustment.set_value(new_value)

            return Gdk.EVENT_STOP
        except Exception as e:
            self.logger.warning(f"Error handling custom scroll: {e}")

        return Gdk.EVENT_PROPAGATE

    def _on_terminal_contents_changed(self, terminal: Vte.Terminal):
        """Handles smart scrolling on new terminal output."""
        if not self.terminal_manager.settings_manager.get("scroll_on_output", True):
            return

        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            return

        adjustment = scrolled_window.get_vadjustment()
        if not adjustment:
            return

        # Check if we are scrolled to the bottom (with a small tolerance of 1.0)
        is_at_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 1.0
        )

        if is_at_bottom:
            # Defer scrolling to the end to the idle loop. This ensures that the
            # adjustment's 'upper' value is updated before we try to scroll.
            def scroll_to_end():
                adjustment.set_value(
                    adjustment.get_upper() - adjustment.get_page_size()
                )
                return GLib.SOURCE_REMOVE

            GLib.idle_add(scroll_to_end)

    def _create_tab_for_terminal(
        self, terminal: Vte.Terminal, session: SessionItem
    ) -> None:
        scroll_controller = Gtk.EventControllerScroll()
        scroll_controller.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_controller.connect("scroll", self._on_terminal_scroll)
        terminal.add_controller(scroll_controller)

        terminal.connect("contents-changed", self._on_terminal_contents_changed)

        scrolled_window = Gtk.ScrolledWindow(child=terminal)
        scrolled_window.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("enter", self._on_pane_focus_in, terminal)
        terminal.add_controller(focus_controller)

        # Connect to the 'realize' signal to grab focus when the widget is ready.
        # This is a one-shot connection; it disconnects itself after running.
        handler_id_ref = [None]

        def on_terminal_realize_once(widget, *args):
            widget.grab_focus()
            if handler_id_ref[0] and widget.handler_is_connected(handler_id_ref[0]):
                widget.disconnect(handler_id_ref[0])

        handler_id = terminal.connect_after("realize", on_terminal_realize_once)
        handler_id_ref[0] = handler_id

        terminal_area = Adw.Bin()
        terminal_area.set_child(scrolled_window)

        content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        content_paned.add_css_class("terminal-content-paned")
        content_paned.set_start_child(terminal_area)
        content_paned.set_resize_start_child(True)
        content_paned.set_shrink_start_child(False)
        content_paned.set_end_child(None)
        content_paned.set_resize_end_child(False)
        content_paned.set_shrink_end_child(True)

        page_name = f"page_{terminal.terminal_id}"
        page = self.view_stack.add_titled(content_paned, page_name, session.name)
        page.content_paned = content_paned
        terminal.zashterminal_parent_page = page

        tab_widget = self._create_tab_widget(page, session)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        self.set_active_tab(tab_widget)
        self.update_all_tab_titles()

        if self.on_tab_count_changed:
            self.on_tab_count_changed()

        GLib.idle_add(self._scroll_to_widget, tab_widget)

    def _get_contrasting_text_color(self, bg_color_str: str) -> str:
        """Calculates whether black or white text is more readable on a given background color."""
        if not bg_color_str:
            return "#000000"  # Default to black

        try:
            match = _RGBA_COLOR_PATTERN.match(bg_color_str)
            if not match:
                return "#000000"

            r, g, b = [int(c) / 255.0 for c in match.groups()]

            # WCAG luminance formula
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b

            return "#000000" if luminance > 0.5 else "#FFFFFF"
        except Exception as e:
            self.logger.warning(f"Could not parse color '{bg_color_str}': {e}")
            return "#000000"

    def _apply_tab_color(self, widget: Gtk.Widget, color_string: Optional[str]):
        style_context = widget.get_style_context()
        if hasattr(widget, "_color_provider"):
            style_context.remove_provider(widget._color_provider)
            del widget._color_provider

        if color_string:
            provider = Gtk.CssProvider()

            # Apply color only to the top part of the tab (top border)
            css = f"""
                .custom-tab-button {{
                    border: 1px solid {color_string};
                }}
            """
            provider.load_from_data(css.encode("utf-8"))
            style_context.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            widget._color_provider = provider

    def _create_tab_widget(
        self, page: Adw.ViewStackPage, session: SessionItem
    ) -> Gtk.Box:
        tab_widget = Gtk.Box(spacing=6)
        tab_widget.add_css_class("custom-tab-button")
        tab_widget.add_css_class("raised")

        icon_name = None
        if session.name.startswith("SFTP-"):
            icon_name = "folder-remote-symbolic"
        elif session.is_ssh():
            icon_name = "network-server-symbolic"

        if icon_name:
            icon = icon_image(icon_name)
            tab_widget.append(icon)

        label = Gtk.Label(
            label=session.name, ellipsize=Pango.EllipsizeMode.START, xalign=1.0
        )
        label.set_width_chars(8)
        tab_widget.append(label)

        close_button = icon_button(
            "window-close-symbolic", css_classes=["circular", "flat"]
        )
        tab_widget.append(close_button)

        left_click = Gtk.GestureClick.new()
        left_click.connect("pressed", self._on_tab_clicked, tab_widget)
        tab_widget.add_controller(left_click)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_tab_right_click, tab_widget)
        tab_widget.add_controller(right_click)

        # Motion controller for hover highlighting during tab move
        motion_controller = Gtk.EventControllerMotion()
        motion_controller.connect("motion", self._on_tab_motion, tab_widget)
        motion_controller.connect("leave", self._on_tab_leave, tab_widget)
        tab_widget.add_controller(motion_controller)

        close_button.connect("clicked", self._on_tab_close_button_clicked, tab_widget)

        tab_widget.label_widget = label
        tab_widget.close_button = close_button  # Store direct reference
        tab_widget._base_title = session.name
        tab_widget._is_local = session.is_local()
        tab_widget.session_item = session

        self._apply_tab_color(tab_widget, session.tab_color)

        return tab_widget

    def _on_tab_motion(self, controller, x, y, tab_widget):
        """Handle mouse motion over a tab during move mode."""
        if self._tab_being_moved is None or tab_widget == self._tab_being_moved:
            return

        # Determine which half of the tab we're over
        tab_width = tab_widget.get_width()
        side = "left" if x < tab_width / 2 else "right"

        # Update highlight
        self._update_move_highlight(tab_widget, side)

    def _on_tab_leave(self, controller, tab_widget):
        """Handle mouse leaving a tab during move mode."""
        if self._tab_being_moved is None:
            return
        # Clear highlight when leaving a tab
        self._clear_tab_drop_highlights()

    def _on_tab_clicked(self, gesture, _n_press, x, _y, tab_widget):
        # If we're in move mode, handle the drop
        if self._tab_being_moved is not None:
            if self._tab_being_moved != tab_widget:
                # Determine which half of the tab was clicked
                tab_width = tab_widget.get_width()
                side = "left" if x < tab_width / 2 else "right"

                # Update the drop target and perform the move
                self._drop_target_tab = tab_widget
                self._drop_side = side
                self._perform_tab_move()
            self._cancel_tab_move()
            return
        self.set_active_tab(tab_widget)

    def _on_tab_right_click(self, _gesture, _n_press, x, y, tab_widget):
        # Cancel any ongoing move operation when right-clicking
        if self._tab_being_moved is not None:
            self._cancel_tab_move()

        menu = Gio.Menu()
        menu.append(_("Move Tab"), "win.move-tab")
        menu.append(_("Duplicate Tab"), "win.duplicate-tab")
        menu.append(_("Detach Tab"), "win.detach-tab")
        popover = create_themed_popover_menu(menu, tab_widget)

        page = self.pages.get(tab_widget)
        if page:
            action_group = Gio.SimpleActionGroup()

            move_action = Gio.SimpleAction.new("move-tab", None)
            move_action.connect(
                "activate",
                lambda _action, _param, tab=tab_widget: self._start_tab_move(tab),
            )
            action_group.add_action(move_action)

            duplicate_action = Gio.SimpleAction.new("duplicate-tab", None)
            duplicate_action.connect(
                "activate",
                lambda _action, _param, tab=tab_widget: self._duplicate_tab(tab),
            )
            action_group.add_action(duplicate_action)

            action = Gio.SimpleAction.new("detach-tab", None)

            action.connect(
                "activate", lambda a, _, pg=page: self._request_detach_tab(pg)
            )
            action_group.add_action(action)
            popover.insert_action_group("win", action_group)

        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        popover.set_pointing_to(rect)
        popover.popup()

    def _request_detach_tab(self, page: Adw.ViewStackPage):
        if self.on_detach_tab_requested:
            self.on_detach_tab_requested(page)

    def _start_tab_move(self, tab_widget: Gtk.Box) -> None:
        """Starts the tab move mode for the given tab widget."""
        if len(self.tabs) < 2:
            self.logger.debug("Cannot move tab: only one tab exists.")
            return

        self._tab_being_moved = tab_widget
        self._current_drop_index = -1
        tab_widget.add_css_class("tab-moving")
        self.tab_bar_box.add_css_class("tab-bar-move-mode")

        # Hide and disable all close buttons during move mode to prevent accidental closing
        for tab in self.tabs:
            close_btn = self._get_tab_close_button(tab)
            if close_btn:
                close_btn.set_visible(False)
                close_btn.set_sensitive(False)

        self.logger.info(f"Tab move started for: {tab_widget.label_widget.get_text()}")

    def _cancel_tab_move(self) -> None:
        """Cancels the current tab move operation."""
        if self._tab_being_moved is not None:
            self._tab_being_moved.remove_css_class("tab-moving")
            self.tab_bar_box.remove_css_class("tab-bar-move-mode")
            self._clear_tab_drop_highlights()

            # Restore all close buttons
            for tab in self.tabs:
                close_btn = self._get_tab_close_button(tab)
                if close_btn:
                    close_btn.set_visible(True)
                    close_btn.set_sensitive(True)

            self.logger.debug("Tab move cancelled.")
            self._tab_being_moved = None

    def _get_tab_close_button(self, tab_widget: Gtk.Box) -> Optional[Gtk.Button]:
        """Get the close button from a tab widget."""
        # Try direct reference first (for newly created tabs)
        if hasattr(tab_widget, "close_button") and tab_widget.close_button:
            return tab_widget.close_button
        # Fallback: iterate through all children to find the close button
        child = tab_widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                return child
            child = child.get_next_sibling()
        return None

    def _rebuild_tab_bar_order(self) -> None:
        """Rebuilds the tab bar widget order to match self.tabs list."""
        # Remove all tabs from the box
        for tab in self.tabs:
            self.tab_bar_box.remove(tab)

        # Re-add them in the correct order
        for tab in self.tabs:
            self.tab_bar_box.append(tab)

    def _duplicate_tab(self, tab_widget: Gtk.Box) -> None:
        """Creates a new tab duplicating the session represented by the given tab widget."""
        page = self.pages.get(tab_widget)
        if not page:
            return

        terminals = self.get_all_terminals_in_page(page)
        if not terminals:
            self.logger.warning("Cannot duplicate tab without terminals.")
            return

        primary_terminal = terminals[0]
        terminal_id = getattr(primary_terminal, "terminal_id", None)
        if not terminal_id:
            self.logger.warning("Primary terminal missing identifier; duplication aborted.")
            return

        terminal_info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        if not terminal_info:
            self.logger.warning("Terminal info unavailable; duplication aborted.")
            return

        session = getattr(tab_widget, "session_item", None)
        session_copy = (
            SessionItem.from_dict(session.to_dict())
            if isinstance(session, SessionItem)
            else None
        )

        term_type = terminal_info.get("type")

        try:
            if term_type == "local":
                working_directory = self._get_terminal_working_directory(primary_terminal)
                self.create_local_tab(
                    session=session_copy,
                    working_directory=working_directory,
                )
            elif term_type == "ssh":
                if session_copy:
                    self.create_ssh_tab(session_copy)
                else:
                    self.logger.warning("Cannot duplicate SSH tab without session data.")
            elif term_type == "sftp":
                if session_copy:
                    self.create_sftp_tab(session_copy)
                else:
                    self.logger.warning("Cannot duplicate SFTP tab without session data.")
            else:
                self.logger.warning(f"Unsupported terminal type for duplication: {term_type}")
        except Exception as exc:
            self.logger.error(
                f"Failed to duplicate tab '{tab_widget.label_widget.get_text()}': {exc}"
            )
            return

    def _get_terminal_working_directory(
        self, terminal: Vte.Terminal
    ) -> Optional[str]:
        """Returns the terminal's current working directory path, if available."""
        uri = terminal.get_current_directory_uri()
        if not uri:
            return None

        try:
            path, _ = GLib.filename_from_uri(uri)
            return path
        except (TypeError, ValueError) as error:
            self.logger.debug(f"Could not resolve working directory from '{uri}': {error}")
            return None

    def _is_widget_in_filemanager(self, widget: Gtk.Widget) -> bool:
        """Checks if a widget is a descendant of the FileManager's main widget."""
        if not widget or not self.active_tab:
            return False

        page = self.pages.get(self.active_tab)
        if not page:
            return False

        fm = self.file_managers.get(page)
        if not fm:
            return False

        fm_widget = fm.get_main_widget()
        current = widget
        while current:
            if current == fm_widget:
                return True
            current = current.get_parent()
        return False

    def set_active_tab(self, tab_to_activate: Gtk.Box):
        if self.active_tab == tab_to_activate:
            return

        if self.active_tab:
            main_window = self.terminal_manager.parent_window
            focus_widget = main_window.get_focus()
            if focus_widget and self._is_widget_in_filemanager(focus_widget):
                self.view_stack.grab_focus()

        if self.active_tab:
            self.active_tab.remove_css_class("active")

        self.active_tab = tab_to_activate
        self.active_tab.add_css_class("active")

        page = self.pages.get(self.active_tab)
        if page:
            self.view_stack.set_visible_child(page.get_child())

            terminal_to_focus = None
            # Check if the page has a remembered focused terminal
            if hasattr(page, "_last_focused_in_page") and page._last_focused_in_page:
                terminal_to_focus = (
                    page._last_focused_in_page()
                )  # This might be None if the ref is dead

            # If no valid remembered terminal, fall back to the first one
            if not terminal_to_focus:
                terminals_in_page = self.get_all_terminals_in_page(page)
                if terminals_in_page:
                    terminal_to_focus = terminals_in_page[0]

            # If we have a terminal, schedule the focus. The schedule function will check if it's realized.
            if terminal_to_focus:
                self._schedule_terminal_focus(terminal_to_focus)

    def toggle_file_manager_for_active_tab(self, is_active: bool):
        """Toggles the file manager's visibility for the currently active tab."""
        if not self.active_tab:
            if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                self.terminal_manager.parent_window.file_manager_button.set_active(
                    False
                )
            return

        page = self.pages.get(self.active_tab)
        if not page:
            if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                self.terminal_manager.parent_window.file_manager_button.set_active(
                    False
                )
            return

        if not hasattr(page, "content_paned"):
            self.logger.warning(
                "Attempted to toggle file manager on a page without a content_paned (likely a detached tab)."
            )
            if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                self.terminal_manager.parent_window.file_manager_button.set_active(
                    False
                )
            return

        paned = page.content_paned
        fm = self.file_managers.get(page)

        if is_active:
            active_terminal = self.get_selected_terminal()
            if not active_terminal:
                if hasattr(self.terminal_manager.parent_window, "file_manager_button"):
                    self.terminal_manager.parent_window.file_manager_button.set_active(
                        False
                    )

            if not fm:
                # Lazy import FileManager only when needed
                from ..filemanager.manager import FileManager
                fm = FileManager(
                    self.terminal_manager.parent_window,
                    self.terminal_manager,
                    self.terminal_manager.settings_manager,
                )
                fm.temp_files_changed_handler_id = fm.connect(
                    "temp-files-changed",
                    self.terminal_manager.parent_window._on_temp_files_changed,
                    page,
                )
                self.file_managers[page] = fm

            fm.rebind_terminal(active_terminal)
            paned.set_end_child(fm.get_main_widget())

            # Use paned's actual available height instead of window height
            # This correctly handles cases where AI panel reduces available space
            paned_allocation = paned.get_allocation()
            available_height = paned_allocation.height

            # If allocation is not available yet (widget not realized), fall back to window height
            if available_height <= 1:
                available_height = self.terminal_manager.parent_window.get_height()
                # Account for approximate headerbar/toolbar overhead
                available_height = max(400, available_height - 100)

            saved_fm_height = self.terminal_manager.settings_manager.get(
                "file_manager_height", 250
            )
            # Enforce minimum height constraint
            min_fm_height = 240
            min_terminal_height = 120  # Minimum space for terminal
            max_fm_height = max(min_fm_height, available_height - min_terminal_height)

            # Clamp file manager height to valid range
            saved_fm_height = max(min_fm_height, min(saved_fm_height, max_fm_height))

            self.logger.debug(
                f"File manager: available_height={available_height}, "
                f"saved_fm_height={saved_fm_height}, max_fm_height={max_fm_height}"
            )

            # Calculate target position from saved height
            target_pos = available_height - saved_fm_height

            # Use page-specific position if available and valid
            if hasattr(page, "_fm_paned_pos"):
                last_pos = page._fm_paned_pos
                # Validate that last_pos gives a reasonable file manager size
                last_fm_height = available_height - last_pos
                if min_fm_height <= last_fm_height <= max_fm_height:
                    target_pos = last_pos

            # Final validation: ensure position is sensible (not negative or too small for terminal)
            target_pos = max(
                min_terminal_height, min(target_pos, available_height - min_fm_height)
            )

            self.logger.debug(
                f"File manager: target_pos={target_pos}, "
                f"has_page_pos={hasattr(page, '_fm_paned_pos')}"
            )

            paned.set_position(target_pos)
            fm.set_visibility(True, source="filemanager")

            # Connect handler to save file manager height on resize (if not already connected)
            if not hasattr(paned, "_fm_position_handler_id"):
                paned._fm_position_handler_id = paned.connect(
                    "notify::position",
                    self._on_file_manager_paned_position_changed,
                    page,
                )

        elif fm:
            page._fm_paned_pos = paned.get_position()
            # Save file manager height to settings for new tabs/windows
            # Use paned's actual height for accurate calculation
            paned_allocation = paned.get_allocation()
            available_height = paned_allocation.height
            if available_height > 1:
                fm_height = available_height - paned.get_position()
            else:
                # Fallback to window height if paned not properly allocated
                window_height = self.terminal_manager.parent_window.get_height()
                fm_height = window_height - paned.get_position()

            # Enforce minimum height constraint
            min_fm_height = 240
            fm_height = max(min_fm_height, fm_height)
            self.logger.debug(
                f"File manager closing: available_height={available_height}, "
                f"paned_pos={paned.get_position()}, fm_height={fm_height}"
            )
            # Save immediately to ensure persistence across sessions
            self.terminal_manager.settings_manager.set(
                "file_manager_height", fm_height, save_immediately=True
            )
            fm.set_visibility(False, source="filemanager")
            paned.set_end_child(None)

    def _on_file_manager_paned_position_changed(self, paned, _param_spec, page):
        """Save file manager height when the pane is resized by the user."""
        # Only save if the file manager is actually visible
        fm = self.file_managers.get(page)
        if not fm or not fm.revealer.get_reveal_child():
            return

        # Use paned's actual height for accurate calculation
        paned_allocation = paned.get_allocation()
        available_height = paned_allocation.height
        if available_height <= 1:
            # Widget not properly allocated yet, skip this update
            return

        fm_height = available_height - paned.get_position()

        # Enforce minimum height constraint
        min_fm_height = 240
        fm_height = max(min_fm_height, fm_height)

        # Store in page for session consistency
        page._fm_paned_pos = paned.get_position()

        # Save to settings immediately so it persists across sessions
        self.terminal_manager.settings_manager.set(
            "file_manager_height", fm_height, save_immediately=True
        )

    def _on_tab_close_button_clicked(self, button: Gtk.Button, tab_widget: Gtk.Box):
        # If in move mode, ignore close button clicks entirely
        if self._tab_being_moved is not None:
            return

        self.logger.debug(
            f"Close button clicked for tab: {tab_widget.label_widget.get_text()}"
        )
        page = self.pages.get(tab_widget)
        if not page:
            return

        terminals_in_page = self.get_all_terminals_in_page(page)
        self.logger.info(
            f"Close request for tab '{page.get_title()}' with {len(terminals_in_page)} terminals."
        )

        # Track if any terminal has a truly active process that will emit child-exited
        # Auto-reconnecting terminals may have short-lived processes that we should not wait for
        should_wait_for_exit = False

        for terminal in terminals_in_page:
            terminal_id = getattr(terminal, "terminal_id", None)
            is_auto_reconnecting = self.terminal_manager.is_auto_reconnect_active(
                terminal
            )

            if terminal_id and not is_auto_reconnecting:
                info = self.terminal_manager.registry.get_terminal_info(terminal_id)
                pid = info.get("process_id") if info else None
                status = info.get("status") if info else None
                # Only wait if there's a stable running process (not auto-reconnecting)
                if pid and pid != -1 and status == "running":
                    should_wait_for_exit = True

            self.terminal_manager.remove_terminal(terminal, force_kill_group=True)

        # If no terminal has a stable active process, close the tab immediately
        # Auto-reconnecting terminals are handled by cancel_auto_reconnect
        if not should_wait_for_exit:
            self._close_tab_by_page(page)

    def _on_terminal_process_exited(
        self, terminal: Vte.Terminal, child_status: int, identifier
    ):
        with self._cleanup_lock:
            page = self.get_page_for_terminal(terminal)
            terminal_id = getattr(terminal, "terminal_id", "N/A")

            self.logger.info(f"[PROCESS_EXITED] Terminal {terminal_id} process exited")
            self.logger.info(
                f"[PROCESS_EXITED] Auto-reconnect active: {self.terminal_manager.is_auto_reconnect_active(terminal)}"
            )

            # IMPORTANT: If auto-reconnect is active, don't do any cleanup
            # The terminal should stay open for reconnection attempts
            if self.terminal_manager.is_auto_reconnect_active(terminal):
                self.logger.info(
                    f"[PROCESS_EXITED] Skipping cleanup for terminal {terminal_id} - auto-reconnect is active"
                )
                return

            pane_to_remove, parent_container = self._find_pane_and_parent(terminal)
            self.logger.info(
                f"[PROCESS_EXITED] Found pane: {pane_to_remove}, parent: {type(parent_container)}"
            )

            # MODIFIED: Only manipulate panes if the parent is a Gtk.Paned (i.e., it's a split)
            if isinstance(parent_container, Gtk.Paned):
                self.logger.info(
                    f"[PROCESS_EXITED] Removing pane from split for terminal {terminal_id}"
                )
                self._remove_pane_ui(pane_to_remove, parent_container)

            self.terminal_manager._cleanup_terminal(terminal, terminal_id)

            if not page:
                return

            active_terminals_in_page = self.get_all_active_terminals_in_page(page)

            if not active_terminals_in_page:
                self.logger.info(
                    f"Last active terminal in tab '{page.get_title()}' exited. Closing tab."
                )
                self._close_tab_by_page(page)

            if self.terminal_manager.registry.get_active_terminal_count() == 0:
                self.logger.info(
                    "Last active terminal in the application has exited. Requesting quit."
                )
                GLib.idle_add(self._quit_application)

    def _close_tab_by_page(self, page: Adw.ViewStackPage):
        tab_to_remove = None
        for tab in self.tabs:
            if self.pages.get(tab) == page:
                tab_to_remove = tab
                break

        if tab_to_remove:
            was_active = self.active_tab == tab_to_remove

            self.tab_bar_box.remove(tab_to_remove)
            self.tabs.remove(tab_to_remove)
            if tab_to_remove in self.pages:
                del self.pages[tab_to_remove]

            # Explicitly destroy the FileManager instance
            if page in self.file_managers:
                fm = self.file_managers.pop(page)
                # Detach the file manager widget from the paned before destroying
                if hasattr(page, "content_paned") and page.content_paned:
                    page.content_paned.set_end_child(None)
                fm.destroy()

            self.view_stack.remove(page.get_child())

            if was_active and self.tabs:
                self.set_active_tab(self.tabs[-1])
            elif not self.tabs:
                self.active_tab = None

        self.update_all_tab_titles()
        if self.on_tab_count_changed:
            self.on_tab_count_changed()

    def get_all_active_terminals_in_page(
        self, page: Adw.ViewStackPage
    ) -> List[Vte.Terminal]:
        active_terminals = []
        all_terminals_in_page = self.get_all_terminals_in_page(page)
        for term in all_terminals_in_page:
            term_id = getattr(term, "terminal_id", None)
            if term_id:
                # If auto-reconnect is active, consider the terminal as active
                # even if it's in a failed/exited state
                if self.terminal_manager.is_auto_reconnect_active(term):
                    active_terminals.append(term)
                    continue

                info = self.terminal_manager.registry.get_terminal_info(term_id)
                if info and info.get("status") not in ["exited", "spawn_failed"]:
                    active_terminals.append(term)
        return active_terminals

    def get_selected_terminal(self) -> Optional[Vte.Terminal]:
        if self._last_focused_terminal and (terminal := self._last_focused_terminal()):
            if terminal.get_realized():
                return terminal

        page_content = self.view_stack.get_visible_child()
        if not page_content:
            return None

        terminals = []
        self._find_terminals_recursive(page_content, terminals)
        return terminals[0] if terminals else None

    def get_all_terminals_in_page(self, page: Adw.ViewStackPage) -> List[Vte.Terminal]:
        terminals = []
        if root_widget := page.get_child():
            self._find_terminals_recursive(root_widget, terminals)
        return terminals

    def get_all_terminals_across_tabs(self) -> List[Vte.Terminal]:
        """Returns a list of all active Vte.Terminal widgets across all tabs."""
        all_terminals = []
        for page in self.pages.values():
            all_terminals.extend(self.get_all_terminals_in_page(page))
        return all_terminals

    def get_page_for_terminal(
        self, terminal: Vte.Terminal
    ) -> Optional[Adw.ViewStackPage]:
        return getattr(terminal, "zashterminal_parent_page", None)

    def update_titles_for_terminal(self, terminal, new_title: str, osc7_info):
        """Updates the tab title and the specific pane title for a terminal."""
        page = self.get_page_for_terminal(terminal)
        if not page:
            return

        # Update the main tab title
        self.set_tab_title(page, new_title)

        # Update the specific pane's title
        pane = self._find_pane_for_terminal(page, terminal)
        if pane and hasattr(pane, "title_label"):
            pane.title_label.set_label(new_title)

    def set_tab_title(self, page: Adw.ViewStackPage, new_title: str) -> None:
        if not (page and new_title):
            return

        tab_button = None
        for tab in self.tabs:
            if self.pages.get(tab) == page:
                tab_button = tab
                break

        if tab_button:
            base_title = tab_button._base_title

            if tab_button._is_local:
                display_title = new_title
            else:
                if new_title.startswith(base_title + ":"):
                    display_title = new_title
                else:
                    display_title = (
                        base_title
                        if new_title == base_title
                        else f"{base_title}: {new_title}"
                    )

            terminal_count = len(self.get_all_terminals_in_page(page))
            if terminal_count > 1:
                display_title = f"{display_title} ({terminal_count})"

            tab_button.label_widget.set_text(display_title)
            page.set_title(display_title)

            # NOVO: Forçar a atualização da UI da janela principal
            if hasattr(self.terminal_manager.parent_window, "_update_tab_layout"):
                self.terminal_manager.parent_window._update_tab_layout()

    def update_all_tab_titles(self) -> None:
        """Updates all tab titles based on the current state of the terminal."""
        for tab in self.tabs:
            page = self.pages.get(tab)
            if page:
                terminals = self.get_all_terminals_in_page(page)
                if terminals:
                    main_terminal = terminals[0]
                    uri = main_terminal.get_current_directory_uri()
                    if uri:
                        from urllib.parse import unquote, urlparse

                        path = unquote(urlparse(uri).path)
                        display_path = self.terminal_manager.osc7_tracker.parser._create_display_path(
                            path
                        )
                        self.set_tab_title(page, display_path)
                    else:
                        self.set_tab_title(page, tab._base_title)
                else:
                    self.set_tab_title(page, tab._base_title)

    def get_tab_count(self) -> int:
        return len(self.tabs)

    def _on_pane_focus_in(self, controller, terminal):
        self._last_focused_terminal = weakref.ref(terminal)
        page = self.get_page_for_terminal(terminal)
        if page:
            page._last_focused_in_page = weakref.ref(terminal)

    def _schedule_terminal_focus(self, terminal: Vte.Terminal) -> None:
        """Schedules a deferred focus call for the terminal, ensuring the UI is ready."""

        def focus_task():
            if (
                terminal
                and terminal.get_realized()
                and terminal.is_visible()
                and terminal.get_can_focus()
            ):
                terminal.grab_focus()
                self.logger.debug(
                    f"Focus set on terminal {getattr(terminal, 'terminal_id', 'N/A')}"
                )
            else:
                self.logger.warning(
                    f"Could not set focus on terminal {getattr(terminal, 'terminal_id', 'N/A')}: not ready or invalid."
                )
            return GLib.SOURCE_REMOVE

        GLib.idle_add(focus_task)

    def _find_pane_for_terminal(
        self, page: Adw.ViewStackPage, terminal_to_find: Vte.Terminal
    ) -> Optional[Adw.ToolbarView]:
        """Recursively finds the Adw.ToolbarView pane that contains a specific terminal."""

        def find_recursive(widget):
            if (
                isinstance(widget, Adw.ToolbarView)
                and getattr(widget, "terminal", None) == terminal_to_find
            ):
                return widget

            if isinstance(widget, Gtk.Paned):
                if start_child := widget.get_start_child():
                    if found := find_recursive(start_child):
                        return found
                if end_child := widget.get_end_child():
                    if found := find_recursive(end_child):
                        return found

            if hasattr(widget, "get_child") and (child := widget.get_child()):
                return find_recursive(child)

            return None

        return find_recursive(page.get_child())

    def show_error_banner_for_terminal(
        self,
        terminal: Vte.Terminal,
        session_name: str,
        error_message: str = "",
        session: Optional[SessionItem] = None,
        is_auth_error: bool = False,
        is_host_key_error: bool = False,
    ) -> bool:
        """
        Show a non-blocking error banner above the terminal.

        The banner is inserted between the terminal container and the scrolled window,
        allowing it to appear above the terminal without blocking the UI.

        Args:
            terminal: The terminal that failed.
            session_name: Name of the session.
            error_message: Error description.
            session: Session object for retry/reconnect.
            is_auth_error: Whether this is an authentication error.
            is_host_key_error: Whether this is a host key verification error.

        Returns:
            True if banner was shown, False otherwise.
        """
        from ..ui.widgets.ssh_error_banner import SSHErrorBanner, BannerAction

        page = self.get_page_for_terminal(terminal)
        if not page:
            self.logger.warning("Cannot show banner - terminal has no page")
            return False

        terminal_id = getattr(terminal, "terminal_id", None)

        # Check if we already have a valid banner
        existing_banner = getattr(terminal, "_error_banner", None)
        if existing_banner is not None:
            # Verify banner widget is still valid (not destroyed)
            try:
                if existing_banner.get_parent() is not None:
                    self.logger.debug(
                        f"Banner already exists and is valid for terminal {terminal_id}"
                    )
                    return True
                else:
                    # Banner widget was orphaned, clear references
                    self.logger.debug(
                        f"Banner was orphaned, creating new one for terminal {terminal_id}"
                    )
                    terminal._error_banner = None
                    terminal._banner_box = None
            except Exception:
                # Banner widget may have been destroyed
                terminal._error_banner = None
                terminal._banner_box = None

        # Find the scrolled window containing the terminal
        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            self.logger.warning(
                f"Cannot show banner - terminal parent is not ScrolledWindow: {type(scrolled_window)}"
            )
            return False

        # Find the container holding the scrolled window
        container = scrolled_window.get_parent()
        if not container:
            self.logger.warning("Cannot show banner - scrolled window has no parent")
            return False

        # Create new banner (colors are now handled by global theme system)
        banner = SSHErrorBanner(
            session_name=session_name,
            error_message=error_message,
            session=session,
            terminal_id=terminal_id,
            is_auth_error=is_auth_error,
            is_host_key_error=is_host_key_error,
        )

        # Set action callback
        def on_banner_action(action: BannerAction, tid: int, config: dict):
            self._handle_banner_action(action, terminal, session, tid, config)

        banner.set_action_callback(on_banner_action)

        # Connect dismissed signal
        banner.connect(
            "dismissed", lambda b: self.hide_error_banner_for_terminal(terminal)
        )

        # Create a vertical box to hold banner + scrolled window
        banner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        banner_box.set_vexpand(True)
        banner_box.set_hexpand(True)
        # Apply CSS class for solid background
        banner_box.add_css_class("ssh-error-banner-container")

        # Add banner at top
        banner_box.append(banner)

        # Remove scrolled window from current parent and add to banner_box
        if isinstance(container, Adw.Bin):
            container.set_child(None)
            banner_box.append(scrolled_window)
            container.set_child(banner_box)
        elif isinstance(container, Adw.ToolbarView):
            container.set_content(None)
            banner_box.append(scrolled_window)
            container.set_content(banner_box)
        elif isinstance(container, Gtk.Box):
            # Container is already a Gtk.Box - check if it's our banner_box
            existing_box = getattr(terminal, "_banner_box", None)
            if existing_box is container:
                # This is our existing banner_box, just prepend new banner
                container.prepend(banner)
                terminal._error_banner = banner
                self.logger.info(
                    f"Showed error banner (reusing box) for terminal {terminal_id}: {session_name}"
                )
                return True
            else:
                # Different box, create new structure
                container.remove(scrolled_window)
                banner_box.append(scrolled_window)
                container.append(banner_box)
        else:
            self.logger.warning(
                f"Cannot show banner - unsupported container type: {type(container)}"
            )
            return False

        # Store references
        terminal._error_banner = banner
        terminal._banner_box = banner_box

        self.logger.info(
            f"Showed error banner for terminal {terminal_id}: {session_name}"
        )

        return True

    def hide_error_banner_for_terminal(self, terminal: Vte.Terminal) -> bool:
        """
        Hide and remove error banner from a terminal.

        Removes the banner and restores the original widget hierarchy.

        Args:
            terminal: The terminal to remove banner from.

        Returns:
            True if banner was removed, False if no banner existed.
        """
        terminal_id = getattr(terminal, "terminal_id", None)

        # Check if terminal has a banner
        if not hasattr(terminal, "_error_banner") or not terminal._error_banner:
            return False

        banner = terminal._error_banner
        banner_box = getattr(terminal, "_banner_box", None)

        # Find the scrolled window
        scrolled_window = terminal.get_parent()
        if not isinstance(scrolled_window, Gtk.ScrolledWindow):
            # Banner may have been removed already
            terminal._error_banner = None
            terminal._banner_box = None
            return False

        if banner_box:
            # Get the container holding the banner_box
            container = banner_box.get_parent()

            # Remove banner from banner_box
            banner_box.remove(banner)

            # Remove scrolled window from banner_box and restore to container
            banner_box.remove(scrolled_window)

            if isinstance(container, Adw.Bin):
                container.set_child(None)
                container.set_child(scrolled_window)
            elif isinstance(container, Adw.ToolbarView):
                container.set_content(None)
                container.set_content(scrolled_window)
        else:
            # Simple case - just remove the banner from its parent
            parent = banner.get_parent()
            if parent:
                parent.remove(banner)

        # Clear references
        terminal._error_banner = None
        terminal._banner_box = None

        self.logger.info(f"Removed error banner for terminal {terminal_id}")

        return True

    def has_error_banner(self, terminal: Vte.Terminal) -> bool:
        """Check if terminal has an active error banner."""
        return hasattr(terminal, "_error_banner") and terminal._error_banner is not None

    def _handle_banner_action(
        self,
        action,
        terminal: Vte.Terminal,
        session: Optional[SessionItem],
        terminal_id: int,
        config: dict,
    ) -> None:
        """Handle action from error banner."""
        from ..ui.widgets.ssh_error_banner import BannerAction

        self.logger.info(f"Banner action: {action} for terminal {terminal_id}")

        if action == BannerAction.RETRY:
            if session:
                # Hide banner while attempting
                self.hide_error_banner_for_terminal(terminal)

                timeout = config.get("timeout", 30)
                GLib.idle_add(
                    self.terminal_manager._retry_ssh_in_same_terminal,
                    terminal,
                    terminal_id,
                    session,
                    timeout,
                )

        elif action == BannerAction.AUTO_RECONNECT:
            if session:
                # Hide banner while attempting
                self.hide_error_banner_for_terminal(terminal)

                duration = config.get("duration_mins", 5)
                interval = config.get("interval_secs", 10)
                timeout = config.get("timeout_secs", 30)

                # Unmark terminal as closing
                self.terminal_manager.lifecycle_manager.unmark_terminal_closing(
                    terminal_id
                )

                # Start auto-reconnect
                self.terminal_manager.start_auto_reconnect(
                    terminal, terminal_id, session, duration, interval, timeout
                )

        elif action == BannerAction.CLOSE:
            # Hide banner and cleanup terminal
            self.hide_error_banner_for_terminal(terminal)

            # Get identifier for cleanup
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            identifier = info.get("identifier") if info else session

            # Cleanup the terminal UI
            self.terminal_manager._cleanup_terminal_ui(
                terminal, terminal_id, 1, identifier
            )

        elif action == BannerAction.EDIT_SESSION:
            if session:
                # Hide banner before opening dialog
                self.hide_error_banner_for_terminal(terminal)

                # Open session edit dialog
                self._open_session_edit_dialog(session, terminal, terminal_id)

        elif action == BannerAction.FIX_HOST_KEY:
            if session:
                # Hide banner while fixing
                self.hide_error_banner_for_terminal(terminal)

                # Fix host key and retry
                self._fix_host_key_and_retry(session, terminal, terminal_id)

    def _open_session_edit_dialog(
        self,
        session: SessionItem,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> None:
        """
        Open the session edit dialog for fixing credentials.

        After the user saves changes, the connection will be retried automatically.
        """
        from ..ui.dialogs import SessionEditDialog

        parent_window = self.terminal_manager.parent_window
        if not parent_window:
            self.logger.warning("Cannot open session edit dialog - no parent window")
            return

        # Find session position in store
        session_store = parent_window.session_store
        position = -1
        for i, s in enumerate(session_store):
            if s.name == session.name:
                position = i
                break

        def on_dialog_closed(dialog):
            """Called when the edit dialog is closed."""
            # Always retry connection after edit dialog is closed
            # The user opened the dialog to fix credentials, so we should try again
            self.logger.info(f"Session edit dialog closed, retrying connection for {session.name}")
            GLib.idle_add(
                self.terminal_manager._retry_ssh_in_same_terminal,
                terminal,
                terminal_id,
                session,
                30,  # Default timeout
            )

        dialog = SessionEditDialog(
            parent_window,
            session,
            session_store,
            position,
            parent_window.folder_store,
            settings_manager=parent_window.settings_manager,
        )
        dialog.connect("close-request", lambda d: on_dialog_closed(d) or False)
        dialog.present()

    def _fix_host_key_and_retry(
        self,
        session: SessionItem,
        terminal: Vte.Terminal,
        terminal_id: int,
    ) -> None:
        """
        Fix SSH host key verification error by removing old key and retrying.

        This removes the offending key from ~/.ssh/known_hosts and
        attempts to reconnect the session.
        """
        import subprocess

        host = session.host
        port = session.port or 22

        # Remove old host key using ssh-keygen
        try:
            # Remove by hostname
            subprocess.run(
                ["ssh-keygen", "-R", host],
                capture_output=True,
                timeout=5,
            )

            # Also remove by hostname:port if non-standard port
            if port != 22:
                subprocess.run(
                    ["ssh-keygen", "-R", f"[{host}]:{port}"],
                    capture_output=True,
                    timeout=5,
                )

            # Display success message in terminal
            terminal.feed(
                f"\r\n\x1b[32m[Host Key] Removed old key for {host}\x1b[0m\r\n".encode("utf-8")
            )
            terminal.feed(b"\x1b[33m[Host Key] Reconnecting...\x1b[0m\r\n")

            self.logger.info(f"Removed host key for {host} and retrying connection")

            # Retry connection
            GLib.idle_add(
                self.terminal_manager._retry_ssh_in_same_terminal,
                terminal,
                terminal_id,
                session,
                30,  # Default timeout
            )

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout removing host key for {host}")
            terminal.feed(b"\r\n\x1b[31m[Host Key] Timeout removing key\x1b[0m\r\n")
        except Exception as e:
            self.logger.error(f"Failed to remove host key for {host}: {e}")
            terminal.feed(
                f"\r\n\x1b[31m[Host Key] Failed: {e}\x1b[0m\r\n".encode("utf-8")
            )

    def _find_pane_and_parent(self, terminal: Vte.Terminal) -> tuple:
        """
        Walks up the widget tree from a terminal to find its direct pane
        (the widget that should be replaced in a split) and that pane's
        parent container.
        """
        widget = terminal
        while widget:
            parent = widget.get_parent()
            if isinstance(parent, (Gtk.Paned, Adw.Bin)):
                return widget, parent
            widget = parent
        return None, None

    def _find_terminals_recursive(
        self, widget, terminals_list: List[Vte.Terminal]
    ) -> None:
        """Recursively find all Vte.Terminal widgets within a container."""
        if isinstance(widget, Adw.ToolbarView):
            if hasattr(widget, "terminal") and isinstance(
                widget.terminal, Vte.Terminal
            ):
                terminals_list.append(widget.terminal)
            return

        if isinstance(widget, Gtk.ScrolledWindow) and isinstance(
            widget.get_child(), Vte.Terminal
        ):
            terminals_list.append(widget.get_child())
            return
        if isinstance(widget, Gtk.Paned):
            if start_child := widget.get_start_child():
                self._find_terminals_recursive(start_child, terminals_list)
            if end_child := widget.get_end_child():
                self._find_terminals_recursive(end_child, terminals_list)
            return
        if hasattr(widget, "get_child") and (child := widget.get_child()):
            self._find_terminals_recursive(child, terminals_list)

    def _quit_application(self) -> bool:
        if self.on_quit_application:
            self.on_quit_application()
        return False

    def _remove_pane_ui(self, pane_to_remove, parent_paned):
        if not isinstance(parent_paned, Gtk.Paned):
            self.logger.warning(
                f"Attempted to remove pane from a non-paned container: {type(parent_paned)}"
            )
            return

        is_start_child = parent_paned.get_start_child() == pane_to_remove
        survivor_pane = (
            parent_paned.get_end_child()
            if is_start_child
            else parent_paned.get_start_child()
        )
        if not survivor_pane:
            return

        grandparent = parent_paned.get_parent()
        if not grandparent:
            return

        survivor_terminals = []
        self._find_terminals_recursive(survivor_pane, survivor_terminals)
        survivor_terminal = survivor_terminals[0] if survivor_terminals else None

        parent_paned.set_focus_child(None)
        parent_paned.set_start_child(None)
        parent_paned.set_end_child(None)

        if isinstance(grandparent, Gtk.Paned):
            is_grandparent_start = grandparent.get_start_child() == parent_paned
            if is_grandparent_start:
                grandparent.set_start_child(survivor_pane)
            else:
                grandparent.set_end_child(survivor_pane)
        elif hasattr(grandparent, "set_child"):
            grandparent.set_child(survivor_pane)

        is_last_split = not isinstance(grandparent, Gtk.Paned)
        if is_last_split and isinstance(survivor_pane, Adw.ToolbarView):
            scrolled_win_child = survivor_pane.get_content()
            if hasattr(grandparent, "set_child"):
                survivor_pane.set_content(None)
                grandparent.set_child(scrolled_win_child)

        def _restore_focus():
            if survivor_terminal and survivor_terminal.get_realized():
                survivor_terminal.grab_focus()
            return False

        def _restore_focus_and_update_titles():
            _restore_focus()
            self.update_all_tab_titles()
            return False

        GLib.idle_add(_restore_focus_and_update_titles)

    def close_pane(self, terminal: Vte.Terminal) -> None:
        """Close a single pane within a tab."""
        self.terminal_manager.remove_terminal(terminal)

    def _on_move_to_tab_callback(self, terminal: Vte.Terminal):
        """Callback to move a terminal from a split pane to a new tab."""
        self.logger.info(f"Request to move terminal {terminal.terminal_id} to new tab.")
        pane_to_remove, parent_paned = self._find_pane_and_parent(terminal)

        if not isinstance(parent_paned, Gtk.Paned):
            self.logger.warning("Attempted to move a pane that is not in a split.")
            if hasattr(self.terminal_manager.parent_window, "toast_overlay"):
                toast = Adw.Toast(title=_("This is the only pane in the tab."))
                self.terminal_manager.parent_window.toast_overlay.add_toast(toast)
            return

        current_parent = terminal.get_parent()
        if current_parent and hasattr(current_parent, "set_child"):
            current_parent.set_child(None)

        self._remove_pane_ui(pane_to_remove, parent_paned)

        terminal_id = getattr(terminal, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        if isinstance(identifier, SessionItem):
            session = identifier
        else:
            session = SessionItem(name=str(identifier), session_type="local")

        self._create_tab_for_terminal(terminal, session)
        self.logger.info(f"Terminal {terminal_id} successfully moved to a new tab.")

    def split_horizontal(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.HORIZONTAL)

    def split_vertical(self, focused_terminal: Vte.Terminal) -> None:
        self._split_terminal(focused_terminal, Gtk.Orientation.VERTICAL)

    def _set_paned_position_from_ratio(self, paned: Gtk.Paned, ratio: float) -> bool:
        alloc = paned.get_allocation()
        total_size = (
            alloc.width
            if paned.get_orientation() == Gtk.Orientation.HORIZONTAL
            else alloc.height
        )
        if total_size > 0:
            paned.set_position(int(total_size * ratio))
        return False

    def _split_terminal(
        self, focused_terminal: Vte.Terminal, orientation: Gtk.Orientation
    ) -> None:
        with self._creation_lock:
            page = self.get_page_for_terminal(focused_terminal)
            if not page:
                self.logger.error("Cannot split: could not find parent page.")
                return

            terminal_id = getattr(focused_terminal, "terminal_id", None)
            info = self.terminal_manager.registry.get_terminal_info(terminal_id)
            identifier = info.get("identifier") if info else "Local"

            new_terminal = None
            new_pane_title = "Terminal"
            if isinstance(identifier, SessionItem):
                new_pane_title = identifier.name
                if identifier.is_ssh():
                    new_terminal = self.terminal_manager.create_ssh_terminal(identifier)
                else:
                    # Use session's local settings when splitting
                    effective_working_dir = (
                        getattr(identifier, "local_working_directory", None) or None
                    )
                    effective_command = (
                        getattr(identifier, "local_startup_command", None) or None
                    )
                    new_terminal = self.terminal_manager.create_local_terminal(
                        session=identifier,
                        working_directory=effective_working_dir,
                        execute_command=effective_command,
                    )
            else:
                new_pane_title = "Local"
                new_terminal = self.terminal_manager.create_local_terminal(
                    title=new_pane_title
                )

            if not new_terminal:
                self.logger.error("Failed to create new terminal for split.")
                return

            new_terminal.zashterminal_parent_page = page
            new_pane = _create_terminal_pane(
                new_terminal,
                new_pane_title,
                self.close_pane,
                self._on_move_to_tab_callback,
                self.terminal_manager.settings_manager,
            )
            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, new_terminal)
            new_terminal.add_controller(focus_controller)

            pane_to_replace, container = self._find_pane_and_parent(focused_terminal)
            if not pane_to_replace:
                self.logger.error("Could not find the pane to replace for splitting.")
                self.terminal_manager.remove_terminal(new_terminal)
                return

            if isinstance(pane_to_replace, Gtk.ScrolledWindow):
                uri = focused_terminal.get_current_directory_uri()
                title = "Terminal"
                if uri:
                    from urllib.parse import unquote, urlparse

                    path = unquote(urlparse(uri).path)
                    title = (
                        self.terminal_manager.osc7_tracker.parser._create_display_path(
                            path
                        )
                    )

                pane_to_replace.set_child(None)
                pane_being_split = _create_terminal_pane(
                    focused_terminal,
                    title,
                    self.close_pane,
                    self._on_move_to_tab_callback,
                    self.terminal_manager.settings_manager,
                )
            else:
                pane_being_split = pane_to_replace

            is_start_child = False
            if isinstance(container, Gtk.Paned):
                is_start_child = container.get_start_child() == pane_to_replace
                container.set_focus_child(None)
                if is_start_child:
                    container.set_start_child(None)
                else:
                    container.set_end_child(None)
            elif isinstance(container, Adw.Bin):
                container.set_child(None)

            new_split_paned = Gtk.Paned(orientation=orientation)
            new_split_paned.set_start_child(pane_being_split)
            new_split_paned.set_end_child(new_pane)

            if isinstance(container, Gtk.Paned):
                if is_start_child:
                    container.set_start_child(new_split_paned)
                else:
                    container.set_end_child(new_split_paned)
            elif isinstance(container, Adw.Bin):
                container.set_child(new_split_paned)
            else:
                self.logger.error(
                    f"Cannot re-parent split: unknown container type {type(container)}"
                )
                self.terminal_manager.remove_terminal(new_terminal)
                return

            GLib.idle_add(self._set_paned_position_from_ratio, new_split_paned, 0.5)
            self._schedule_terminal_focus(new_terminal)
            self.update_all_tab_titles()

    def re_attach_detached_page(
        self,
        content: Gtk.Widget,
        title: str,
        session_type: str,
        file_manager_instance: Optional["FileManager"] = None,
    ) -> Adw.ViewStackPage:
        """Creates a new tab for a content widget that was detached from another window."""
        page_name = f"page_detached_{GLib.random_int()}"
        page = self.view_stack.add_titled(content, page_name, title)
        page.content_paned = content

        # Re-create a dummy session for the tab widget
        session = SessionItem(name=title, session_type=session_type)

        for terminal in self.get_all_terminals_in_page(page):
            terminal.zashterminal_parent_page = page

        tab_widget = self._create_tab_widget(page, session)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        if file_manager_instance:
            self.file_managers[page] = file_manager_instance
            file_manager_instance.reparent(
                self.terminal_manager.parent_window, self.terminal_manager
            )

        self.set_active_tab(tab_widget)
        if terminal := self.get_selected_terminal():
            self._schedule_terminal_focus(terminal)

        self.update_all_tab_titles()
        if self.on_tab_count_changed:
            self.on_tab_count_changed()
        return page

    def select_next_tab(self):
        """Selects the next tab in the list."""
        if not self.tabs or len(self.tabs) <= 1:
            return
        try:
            current_index = self.tabs.index(self.active_tab)
            next_index = (current_index + 1) % len(self.tabs)
            self.set_active_tab(self.tabs[next_index])
        except (ValueError, IndexError):
            if self.tabs:
                self.set_active_tab(self.tabs[0])

    def select_previous_tab(self):
        """Selects the previous tab in the list."""
        if not self.tabs or len(self.tabs) <= 1:
            return
        try:
            current_index = self.tabs.index(self.active_tab)
            prev_index = (current_index - 1 + len(self.tabs)) % len(self.tabs)
            self.set_active_tab(self.tabs[prev_index])
        except (ValueError, IndexError):
            if self.tabs:
                self.set_active_tab(self.tabs[0])

    def recreate_tab_from_structure(self, structure: dict):
        """Recreates a complete tab, including splits, from a saved structure."""
        if not structure:
            return

        root_widget = self._recreate_widget_from_node(structure)
        if not root_widget:
            self.logger.error("Failed to create root widget for tab restoration.")
            return

        # If the restored root is a single pane (ToolbarView), we need to unwrap it
        # to match the structure of a newly created single-terminal tab.
        terminal_area_content = root_widget
        if isinstance(root_widget, Adw.ToolbarView):
            scrolled_win = root_widget.get_content()
            if scrolled_win:
                root_widget.set_content(None)
                terminal_area_content = scrolled_win

        # Now, build the standard tab structure with the restored content
        terminal_area = Adw.Bin()
        terminal_area.set_child(terminal_area_content)

        content_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        content_paned.add_css_class("terminal-content-paned")
        content_paned.set_start_child(terminal_area)
        content_paned.set_resize_start_child(True)
        content_paned.set_shrink_start_child(False)
        content_paned.set_end_child(None)
        content_paned.set_resize_end_child(False)
        content_paned.set_shrink_end_child(True)

        first_terminal = None
        terminals = []
        self._find_terminals_recursive(root_widget, terminals)
        if terminals:
            first_terminal = terminals[0]

        if not first_terminal:
            self.logger.error("Restored tab contains no terminals.")
            for term in terminals:
                self.terminal_manager.remove_terminal(term)
            return

        terminal_id = getattr(first_terminal, "terminal_id", None)
        info = self.terminal_manager.registry.get_terminal_info(terminal_id)
        identifier = info.get("identifier") if info else "Local"

        if isinstance(identifier, SessionItem):
            session = identifier
        else:
            session = SessionItem(name=str(identifier), session_type="local")

        page_name = f"page_restored_{GLib.random_int()}"
        page = self.view_stack.add_titled(content_paned, page_name, session.name)
        page.content_paned = content_paned
        for term in terminals:
            term.zashterminal_parent_page = page

        tab_widget = self._create_tab_widget(page, session)
        self.tabs.append(tab_widget)
        self.pages[tab_widget] = page
        self.tab_bar_box.append(tab_widget)

        self.set_active_tab(tab_widget)
        self._schedule_terminal_focus(first_terminal)
        self.update_all_tab_titles()

        if self.on_tab_count_changed:
            self.on_tab_count_changed()

    def _recreate_widget_from_node(self, node: dict) -> Optional[Gtk.Widget]:
        """Recursively builds a widget tree from a serialized node."""
        if not node or "type" not in node:
            return None

        node_type = node["type"]

        if node_type == "terminal":
            terminal = None
            working_dir = node.get("working_dir")
            initial_command = (
                f'cd "{working_dir}"'
                if working_dir and node["session_type"] == "ssh"
                else None
            )
            title = node.get("session_name", "Terminal")
            session_type = node.get("session_type", "local")

            if session_type == "ssh":
                session = next(
                    (
                        s
                        for s in self.terminal_manager.parent_window.session_store
                        if s.name == node["session_name"]
                    ),
                    None,
                )
                if session and session.is_ssh():
                    terminal = self.terminal_manager.create_ssh_terminal(
                        session, initial_command=initial_command
                    )
                else:
                    self.logger.warning(
                        f"Could not find SSH session '{node['session_name']}' to restore, or type mismatch."
                    )
                    terminal = self.terminal_manager.create_local_terminal(
                        title=f"Missing: {title}"
                    )
            else:  # session_type is local
                session = next(
                    (
                        s
                        for s in self.terminal_manager.parent_window.session_store
                        if s.name == node["session_name"] and s.is_local()
                    ),
                    None,
                )
                terminal = self.terminal_manager.create_local_terminal(
                    session=session, title=title, working_directory=working_dir
                )

            if not terminal:
                return None

            # For splits, we need the pane wrapper. For single terminals, we'll unwrap it later.
            pane_widget = _create_terminal_pane(
                terminal,
                title,
                self.close_pane,
                self._on_move_to_tab_callback,
                self.terminal_manager.settings_manager,
            )

            focus_controller = Gtk.EventControllerFocus()
            focus_controller.connect("enter", self._on_pane_focus_in, terminal)
            terminal.add_controller(focus_controller)

            return pane_widget

        elif node_type == "paned":
            orientation = (
                Gtk.Orientation.HORIZONTAL
                if node["orientation"] == "horizontal"
                else Gtk.Orientation.VERTICAL
            )
            paned = Gtk.Paned(orientation=orientation)

            child1 = self._recreate_widget_from_node(node["child1"])
            child2 = self._recreate_widget_from_node(node["child2"])

            if not child1 or not child2:
                self.logger.error("Failed to recreate children for a split pane.")
                if child1:
                    self._find_and_remove_terminals(child1)
                if child2:
                    self._find_and_remove_terminals(child2)
                return None

            paned.set_start_child(child1)
            paned.set_end_child(child2)

            ratio = node.get("position_ratio", 0.5)
            GLib.idle_add(self._set_paned_position_from_ratio, paned, ratio)

            return paned

        return None

    def _find_and_remove_terminals(self, widget: Gtk.Widget):
        """Finds all terminals in a widget tree and removes them."""
        terminals = []
        self._find_terminals_recursive(widget, terminals)
        for term in terminals:
            self.terminal_manager.remove_terminal(term)

    def close_all_tabs(self):
        """Closes all currently open tabs by simulating a click on each close button."""
        for tab_widget in self.tabs[:]:
            self._on_tab_close_button_clicked(None, tab_widget)
