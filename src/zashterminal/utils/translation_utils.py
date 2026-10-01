#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# translation_utils.py - Utilities for translation support
#
import gettext
import os
from pathlib import Path

# Determine locale directory (works in AppImage and system install)
locale_dir = '/usr/share/locale'  # Default for system install

# Check if we're in an AppImage
if 'APPIMAGE' in os.environ or 'APPDIR' in os.environ:
    # Running from AppImage
    # translation_utils.py is in: usr/share/zashterminal/utils/translation_utils.py
    # We need to get to: usr/share/locale
    script_dir = os.path.dirname(os.path.abspath(__file__))  # usr/share/zashterminal/utils
    app_dir = os.path.dirname(script_dir)                    # usr/share/zashterminal
    share_dir = os.path.dirname(app_dir)                     # usr/share
    appimage_locale = os.path.join(share_dir, 'locale')      # usr/share/locale

    if os.path.isdir(appimage_locale):
        locale_dir = appimage_locale

# Configure the translation text domain for zashterminal
gettext.bindtextdomain("zashterminal", locale_dir)
gettext.textdomain("zashterminal")

# Prefer compiled checkout catalogs during source runs. Installed/AppImage
# catalogs remain the fallback, and no compilation or writes happen at startup.
_translation = gettext.translation("zashterminal", localedir=locale_dir, fallback=True)
_checkout = Path(__file__).resolve().parents[3]
if (_checkout / "pyproject.toml").is_file() and (_checkout / "locale").is_dir():
    _local_translation = gettext.translation(
        "zashterminal", localedir=str(_checkout / "locale"), fallback=True
    )
    _local_translation.add_fallback(_translation)
    _translation = _local_translation

_ = _translation.gettext
