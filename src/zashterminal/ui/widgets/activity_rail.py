# zashterminal/ui/widgets/activity_rail.py
"""Vertical activity rail (icon + caption buttons) at the window's left edge."""

from typing import Callable, Dict, Optional

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango

from ...utils.icons import icon_image
from ...utils.tooltip_helper import get_tooltip_helper


class ActivityRail(Gtk.Box):
    """Column of toggle buttons; each reports its state through a callback.

    The rail owns no application state: callers keep the real state elsewhere
    and mirror it back with :meth:`set_active`, which never fires the callback.
    """

    def __init__(self) -> None:
        super().__init__(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            css_classes=["zash-rail"],
        )
        self._buttons: Dict[str, Gtk.ToggleButton] = {}
        self._handlers: Dict[str, int] = {}

    def add_item(
        self,
        key: str,
        icon_name: str,
        label: str,
        tooltip: str,
        on_toggled: Callable[[bool], None],
        *,
        use_bundled: Optional[bool] = None,
        momentary: bool = False,
        shortcut_action: Optional[str] = None,
    ) -> Gtk.ToggleButton:
        """Append an item. ``momentary`` items fire once and never stay pressed."""
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3, halign=Gtk.Align.CENTER
        )
        content.append(icon_image(icon_name, size=20, use_bundled=use_bundled))
        caption = Gtk.Label(
            label=label,
            css_classes=["zash-rail-label"],
            ellipsize=Pango.EllipsizeMode.END,
            # Room for captions like "Commands"/"Histórico" before ellipsizing
            max_width_chars=12,
        )
        content.append(caption)

        button = Gtk.ToggleButton(css_classes=["zash-rail-item", "flat"])
        button.set_child(content)
        button.set_focus_on_click(False)

        helper = get_tooltip_helper()
        if shortcut_action:
            helper.add_tooltip_with_shortcut(button, tooltip, shortcut_action)
        else:
            helper.add_tooltip(button, tooltip)

        def handle(btn: Gtk.ToggleButton) -> None:
            active = btn.get_active()
            on_toggled(active)
            if momentary and active:
                self.set_active(key, False)

        self._handlers[key] = button.connect("toggled", handle)
        self._buttons[key] = button
        self.append(button)
        return button

    def set_active(self, key: str, active: bool) -> None:
        """Mirror external state without triggering the item's callback."""
        button = self._buttons.get(key)
        if button is None or button.get_active() == active:
            return
        handler = self._handlers[key]
        button.handler_block(handler)
        try:
            button.set_active(active)
        finally:
            button.handler_unblock(handler)

    def set_item_visible(self, key: str, visible: bool) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.set_visible(visible)

    def add_spacer(self) -> None:
        self.append(Gtk.Box(vexpand=True))
