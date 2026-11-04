"""Compose ABNT2 dead keys without handing them to the GTK input method.

GTK's simple IM keeps a preedit after ``~`` / ``^`` and then swallows the next
Ctrl+C (it never reaches the PTY). Handling the dead key here lets Ctrl+C
interrupt the shell the way a normal terminal does.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

# text_to_feed, new_pending, stop_event
DeadKeyDecision = Tuple[Optional[str], Optional[int], bool]

_SHORTCUT_MODIFIERS = (
    Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.ALT_MASK
    | Gdk.ModifierType.SUPER_MASK
)


def _pairs(mapping: Dict[str, str]) -> Dict[int, str]:
    out: Dict[int, str] = {}
    for latin, composed in mapping.items():
        out[Gdk.keyval_from_name(latin)] = composed
    return out


_DEAD_KEYS: Dict[int, Dict[str, object]] = {
    Gdk.KEY_dead_tilde: {
        "literal": "~",
        "chars": _pairs(
            {
                "a": "ã",
                "A": "Ã",
                "n": "ñ",
                "N": "Ñ",
                "o": "õ",
                "O": "Õ",
            }
        ),
    },
    Gdk.KEY_dead_circumflex: {
        "literal": "^",
        "chars": _pairs(
            {
                "a": "â",
                "A": "Â",
                "e": "ê",
                "E": "Ê",
                "i": "î",
                "I": "Î",
                "o": "ô",
                "O": "Ô",
                "u": "û",
                "U": "Û",
            }
        ),
    },
    Gdk.KEY_dead_acute: {
        "literal": "´",
        "chars": _pairs(
            {
                "a": "á",
                "A": "Á",
                "e": "é",
                "E": "É",
                "i": "í",
                "I": "Í",
                "o": "ó",
                "O": "Ó",
                "u": "ú",
                "U": "Ú",
                "y": "ý",
                "Y": "Ý",
                "c": "ç",
                "C": "Ç",
            }
        ),
    },
    Gdk.KEY_dead_grave: {
        "literal": "`",
        "chars": _pairs(
            {
                "a": "à",
                "A": "À",
                "e": "è",
                "E": "È",
                "i": "ì",
                "I": "Ì",
                "o": "ò",
                "O": "Ò",
                "u": "ù",
                "U": "Ù",
            }
        ),
    },
}


def _printable(keyval: int) -> str:
    codepoint = Gdk.keyval_to_unicode(keyval)
    if not codepoint:
        return ""
    char = chr(codepoint)
    return char if char.isprintable() else ""


def resolve_dead_key(
    pending: Optional[int],
    keyval: int,
    state: Gdk.ModifierType,
) -> DeadKeyDecision:
    """Decide what a key does while an ABNT2 dead key may be pending.

    Returns ``(text_to_feed, new_pending, stop_event)``.
    ``stop_event`` means the key must not reach GTK's input method.
    """
    shortcut = bool(state & _SHORTCUT_MODIFIERS)

    if pending and (shortcut or keyval in (Gdk.KEY_Escape, Gdk.KEY_Return, Gdk.KEY_KP_Enter)):
        # Ctrl+C and other shortcuts must reach the terminal unchanged.
        return None, None, False

    if pending and keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
        return None, None, True

    if keyval in _DEAD_KEYS and not shortcut:
        if pending == keyval:
            literal = str(_DEAD_KEYS[keyval]["literal"])
            return literal, None, True
        if pending:
            previous = str(_DEAD_KEYS[pending]["literal"])
            return previous, keyval, True
        return None, keyval, True

    if not pending:
        return None, None, False

    spec = _DEAD_KEYS.get(pending)
    if spec is None:
        return None, None, False

    if keyval in (Gdk.KEY_space, Gdk.KEY_KP_Space):
        return str(spec["literal"]), None, True

    chars: Dict[int, str] = spec["chars"]  # type: ignore[assignment]
    composed = chars.get(keyval)
    if composed:
        return composed, None, True

    extra = _printable(keyval)
    if extra:
        return str(spec["literal"]) + extra, None, True
    return None, None, False
