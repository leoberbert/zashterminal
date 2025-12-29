# zashterminal/ui/widgets/conversation_history.py

"""
Conversation History Panel for AI Chat.

A clean, simple conversation history using Adwaita standard components.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GObject, Gtk

from ...utils.icons import icon_button
from ...utils.tooltip_helper import get_tooltip_helper
from ...utils.translation_utils import _

if TYPE_CHECKING:
    from ...data.ai_history_manager import AIHistoryManager


def _get_relative_time(created_at: str) -> str:
    """Get human-readable relative time."""
    if not created_at:
        return ""

    try:
        # Parse ISO format
        if "T" in created_at:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(created_at)

        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt

        if delta.days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                mins = delta.seconds // 60
                if mins < 2:
                    return _("Just now")
                return _("{} min").format(mins)
            return _("{} hr").format(hours)
        elif delta.days == 1:
            return _("Yesterday")
        elif delta.days < 7:
            return _("{} days").format(delta.days)
        else:
            return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return ""


def _get_time_group(created_at: str) -> str:
    """Get time group label for a conversation."""
    if not created_at:
        return _("Older")

    try:
        if "T" in created_at:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(created_at)

        now = datetime.now()
        today = now.date()
        conv_date = dt.date()

        if conv_date == today:
            return _("Today")
        elif conv_date == today - timedelta(days=1):
            return _("Yesterday")
        elif conv_date > today - timedelta(days=7):
            return _("Last 7 Days")
        elif conv_date > today - timedelta(days=30):
            return _("This Month")
        else:
            return _("Older")
    except (ValueError, TypeError):
        return _("Older")


class ConversationHistoryPanel(Gtk.Box):
    """
    A panel for viewing and managing conversation history.

    Uses standard Adwaita components for better usability and reliability.

    Signals:
    - conversation-selected(conv_id): Emitted when a conversation is selected
    - conversation-deleted(conv_id): Emitted when a conversation is deleted
    - close-requested(): Emitted when user wants to close the panel
    """

    __gtype_name__ = "ConversationHistoryPanel"

    __gsignals__ = {
        "conversation-selected": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "conversation-deleted": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "close-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, history_manager: "AIHistoryManager"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._history_manager = history_manager
        self._search_query = ""
        self._current_conv_id = getattr(history_manager, "_current_conversation_id", None)
        self._row_map = {}  # conv_id -> row widget

        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        """Build the panel UI using standard Adwaita components."""
        self.set_size_request(420, 500)
        self.add_css_class("background")

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        title = Adw.WindowTitle(title=_("Conversation History"))
        header.set_title_widget(title)

        # Close button
        close_btn = icon_button("window-close-symbolic")
        close_btn.add_css_class("flat")
        get_tooltip_helper().add_tooltip(close_btn, _("Close"))
        close_btn.connect("clicked", lambda b: self.emit("close-requested"))
        header.pack_end(close_btn)

        # Clear all button (trash icon)
        self._clear_all_btn = icon_button("user-trash-symbolic")
        self._clear_all_btn.add_css_class("flat")
        get_tooltip_helper().add_tooltip(
            self._clear_all_btn, _("Delete All Conversations")
        )
        self._clear_all_btn.connect("clicked", self._on_clear_all)
        header.pack_end(self._clear_all_btn)

        # New conversation button
        new_btn = icon_button("list-add-symbolic")
        new_btn.add_css_class("flat")
        get_tooltip_helper().add_tooltip(new_btn, _("New Conversation"))
        new_btn.connect("clicked", self._on_new_conversation)
        header.pack_start(new_btn)

        self.append(header)

        # Search entry
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_box.set_margin_start(12)
        search_box.set_margin_end(12)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text(_("Search…"))
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        search_box.append(self._search_entry)

        self.append(search_box)

        # Main content with stack for empty state
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_vexpand(True)

        # Scrollable list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list_box.add_css_class("navigation-sidebar")
        self._list_box.set_header_func(self._header_func)
        self._list_box.connect("row-activated", self._on_row_activated)

        scrolled.set_child(self._list_box)
        self._stack.add_named(scrolled, "list")

        # Empty state
        empty_page = Adw.StatusPage()
        empty_page.set_icon_name("chat-symbolic")
        empty_page.set_title(_("No Conversations"))
        empty_page.set_description(_("Start chatting to create your first conversation"))
        self._stack.add_named(empty_page, "empty")

        self.append(self._stack)

    def _header_func(self, row, before):
        """Add group headers between different time groups."""
        if not hasattr(row, "time_group"):
            return

        current_group = row.time_group

        if before is None:
            # First row - always add header
            header = Gtk.Label(label=current_group)
            header.set_xalign(0)
            header.add_css_class("caption")
            header.add_css_class("dim-label")
            header.set_margin_start(12)
            header.set_margin_top(12)
            header.set_margin_bottom(6)
            row.set_header(header)
        elif hasattr(before, "time_group") and before.time_group != current_group:
            # Different group - add header
            header = Gtk.Label(label=current_group)
            header.set_xalign(0)
            header.add_css_class("caption")
            header.add_css_class("dim-label")
            header.set_margin_start(12)
            header.set_margin_top(12)
            header.set_margin_bottom(6)
            row.set_header(header)
        else:
            row.set_header(None)

    def refresh(self):
        """Refresh the conversation list."""
        # Clear existing rows
        self._row_map.clear()
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        # Get conversations
        conversations = self._history_manager.get_all_conversations()

        # Filter by search query
        if self._search_query:
            query_lower = self._search_query.lower()
            conversations = [
                conv for conv in conversations
                if any(
                    query_lower in msg.get("content", "").lower()
                    for msg in conv.get("messages", [])
                )
            ]

        # Show empty state if no conversations
        if not conversations:
            self._stack.set_visible_child_name("empty")
            return

        self._stack.set_visible_child_name("list")

        # Sort by created_at descending (newest first)
        conversations.sort(
            key=lambda c: c.get("created_at", ""),
            reverse=True
        )

        # Add rows
        for conv in conversations:
            row = self._create_conversation_row(conv)
            self._list_box.append(row)
            self._row_map[conv.get("id", "")] = row

    def _create_conversation_row(self, conv: dict) -> Gtk.ListBoxRow:
        """Create a row for a conversation."""
        conv_id = conv.get("id", "")
        messages = conv.get("messages", [])
        created_at = conv.get("created_at", "")

        # Get title from first user message
        title = _("New Conversation")
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "").replace("\n", " ").strip()
                title = content[:45] + "…" if len(content) > 45 else content
                break

        # Get preview from last message
        subtitle = ""
        if messages:
            last_msg = messages[-1]
            content = last_msg.get("content", "").replace("\n", " ").strip()
            role_prefix = "You: " if last_msg.get("role") == "user" else "AI: "
            subtitle = role_prefix + (content[:40] + "…" if len(content) > 40 else content)

        # Create row using Adw.ActionRow
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_activatable(True)

        # Store conv_id and time group on the row
        row.conv_id = conv_id
        row.time_group = _get_time_group(created_at)

        # Add time label as suffix
        time_text = _get_relative_time(created_at)
        if time_text:
            time_label = Gtk.Label(label=time_text)
            time_label.add_css_class("caption")
            time_label.add_css_class("dim-label")
            time_label.set_valign(Gtk.Align.CENTER)
            row.add_suffix(time_label)

        # Add message count
        msg_count = len(messages)
        if msg_count > 0:
            count_label = Gtk.Label(label=str(msg_count))
            count_label.add_css_class("caption")
            count_label.add_css_class("dim-label")
            count_label.set_valign(Gtk.Align.CENTER)
            row.add_suffix(count_label)

        # Add delete button (uses bundled icon)
        delete_btn = icon_button(
            "user-trash-symbolic", css_classes=["flat", "circular"]
        )
        delete_btn.set_valign(Gtk.Align.CENTER)
        get_tooltip_helper().add_tooltip(delete_btn, _("Delete"))
        delete_btn.connect("clicked", self._on_delete_clicked, conv_id)
        row.add_suffix(delete_btn)

        # Mark active conversation
        if conv_id == self._current_conv_id:
            row.add_css_class("activatable")

        return row

    def _on_row_activated(self, listbox, row):
        """Handle row activation (click/Enter)."""
        if hasattr(row, "conv_id"):
            self._current_conv_id = row.conv_id
            self.emit("conversation-selected", row.conv_id)

    def _on_delete_clicked(self, button, conv_id: str):
        """Handle delete button click with confirmation."""
        # Create confirmation dialog
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete Conversation?"))
        dialog.set_body(_("This conversation will be permanently deleted."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response == "delete":
                self._history_manager.delete_conversation(conv_id)
                self.emit("conversation-deleted", conv_id)
                self.refresh()

        dialog.connect("response", on_response)

        # Get the toplevel window
        root = self.get_root()
        if root:
            dialog.present(root)

    def _on_clear_all(self, button):
        """Handle clear all conversations button click."""
        # Get conversation count
        conversations = self._history_manager.get_all_conversations()
        count = len(conversations)

        if count == 0:
            return

        # Create confirmation dialog
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Delete All Conversations?"))
        dialog.set_body(
            _("This will permanently delete all {} conversations. This action cannot be undone.").format(count)
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("delete", _("Delete All"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response == "delete":
                self._history_manager.clear_all_history()
                self._current_conv_id = None
                self.emit("conversation-deleted", "")  # Empty string signals all deleted
                self.refresh()

        dialog.connect("response", on_response)

        # Get the toplevel window
        root = self.get_root()
        if root:
            dialog.present(root)

    def _on_search_changed(self, entry):
        """Handle search query change."""
        self._search_query = entry.get_text().strip()
        self.refresh()

    def _on_new_conversation(self, button):
        """Handle new conversation button click."""
        self._history_manager.new_conversation()
        self._current_conv_id = getattr(self._history_manager, "_current_conversation_id", None)
        self.emit("conversation-selected", self._current_conv_id or "")
        # Close the dialog after creating new conversation
        self.emit("close-requested")
