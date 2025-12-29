# zashterminal/helpers.py

from typing import Set
from urllib.parse import urlparse

from gi.repository import Gtk

from .utils.logger import get_logger
from .utils.security import InputSanitizer


def is_valid_url(text: str) -> bool:
    """
    Checks if a string is a valid URL or an email address.
    """
    if not text:
        return False

    stripped_text = text.strip()

    # Check for email-like strings first, which urlparse might not handle as a URL
    if "@" in stripped_text and "." in stripped_text.split("@")[-1]:
        # A simple check to avoid matching things like 'user@host' without a TLD
        # and also avoid re-matching things that are already full mailto links.
        if not any(stripped_text.startswith(s) for s in ["http", "ftp", "mailto"]):
            return True

    # Use urlparse for standard URL schemes
    try:
        result = urlparse(stripped_text)
        return bool(result.scheme and result.netloc)
    except Exception:
        return False


def generate_unique_name(base_name: str, existing_names: Set[str]) -> str:
    """
    Generate a unique name by appending a number if the base name already exists.

    Args:
        base_name: The desired base name
        existing_names: Set of existing names to avoid

    Returns:
        A unique name that doesn't conflict with existing names
    """
    logger = get_logger("zashterminal.helpers")
    try:
        sanitized_base = InputSanitizer.sanitize_filename(base_name)
        if sanitized_base not in existing_names:
            return sanitized_base
        counter = 1
        while f"{sanitized_base} ({counter})" in existing_names:
            counter += 1
        return f"{sanitized_base} ({counter})"
    except Exception as e:
        logger.error(f"Error generating unique name for '{base_name}': {e}")
        # Fallback to a simpler logic in case of unexpected errors
        if base_name not in existing_names:
            return base_name
        counter = 1
        while f"{base_name} ({counter})" in existing_names:
            counter += 1
        return f"{base_name} ({counter})"


def accelerator_to_label(accelerator: str) -> str:
    """Convert GTK accelerator string to a human-readable label."""
    if not accelerator:
        return ""

    def _manual_conversion(accel_str: str) -> str:
        """Manual conversion for robustness."""
        clean_accel = accel_str.replace("<", "").replace(">", "+")
        replacements = {
            "Control": "Ctrl",
            "Shift": "Shift",
            "Alt": "Alt",
            "Super": "Super",
            "Meta": "Meta",
        }
        result = clean_accel
        for old, new in replacements.items():
            result = result.replace(old, new)

        key_replacements = {
            "plus": "+",
            "minus": "-",
            "Return": "Enter",
            "BackSpace": "Backspace",
            "Delete": "Del",
            "Insert": "Ins",
            "space": "Space",
            "Tab": "Tab",
            "Escape": "Esc",
            "comma": ",",
            "period": ".",
            "slash": "/",
            "backslash": "\\",
            "semicolon": ";",
            "apostrophe": "'",
            "grave": "`",
            "bracketleft": "[",
            "bracketright": "]",
            "equal": "=",
        }
        parts = result.split("+")
        if parts:
            last_part = parts[-1].lower()
            for old_key, new_key in key_replacements.items():
                if last_part == old_key:
                    parts[-1] = new_key
                    break
            else:
                if len(parts[-1]) == 1:
                    parts[-1] = parts[-1].upper()
        return "+".join(parts)

    try:
        success, keyval, mods = Gtk.accelerator_parse(accelerator)
        if not success or keyval == 0:
            return _manual_conversion(accelerator)

        key_name = Gtk.accelerator_get_label(keyval, mods)
        return key_name if key_name else _manual_conversion(accelerator)
    except Exception:
        return _manual_conversion(accelerator)


def create_themed_popover_menu(menu_model, parent_widget=None):
    """
    Create a PopoverMenu with the zashterminal-popover CSS class for theming.

    Use this helper instead of Gtk.PopoverMenu.new_from_model() directly
    to ensure consistent theming across the application.

    Args:
        menu_model: Gio.MenuModel for the popover
        parent_widget: Optional widget to set as parent

    Returns:
        Gtk.PopoverMenu with CSS class applied
    """
    popover = Gtk.PopoverMenu.new_from_model(menu_model)
    popover.add_css_class("zashterminal-popover")
    popover.set_autohide(True)
    popover.set_has_arrow(False)

    if parent_widget is not None:
        if popover.get_parent() is not None:
            popover.unparent()
        popover.set_parent(parent_widget)

    return popover
