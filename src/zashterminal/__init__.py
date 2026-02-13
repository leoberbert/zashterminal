import argparse
import os
import signal
import sys

def apply_renderer_fix():
    """
    Selects the best GSK_RENDERER based on the desktop environment and hardware.
    This prevents crashes on GNOME/VMs while avoiding lag on KDE+Nvidia.
    """
    # If the user has already manually set a renderer, do not override it.
    if "GSK_RENDERER" in os.environ:
        return

    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    
    # Check if the NVIDIA proprietary driver is loaded.
    is_nvidia = os.path.exists("/sys/module/nvidia")

    # Detect if running inside a Virtual Machine.
    is_vm = False
    try:
        with open("/proc/cpuinfo", "r") as f:
            if "hypervisor" in f.read().lower():
                is_vm = True
    except Exception:
        pass

    # DECISION LOGIC:
    # 1. KDE/Plasma + NVIDIA: 'ngl' causes context menu lag/flicker.
    #    We leave it unset to let GTK choose the stable default (usually 'gl' or 'vulkan').
    if ("KDE" in desktop or "PLASMA" in desktop) and is_nvidia:
        return 

    # 2. GNOME or Virtual Machines: 'ngl' is often required for stability in GTK4.
    if is_vm or "GNOME" in desktop:
        os.environ["GSK_RENDERER"] = "gl"
    
    # 3. Default case (AMD/Intel): 'ngl' is generally the most compatible and modern path.
    else:
        os.environ["GSK_RENDERER"] = "ngl"

# The renderer selection MUST be set BEFORE importing GTK/GLib.
apply_renderer_fix()

_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
_desktop_upper = _desktop.upper()

# Fix for KDE Plasma (dead keys) on Wayland
if "KDE" in _desktop_upper or "PLASMA" in _desktop_upper:
    os.environ.setdefault("GTK_IM_MODULE", "gtk-im-context-simple")

# Fix for Hyprland (dead keys/accents issue)
if "HYPRLAND" in _desktop_upper:
    os.environ.setdefault("GTK_IM_MODULE", "gtk-im-context-simple")

if __package__ is None:
    import pathlib

    parent_dir = pathlib.Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    __package__ = "zashterminal"

_logger_module = None
_translation_module = None


def _get_logger_funcs():
    """Lazy load logger functions."""
    global _logger_module
    if _logger_module is None:
        from .utils import logger as _logger_module
    return _logger_module


def _get_translation():
    """Lazy load translation function."""
    global _translation_module
    if _translation_module is None:
        from .utils import translation_utils as _translation_module
    return _translation_module._


def setup_signal_handlers():
    """Set up signal handlers for graceful shutdown on Linux."""
    _ = _get_translation()

    def signal_handler(sig, frame):
        print("\n" + _("Received signal {}, shutting down gracefully...").format(sig))
        try:
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            app = Gtk.Application.get_default()
            if app:
                app.quit()
            else:
                sys.exit(0)
        except Exception:
            sys.exit(0)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except Exception as e:
        print(_("Warning: Could not set up signal handlers: {}").format(e))


def main() -> int:
    """Main entry point for the application."""
    logger_mod = _get_logger_funcs()
    _ = _get_translation()

    # Use a separate parser to handle debug/log flags before the main app starts
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--debug", "-d", action="store_true")
    pre_parser.add_argument("--log-level")
    pre_args, remaining_argv = pre_parser.parse_known_args()

    # Apply pre-launch log settings if provided
    if pre_args.debug:
        logger_mod.enable_debug_mode()
    elif pre_args.log_level:
        try:
            logger_mod.set_console_log_level(pre_args.log_level)
        except KeyError:
            print(f"Warning: Invalid log level '{pre_args.log_level}' provided.")

    logger = logger_mod.get_logger("zashterminal.main")

    # Tries to set the process title and logs failures
    try:
        import setproctitle

        setproctitle.setproctitle("zashterminal")
        logger.info("Process title set to 'zashterminal'.")
    except Exception as e:
        logger.error(f"Failed to set process title: {e}", exc_info=True)

    # Main parser for the application's command-line interface
    parser = argparse.ArgumentParser(
        prog="zashterminal",
        description=_(
            "Zashterminal - A modern terminal emulator with session management"
        ),
        epilog=_("For more information, visit: https://github.com/leoberbert/zashterminal/"),
    )

    # Custom version action to load APP_VERSION lazily (avoids loading GTK for --version)
    class LazyVersionAction(argparse.Action):
        def __call__(self, parser, _namespace, values, _option_string=None):
            from .settings.config import APP_VERSION

            print(f"zashterminal {APP_VERSION}")
            parser.exit()

    parser.add_argument(
        "--version",
        "-v",
        nargs=0,
        action=LazyVersionAction,
        help=_("Show version and exit"),
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help=_("Enable debug mode")
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=_("Set logging level"),
    )
    parser.add_argument(
        "--working-directory",
        "-w",
        metavar="DIR",
        help=_("Set the working directory for the initial terminal"),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=None,
        help=_("Working directory (positional argument)"),
    )
    parser.add_argument(
        "--execute",
        "-e",
        "-x",
        metavar="COMMAND",
        nargs=argparse.REMAINDER,
        help=_("Execute command in the terminal (takes all remaining arguments)"),
    )
    parser.add_argument(
        "--close-after-execute",
        action="store_true",
        help=_("Close terminal after executing command (only with --execute)"),
    )
    parser.add_argument(
        "--ssh",
        metavar="[USER@]HOST[:PORT][:/PATH]",
        help=_("Connect to SSH host with optional user, port, and remote path"),
    )
    parser.add_argument(
        "--convert-to-ssh", help=_("Convert KIO/GVFS URI path to SSH format")
    )
    parser.add_argument(
        "--new-window", action="store_true", help=_("Force opening a new window")
    )

    try:
        parser.parse_known_args()
    except SystemExit:
        return 0

    setup_signal_handlers()

    try:
        logger.info("Creating application instance")

        # Lazy import: only load the heavy GTK/Adw/VTE modules when actually running
        from .app import CommTerminalApp

        app = CommTerminalApp()
        return app.run(sys.argv)
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
        return 0
    except Exception as e:
        logger.critical(f"A fatal error occurred: {e}", exc_info=True)
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        # GTK4 uses GtkAlertDialog instead of GtkMessageDialog.run() (GTK3 pattern)
        alert = Gtk.AlertDialog()
        alert.set_message(_("Fatal Application Error"))
        alert.set_detail(_("Could not start Zashterminal.\n\nError: {}").format(e))
        alert.set_modal(True)
        alert.show(None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
