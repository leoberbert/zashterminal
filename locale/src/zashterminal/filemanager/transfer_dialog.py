# zashterminal/filemanager/transfer_dialog.py
"""Transfer history dialog with elite Adwaita UI design following GNOME HIG."""
import time
from datetime import datetime
from typing import Callable, Dict

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango

from ..utils.icons import icon_button
from ..utils.logger import get_logger
from ..utils.tooltip_helper import get_tooltip_helper
from ..utils.translation_utils import _
from .transfer_manager import TransferItem, TransferStatus, TransferType


class TransferRow(Gtk.Box):
    """A polished transfer row with professional alignment and visual hierarchy."""

    __gtype_name__ = "TransferRow"

    def __init__(
        self,
        transfer: TransferItem,
        transfer_manager,
        on_remove_callback: Callable[[str], None],
    ):
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_top=10,
            margin_bottom=10,
            margin_start=16,
            margin_end=16,
        )
        self.transfer = transfer
        self.transfer_manager = transfer_manager
        self.on_remove_callback = on_remove_callback
        self.logger = get_logger(__name__)

        self._build_ui()
        self.update_state()

    def _build_ui(self):
        """Build a professional transfer row with proper visual hierarchy."""
        # Transfer direction icon with fixed size container for alignment
        icon_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            width_request=32,
        )
        self.append(icon_container)

        icon_name = (
            "folder-download-symbolic"
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else "folder-upload-symbolic"
        )
        self.type_icon = Gtk.Image.new_from_icon_name(icon_name)
        self.type_icon.set_pixel_size(20)
        self.type_icon.add_css_class("dim-label")
        icon_container.append(self.type_icon)

        # Main content area with filename, details, and progress
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            hexpand=True,
            valign=Gtk.Align.CENTER,
        )
        self.append(content_box)

        # Header row: filename and status badge
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content_box.append(header_row)

        self.filename_label = Gtk.Label(xalign=0.0, hexpand=True)
        self.filename_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.filename_label.set_max_width_chars(40)
        self.filename_label.add_css_class("title-4")
        header_row.append(self.filename_label)

        # Status badge container
        self.status_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            valign=Gtk.Align.CENTER,
        )
        header_row.append(self.status_box)

        self.status_icon = Gtk.Image()
        self.status_icon.set_pixel_size(12)
        self.status_box.append(self.status_icon)

        self.status_label = Gtk.Label()
        self.status_label.add_css_class("caption")
        self.status_box.append(self.status_label)

        # Details row: metadata (size, time, speed)
        self.details_label = Gtk.Label(xalign=0.0)
        self.details_label.add_css_class("dim-label")
        self.details_label.add_css_class("caption")
        content_box.append(self.details_label)

        # Progress bar container (visible only during active transfer)
        self.progress_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
        )
        content_box.append(self.progress_container)

        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_hexpand(True)
        self.progress_bar.add_css_class("osd")
        self.progress_container.append(self.progress_bar)

        self.progress_label = Gtk.Label(xalign=0.0)
        self.progress_label.add_css_class("caption")
        self.progress_label.add_css_class("dim-label")
        self.progress_container.append(self.progress_label)

        # Action buttons container with fixed width for alignment
        action_container = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            valign=Gtk.Align.CENTER,
            width_request=40,
        )
        self.append(action_container)

        self.cancel_button = icon_button("media-playback-stop-symbolic")
        self.cancel_button.add_css_class("flat")
        self.cancel_button.add_css_class("circular")
        self.cancel_button.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(self.cancel_button, _("Cancel"))
        self.cancel_button.connect(
            "clicked", lambda _: self.transfer_manager.cancel_transfer(self.transfer.id)
        )
        action_container.append(self.cancel_button)

        self.remove_button = icon_button("edit-delete-symbolic")
        self.remove_button.add_css_class("flat")
        self.remove_button.add_css_class("circular")
        self.remove_button.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(self.remove_button, _("Remove"))
        self.remove_button.connect(
            "clicked", lambda _: self.on_remove_callback(self.transfer.id)
        )
        action_container.append(self.remove_button)

    def update_state(self):
        """Update the display based on transfer status."""
        status = self.transfer.status
        size_str = self._format_file_size(self.transfer.file_size)

        # Update filename
        self.filename_label.set_label(self.transfer.filename)

        # Reset icon color classes
        for css_class in ["success", "error", "warning", "accent"]:
            self.type_icon.remove_css_class(css_class)
            self.status_icon.remove_css_class(css_class)
            self.status_label.remove_css_class(css_class)

        is_final_state = status in [
            TransferStatus.COMPLETED,
            TransferStatus.FAILED,
            TransferStatus.CANCELLED,
        ]

        # Show/hide buttons based on state
        self.cancel_button.set_visible(not is_final_state)
        self.remove_button.set_visible(is_final_state)

        # Build date string for completed transfers
        date_str = ""
        if self.transfer.start_time:
            dt = datetime.fromtimestamp(self.transfer.start_time)
            today = datetime.now().date()
            if dt.date() == today:
                date_str = dt.strftime("%H:%M")
            else:
                date_str = dt.strftime("%d %b %H:%M")

        type_str = (
            _("Download")
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else _("Upload")
        )

        if status == TransferStatus.PENDING:
            self._set_status(_("Queued"), "content-loading-symbolic", "dim-label")
            self.details_label.set_label(f"{type_str} • {size_str}")
            self.progress_container.set_visible(False)

        elif status == TransferStatus.IN_PROGRESS:
            self.status_box.set_visible(False)
            self.progress_container.set_visible(True)
            self.type_icon.add_css_class("accent")
            self.update_progress()
            return

        elif status == TransferStatus.COMPLETED:
            self._set_status(_("Done"), "emblem-ok-symbolic", "success")
            duration = self.transfer.get_duration()
            duration_str = self._format_duration(duration) if duration else ""

            details = [type_str, size_str]
            if duration_str:
                details.append(duration_str)
            if date_str:
                details.append(date_str)
            self.details_label.set_label(" • ".join(details))

            self.progress_container.set_visible(False)
            self.type_icon.add_css_class("success")

        elif status == TransferStatus.FAILED:
            self._set_status(_("Failed"), "dialog-error-symbolic", "error")
            error_msg = self.transfer.error_message or _("Unknown error")

            details = [type_str, size_str]
            if date_str:
                details.append(date_str)
            self.details_label.set_label(" • ".join(details))

            # Show error as tooltip on the row
            get_tooltip_helper().add_tooltip(self, error_msg)

            self.progress_container.set_visible(False)
            self.type_icon.add_css_class("error")

        elif status == TransferStatus.CANCELLED:
            self._set_status(_("Cancelled"), "process-stop-symbolic", "warning")

            details = [type_str, size_str]
            if date_str:
                details.append(date_str)
            self.details_label.set_label(" • ".join(details))

            self.progress_container.set_visible(False)
            self.type_icon.add_css_class("warning")

        self.status_box.set_visible(True)

    def _set_status(self, text: str, icon: str, css_class: str):
        """Set status badge with icon and text."""
        self.status_icon.set_from_icon_name(icon)
        self.status_icon.add_css_class(css_class)
        self.status_label.set_label(text)
        self.status_label.add_css_class(css_class)

    def update_progress(self):
        """Update progress bar and details for active transfers."""
        if self.transfer.status != TransferStatus.IN_PROGRESS:
            return

        progress = self.transfer.progress
        self.progress_bar.set_fraction(progress / 100.0)

        size_str = self._format_file_size(self.transfer.file_size)
        type_str = (
            _("Downloading")
            if self.transfer.transfer_type == TransferType.DOWNLOAD
            else _("Uploading")
        )

        # Update details label with type and size
        self.details_label.set_label(f"{type_str} • {size_str}")

        # Build progress details
        progress_parts = [f"{progress:.0f}%"]

        if self.transfer.start_time:
            elapsed = time.time() - self.transfer.start_time
            if elapsed > 0.5 and self.transfer.file_size > 0:
                bytes_transferred = (progress / 100.0) * self.transfer.file_size
                speed = bytes_transferred / elapsed
                progress_parts.append(f"{self._format_file_size(int(speed))}/s")

                if speed > 0 and progress < 100:
                    remaining_bytes = self.transfer.file_size - bytes_transferred
                    eta_seconds = remaining_bytes / speed
                    progress_parts.append(f"{self._format_duration(eta_seconds)} left")

        self.progress_label.set_label(" • ".join(progress_parts))

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size with appropriate unit."""
        if size_bytes == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        size = float(size_bytes)
        while size >= 1024 and i < len(units) - 1:
            size /= 1024.0
            i += 1
        if i == 0:
            return f"{int(size)} {units[i]}"
        return f"{size:.1f} {units[i]}"

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            m, s = divmod(int(seconds), 60)
            return f"{m}m {s}s" if s else f"{m}m"
        else:
            h, remainder = divmod(int(seconds), 3600)
            m = remainder // 60
            return f"{h}h {m}m" if m else f"{h}h"


class TransferManagerDialog(Adw.Window):
    """Elite transfer history dialog following GNOME HIG and WCAG 2.1 standards."""

    __gtype_name__ = "TransferManagerDialog"

    def __init__(self, transfer_manager, parent_window):
        super().__init__(transient_for=parent_window)
        self.transfer_manager = transfer_manager
        self.parent_window = parent_window
        self.logger = get_logger(__name__)
        self.transfer_rows: Dict[str, TransferRow] = {}
        self.handler_ids = []

        self.add_css_class("zashterminal-dialog")
        self.set_title(_("Transfers"))
        self.set_default_size(480, 500)
        self.set_modal(False)
        self.set_hide_on_close(True)

        self._build_ui()
        self._connect_signals()
        self._populate_transfers()
        self.connect("destroy", self._on_destroy)
        self.connect("show", self._on_show)
        self._apply_headerbar_transparency()

    def _apply_headerbar_transparency(self):
        """Apply headerbar transparency consistent with app settings."""
        try:
            if self.parent_window:
                settings_manager = getattr(self.parent_window, "settings_manager", None)
                if settings_manager:
                    settings_manager.apply_headerbar_transparency(self.header_bar)
        except Exception as e:
            self.logger.warning(f"Failed to apply headerbar transparency: {e}")

    def _on_show(self, window):
        """Refresh the transfer list when window is shown again after being hidden."""
        self._refresh_transfers()

    def _refresh_transfers(self):
        """Refresh the transfer list with current data."""
        current_ids = set(self.transfer_rows.keys())

        # Add any missing transfers (active or history)
        with self.transfer_manager._transfer_lock:
            active_transfers = list(self.transfer_manager.active_transfers.values())

        for transfer in active_transfers:
            if transfer.id not in current_ids:
                self._add_transfer_row(transfer)

        for transfer in self.transfer_manager.history:
            if transfer.id not in current_ids:
                self._add_transfer_row(transfer)

        # Update existing rows
        for transfer_id, row in list(self.transfer_rows.items()):
            transfer = self.transfer_manager.get_transfer(transfer_id) or next(
                (t for t in self.transfer_manager.history if t.id == transfer_id), None
            )
            if transfer:
                row.transfer = transfer
                row.update_state()

        self._update_view()

    def _on_destroy(self, window):
        """Safely disconnect all signal handlers when window is destroyed."""
        for handler_id in self.handler_ids:
            if self.transfer_manager and GObject.signal_handler_is_connected(
                self.transfer_manager, handler_id
            ):
                self.transfer_manager.disconnect(handler_id)
        self.handler_ids.clear()

    def _build_ui(self):
        """Build the dialog UI with proper structure."""
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar
        self.header_bar = Adw.HeaderBar()
        toolbar_view.add_top_bar(self.header_bar)

        # Header bar buttons in a box for proper spacing
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.header_bar.pack_end(button_box)

        # Clear button
        self.clear_button = Gtk.Button()
        self.clear_button.set_icon_name("user-trash-symbolic")
        self.clear_button.add_css_class("flat")
        get_tooltip_helper().add_tooltip(self.clear_button, _("Clear history"))
        self.clear_button.connect("clicked", self._on_clear_clicked)
        button_box.append(self.clear_button)

        # Cancel all button
        self.cancel_all_button = Gtk.Button()
        self.cancel_all_button.set_icon_name("media-playback-stop-symbolic")
        self.cancel_all_button.add_css_class("flat")
        self.cancel_all_button.add_css_class("destructive-action")
        get_tooltip_helper().add_tooltip(self.cancel_all_button, _("Cancel all"))
        self.cancel_all_button.connect("clicked", self._on_cancel_all_clicked)
        button_box.append(self.cancel_all_button)

        # Content stack for list vs empty state
        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(200)
        toolbar_view.set_content(self.content_stack)

        # Scrolled window with the transfer list
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Clamp for proper width on wide screens
        clamp = Adw.Clamp(maximum_size=550, tightening_threshold=400)
        scrolled.set_child(clamp)

        # Main list container
        list_box_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        clamp.set_child(list_box_container)

        self.transfer_listbox = Gtk.ListBox()
        self.transfer_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.transfer_listbox.add_css_class("boxed-list")
        list_box_container.append(self.transfer_listbox)

        self.content_stack.add_named(scrolled, "list")

        # Empty state with helpful message
        empty_page = Adw.StatusPage(
            title=_("No Transfers"),
            description=_("Upload or download files to see them here"),
            icon_name="folder-download-symbolic",
            vexpand=True,
        )
        self.content_stack.add_named(empty_page, "empty")

    def _update_view(self):
        """Switch between list and empty state based on content."""
        with self.transfer_manager._transfer_lock:
            active_count = len(self.transfer_manager.active_transfers)

        total = active_count + len(self.transfer_manager.history)
        self.content_stack.set_visible_child_name("list" if total > 0 else "empty")
        self._update_button_states()

    def _update_button_states(self):
        """Update button sensitivity based on current state."""
        with self.transfer_manager._transfer_lock:
            has_active = len(self.transfer_manager.active_transfers) > 0

        has_history = len(self.transfer_manager.history) > 0

        self.cancel_all_button.set_visible(has_active)
        self.clear_button.set_sensitive(has_history)

    def _connect_signals(self):
        """Connect to transfer manager signals."""
        signals = [
            "transfer-started",
            "transfer-completed",
            "transfer-failed",
            "transfer-cancelled",
        ]
        for sig in signals:
            handler_id = self.transfer_manager.connect(sig, self._on_transfer_change)
            self.handler_ids.append(handler_id)

        handler_id = self.transfer_manager.connect(
            "transfer-progress", self._on_transfer_progress
        )
        self.handler_ids.append(handler_id)

    def _populate_transfers(self):
        """Populate list with existing transfers."""
        with self.transfer_manager._transfer_lock:
            all_transfers = (
                list(self.transfer_manager.active_transfers.values())
                + self.transfer_manager.history
            )

        # Sort: active first, then by time (newest first)
        def sort_key(t):
            is_active = t.status in [TransferStatus.PENDING, TransferStatus.IN_PROGRESS]
            return (not is_active, -(t.start_time or 0))

        all_transfers.sort(key=sort_key)

        for transfer in all_transfers:
            self._add_transfer_row(transfer)

        self._update_view()

    def _add_transfer_row(self, transfer: TransferItem):
        """Add a new transfer row to the list."""
        if transfer.id in self.transfer_rows:
            row = self.transfer_rows[transfer.id]
            row.transfer = transfer
            row.update_state()
            return

        row = TransferRow(transfer, self.transfer_manager, self._on_remove_row)
        self.transfer_rows[transfer.id] = row

        # Wrap in ListBoxRow for proper ListBox integration
        list_row = Gtk.ListBoxRow()
        list_row.set_child(row)
        list_row.set_selectable(False)
        list_row.set_activatable(False)
        list_row.transfer_id = transfer.id  # Store ID for later reference

        # Insert active transfers at top, completed at bottom
        if transfer.status in [TransferStatus.PENDING, TransferStatus.IN_PROGRESS]:
            self.transfer_listbox.prepend(list_row)
        else:
            self.transfer_listbox.append(list_row)

    def _on_transfer_change(self, manager, transfer_id, *_):
        """Handle transfer state changes."""
        transfer = manager.get_transfer(transfer_id) or next(
            (t for t in manager.history if t.id == transfer_id), None
        )

        def update_ui():
            if transfer:
                self._add_transfer_row(transfer)
            self._update_view()
            return False

        GLib.idle_add(update_ui)

    def _on_transfer_progress(self, manager, transfer_id, progress):
        """Handle progress updates."""
        if transfer_id in self.transfer_rows:
            row = self.transfer_rows[transfer_id]
            transfer_obj = manager.get_transfer(transfer_id)
            if transfer_obj:
                row.transfer = transfer_obj
                GLib.idle_add(row.update_progress)

    def _on_cancel_all_clicked(self, button):
        """Cancel all active transfers."""
        with self.transfer_manager._transfer_lock:
            transfer_ids = list(self.transfer_manager.active_transfers.keys())

        for transfer_id in transfer_ids:
            self.transfer_manager.cancel_transfer(transfer_id)

    def _on_clear_clicked(self, button):
        """Show confirmation dialog for clearing history."""
        dialog = Adw.AlertDialog(
            heading=_("Clear Transfer History?"),
            body=_("This will remove all completed, failed, and cancelled transfers."),
            default_response="cancel",
            close_response="cancel",
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_clear_confirm)
        dialog.present(self)

    def _on_clear_confirm(self, dialog, response):
        """Handle clear confirmation response."""
        if response != "clear":
            return

        # Remove completed transfer rows from UI
        rows_to_remove = []
        row = self.transfer_listbox.get_first_child()
        while row:
            next_row = row.get_next_sibling()
            transfer_id = getattr(row, "transfer_id", None)
            if transfer_id and transfer_id in self.transfer_rows:
                transfer_row = self.transfer_rows[transfer_id]
                if transfer_row.transfer.status not in [
                    TransferStatus.IN_PROGRESS,
                    TransferStatus.PENDING,
                ]:
                    rows_to_remove.append((row, transfer_id))
            row = next_row

        for row, transfer_id in rows_to_remove:
            self.transfer_listbox.remove(row)
            self.transfer_rows.pop(transfer_id, None)

        # Clear history and persist
        self.transfer_manager.history.clear()
        self.transfer_manager._save_history()
        self._update_view()

    def _on_remove_row(self, transfer_id: str):
        """Remove a single row from history."""
        # Find and remove the ListBoxRow
        row = self.transfer_listbox.get_first_child()
        while row:
            if getattr(row, "transfer_id", None) == transfer_id:
                self.transfer_listbox.remove(row)
                break
            row = row.get_next_sibling()

        # Clean up tracking
        self.transfer_rows.pop(transfer_id, None)

        # Update history
        self.transfer_manager.history = [
            t for t in self.transfer_manager.history if t.id != transfer_id
        ]
        self.transfer_manager._save_history()
        self._update_view()
