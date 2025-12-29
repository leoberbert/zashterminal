# zashterminal/utils/icons.py
"""
Icon loading utilities for Zashterminal.

Provides icon loading from bundled SVG files with proper symbolic icon
color adaptation via GTK's icon rendering system.

Usage:
    from zashterminal.utils.icons import icon_button, icon_image

    # These automatically use bundled icons when setting is 'zashterminal'
    button = icon_button("edit-find-symbolic")
    image = icon_image("folder-symbolic", size=24)
"""

import os
from pathlib import Path
from typing import Optional

import gi

from .tooltip_helper import get_tooltip_helper

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio


# Icon directory paths (in order of priority)
_ICON_PATHS = [
    "/usr/share/zashterminal/icons",
    str(Path(__file__).parent.parent / "icons"),  # src/zashterminal/icons
]

# Cached icon directory (resolved once)
_icon_dir: Optional[str] = None

# Flag set by app.py during startup based on icon_theme_strategy setting
# True = use bundled SVG icons, False = use system icons only
_use_bundled_icons: bool = True  # Default to bundled for performance


def _get_icon_dir() -> Optional[str]:
    """Get the bundled icons directory (cached)."""
    global _icon_dir
    if _icon_dir is None:
        for path in _ICON_PATHS:
            if os.path.isdir(path):
                _icon_dir = path
                break
    return _icon_dir


def get_icon_path(icon_name: str) -> Optional[str]:
    """Get the full path to a bundled icon SVG file.

    Args:
        icon_name: Icon name (with or without .svg extension)

    Returns:
        Full path to the SVG file, or None if not found
    """
    icon_dir = _get_icon_dir()
    if not icon_dir:
        return None

    # Normalize icon name - add .svg if not present
    if not icon_name.endswith(".svg"):
        icon_name = f"{icon_name}.svg"

    icon_path = os.path.join(icon_dir, icon_name)
    if os.path.isfile(icon_path):
        return icon_path
    return None


def has_bundled_icon(icon_name: str) -> bool:
    """Check if a bundled icon exists.

    Args:
        icon_name: Icon name (with or without .svg extension)

    Returns:
        True if the icon exists in the bundled icons directory
    """
    return get_icon_path(icon_name) is not None


def _create_image_from_file(icon_path: str, size: int) -> Gtk.Image:
    """Create a Gtk.Image from a file path using GIcon.

    Uses Gio.FileIcon which GTK can render with symbolic styling
    when the image has the 'symbolic' CSS class.
    """
    gfile = Gio.File.new_for_path(icon_path)
    file_icon = Gio.FileIcon.new(gfile)
    image = Gtk.Image.new_from_gicon(file_icon)
    image.set_pixel_size(size)
    return image


def create_icon_image(
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
    fallback_to_system: bool = True,
) -> Gtk.Image:
    """Create a Gtk.Image from an icon name.

    When use_bundled=True, loads directly from bundled SVG files.
    When use_bundled=False, uses system icons via GTK IconTheme.

    Args:
        icon_name: Icon name (e.g., "edit-find-symbolic")
        size: Icon size in pixels (default: 16)
        use_bundled: Try bundled icons first (None = use global setting)
        fallback_to_system: Fall back to system icons (default: True)

    Returns:
        Gtk.Image widget with the icon loaded
    """
    # Use global setting if not explicitly specified
    if use_bundled is None:
        use_bundled = _use_bundled_icons

    # Try bundled icon first
    if use_bundled:
        icon_path = get_icon_path(icon_name)
        if icon_path:
            image = _create_image_from_file(icon_path, size)
            # Add symbolic CSS class for theme color adaptation
            if icon_name.endswith("-symbolic"):
                image.add_css_class("icon-symbolic")
            return image

    # Fall back to system icons via icon name
    if fallback_to_system:
        image = Gtk.Image.new_from_icon_name(icon_name)
        image.set_pixel_size(size)
        return image

    # Return empty image if nothing works
    return Gtk.Image()


def create_icon_button(
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
    tooltip: Optional[str] = None,
    css_classes: Optional[list] = None,
) -> Gtk.Button:
    """Create a Gtk.Button with an icon.

    When use_bundled=True, loads directly from bundled SVG files.
    When use_bundled=False, uses system icons via GTK IconTheme.

    Args:
        icon_name: Icon name (e.g., "edit-find-symbolic")
        size: Icon size in pixels (default: 16)
        use_bundled: Try bundled icons first (None = use global setting)
        tooltip: Optional tooltip text
        css_classes: Optional list of CSS classes to add

    Returns:
        Gtk.Button with the icon
    """
    # Use global setting if not explicitly specified
    if use_bundled is None:
        use_bundled = _use_bundled_icons

    button = None

    # Try bundled icon first
    if use_bundled:
        icon_path = get_icon_path(icon_name)
        if icon_path:
            image = _create_image_from_file(icon_path, size)
            # Add symbolic CSS class for theme color adaptation
            if icon_name.endswith("-symbolic"):
                image.add_css_class("icon-symbolic")
            button = Gtk.Button()
            button.set_child(image)

    if button is None:
        # Fall back to system icon via icon name
        button = Gtk.Button.new_from_icon_name(icon_name)
        # Set icon size on the button's image child
        child = button.get_child()
        if isinstance(child, Gtk.Image):
            child.set_pixel_size(size)

    if tooltip:
        helper = get_tooltip_helper()
        if helper:
            helper.add_tooltip(button, tooltip)
        else:
            button.set_tooltip_text(tooltip)

    if css_classes:
        for css_class in css_classes:
            button.add_css_class(css_class)

    return button


def set_image_from_icon(
    image: Gtk.Image,
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
) -> None:
    """Set a Gtk.Image's content from an icon name.

    When use_bundled=True, loads directly from bundled SVG files.
    When use_bundled=False, uses system icons via GTK IconTheme.

    Args:
        image: Existing Gtk.Image widget to update
        icon_name: Icon name (e.g., "folder-symbolic")
        size: Icon size in pixels (default: 16)
        use_bundled: Try bundled icons first (None = use global setting)
    """
    # Use global setting if not explicitly specified
    if use_bundled is None:
        use_bundled = _use_bundled_icons

    # Try bundled icon first
    if use_bundled:
        icon_path = get_icon_path(icon_name)
        if icon_path:
            gfile = Gio.File.new_for_path(icon_path)
            file_icon = Gio.FileIcon.new(gfile)
            image.set_from_gicon(file_icon)
            image.set_pixel_size(size)
            # Add symbolic CSS class for theme color adaptation
            if icon_name.endswith("-symbolic"):
                image.add_css_class("icon-symbolic")
            return

    # Fall back to system icon
    image.set_from_icon_name(icon_name)
    image.set_pixel_size(size)


def set_button_icon(
    button: Gtk.Button,
    icon_name: str,
    size: int = 16,
    use_bundled: Optional[bool] = None,
) -> None:
    """Set a Gtk.Button's icon from an icon name.

    When use_bundled=True, loads directly from bundled SVG files.
    When use_bundled=False, uses system icons via GTK IconTheme.

    This function properly handles bundled icons, unlike button.set_icon_name()
    which only works with system icons.

    Args:
        button: Existing Gtk.Button widget to update
        icon_name: Icon name (e.g., "folder-symbolic")
        size: Icon size in pixels (default: 16)
        use_bundled: Try bundled icons first (None = use global setting)
    """
    # Check if button already has an image child we can update
    child = button.get_child()
    if isinstance(child, Gtk.Image):
        set_image_from_icon(child, icon_name, size, use_bundled)
    else:
        # Create new image and set as button child
        image = create_icon_image(icon_name, size, use_bundled)
        button.set_child(image)


# Convenience aliases for cleaner imports
icon_image = create_icon_image
icon_button = create_icon_button
