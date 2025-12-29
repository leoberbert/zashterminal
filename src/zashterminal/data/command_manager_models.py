# zashterminal/data/command_manager_models.py
"""
Data models for the Command Manager feature.
Provides enhanced command definitions with form fields, execution options,
and display customization.
"""

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional, Dict, Any

from ..settings.config import get_config_paths
from ..utils.logger import get_logger
from ..utils.translation_utils import _


class ExecutionMode(Enum):
    """How the command should be executed when clicked."""
    INSERT_ONLY = "insert_only"  # Just add to terminal, don't execute
    INSERT_AND_EXECUTE = "insert_and_execute"  # Add and press Enter
    SHOW_DIALOG = "show_dialog"  # Show a form dialog first


class DisplayMode(Enum):
    """How the command button should be displayed."""
    ICON_ONLY = "icon_only"
    TEXT_ONLY = "text_only"
    ICON_AND_TEXT = "icon_and_text"


class FieldType(Enum):
    """Types of form fields available in command dialogs."""
    TEXT = "text"  # Simple text input
    SWITCH = "switch"  # Boolean toggle
    DROPDOWN = "dropdown"  # Select from options
    NUMBER = "number"  # Numeric input
    FILE_PATH = "file_path"  # File chooser
    DIRECTORY_PATH = "directory_path"  # Directory chooser
    PASSWORD = "password"  # Password/secret input (masked)
    MULTI_SELECT = "multi_select"  # Multiple selections from options
    TEXT_AREA = "text_area"  # Multi-line text input
    SLIDER = "slider"  # Range slider with min/max
    RADIO = "radio"  # Radio button group (mutually exclusive)
    DATE_TIME = "date_time"  # Date/time picker
    COLOR = "color"  # Color picker


@dataclass(slots=True)
class CommandFormField:
    """
    Represents a form field in a command's dialog.
    Used to build dynamic command strings based on user input.
    """
    id: str  # Unique identifier for the field
    label: str  # Display label
    field_type: FieldType = FieldType.TEXT
    default_value: Any = ""
    placeholder: str = ""
    tooltip: str = ""
    required: bool = False
    # For SWITCH type: command_flag is added when switch is ON
    command_flag: str = ""
    # For SWITCH type: off_value is added when switch is OFF (can be empty)
    off_value: str = ""
    # For DROPDOWN type: list of (value, label) tuples
    options: List[tuple] = field(default_factory=list)
    # Position marker in command template (e.g., "{search_term}")
    template_key: str = ""
    # For NUMBER type
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    # Extra configuration for special field types (slider, date_time, color, text_area)
    extra_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["field_type"] = self.field_type.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "CommandFormField":
        """Create from dictionary."""
        data = data.copy()
        data["field_type"] = FieldType(data.get("field_type", "text"))
        # Convert options from list of lists to list of tuples
        if "options" in data and data["options"]:
            data["options"] = [tuple(opt) for opt in data["options"]]
        return cls(**data)


@dataclass(slots=True)
class CommandButton:
    """
    Represents a command button in the Command Manager.
    Enhanced from the old CommandItem with execution options and form support.
    """
    id: str  # Unique identifier
    name: str  # Display name
    description: str  # Help text / tooltip
    command_template: str  # Command with placeholders like {search_term}
    icon_name: str = "utilities-terminal-symbolic"  # GTK icon name
    display_mode: DisplayMode = DisplayMode.ICON_AND_TEXT
    execution_mode: ExecutionMode = ExecutionMode.INSERT_ONLY
    # Where to place cursor after insertion (0 = end, negative = from end)
    cursor_position: int = 0
    # Form fields for SHOW_DIALOG mode
    form_fields: List[CommandFormField] = field(default_factory=list)
    # Whether this is a built-in command
    is_builtin: bool = False
    # Category for grouping
    category: str = ""
    # Sort order within category
    sort_order: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "command_template": self.command_template,
            "icon_name": self.icon_name,
            "display_mode": self.display_mode.value,
            "execution_mode": self.execution_mode.value,
            "cursor_position": self.cursor_position,
            "form_fields": [f.to_dict() for f in self.form_fields],
            "is_builtin": self.is_builtin,
            "category": self.category,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CommandButton":
        """Create from dictionary."""
        data = data.copy()
        data["display_mode"] = DisplayMode(data.get("display_mode", "icon_and_text"))
        data["execution_mode"] = ExecutionMode(data.get("execution_mode", "insert_only"))
        data["form_fields"] = [
            CommandFormField.from_dict(f) for f in data.get("form_fields", [])
        ]
        return cls(**data)

    def build_command(self, field_values: Dict[str, Any] = None) -> str:
        """
        Build the final command string by substituting field values.
        
        Args:
            field_values: Dictionary mapping field IDs to their values
            
        Returns:
            The constructed command string
        """
        if not field_values:
            field_values = {}

        command = self.command_template

        for form_field in self.form_fields:
            value = field_values.get(form_field.id, form_field.default_value)
            template_key = form_field.template_key or form_field.id

            if form_field.field_type == FieldType.SWITCH:
                # For switches, add the flag when ON, or off_value when OFF
                if value:
                    # Replace placeholder with flag, or append if no placeholder
                    if f"{{{template_key}}}" in command:
                        command = command.replace(f"{{{template_key}}}", form_field.command_flag)
                    else:
                        command = f"{command} {form_field.command_flag}"
                else:
                    # Replace placeholder with off_value (can be empty)
                    if f"{{{template_key}}}" in command:
                        command = command.replace(f"{{{template_key}}}", form_field.off_value)
                    elif form_field.off_value:
                        command = f"{command} {form_field.off_value}"
            else:
                # For other fields, substitute the value
                if f"{{{template_key}}}" in command:
                    command = command.replace(f"{{{template_key}}}", str(value) if value else "")

        # Clean up multiple spaces
        command = " ".join(command.split())
        return command


def generate_id() -> str:
    """Generate a unique ID for a command button."""
    return str(uuid.uuid4())[:8]


# Built-in example commands
def get_builtin_commands() -> List[CommandButton]:
    """
    Returns the built-in example commands.
    These demonstrate the different features of the Command Manager.
    """
    return [
        # Example 1: find command with form dialog
        CommandButton(
            id="builtin_find",
            name=_("Find Files"),
            description=_("Search for files and directories with various filters"),
            command_template="find {path} {name_flag} {recursive_flag} {type_flag} {size_flag} {date_flag} {grep_flag}",
            icon_name="system-search-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            is_builtin=True,
            category=_("File Operations"),
            sort_order=1,
            form_fields=[
                CommandFormField(
                    id="path",
                    label=_("Search Path"),
                    field_type=FieldType.DIRECTORY_PATH,
                    default_value=".",
                    placeholder=_("Directory to search in"),
                    tooltip=_("Starting directory for the search"),
                    required=True,
                    template_key="path",
                ),
                CommandFormField(
                    id="name_pattern",
                    label=_("File Name Pattern"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("e.g., *.txt or report*"),
                    tooltip=_("Pattern to match file names (supports wildcards)"),
                    template_key="name_flag",
                ),
                CommandFormField(
                    id="recursive",
                    label=_("Recursive Search"),
                    field_type=FieldType.SWITCH,
                    default_value=True,
                    tooltip=_("Search in subdirectories"),
                    command_flag="-maxdepth 1",
                    template_key="recursive_flag",
                ),
                CommandFormField(
                    id="file_type",
                    label=_("File Type"),
                    field_type=FieldType.DROPDOWN,
                    default_value="",
                    options=[
                        ("", _("Any")),
                        ("-type f", _("Files only")),
                        ("-type d", _("Directories only")),
                        ("-type l", _("Symbolic links")),
                    ],
                    tooltip=_("Filter by file type"),
                    template_key="type_flag",
                ),
                CommandFormField(
                    id="size_filter",
                    label=_("Size Filter"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("e.g., +100M or -1k"),
                    tooltip=_("Filter by size (+100M = larger than 100MB, -1k = smaller than 1KB)"),
                    template_key="size_flag",
                ),
                CommandFormField(
                    id="date_value",
                    label=_("Modified in Last N"),
                    field_type=FieldType.NUMBER,
                    default_value="",
                    placeholder=_("e.g., 7"),
                    tooltip=_("Find files modified in the last N time units (leave empty to skip)"),
                    template_key="date_value",
                    min_value=1,
                ),
                CommandFormField(
                    id="date_unit",
                    label=_("Time Unit"),
                    field_type=FieldType.DROPDOWN,
                    default_value="days",
                    options=[
                        ("minutes", _("Minutes")),
                        ("hours", _("Hours")),
                        ("days", _("Days")),
                    ],
                    tooltip=_("Time unit for the modified filter"),
                    template_key="date_unit",
                ),
                CommandFormField(
                    id="grep_pattern",
                    label=_("Search in File Content"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("Text to search for inside files"),
                    tooltip=_("Use grep to search within file contents"),
                    template_key="grep_flag",
                ),
            ],
        ),
        # Example 2: Simple ls command with insert and execute
        CommandButton(
            id="builtin_ls",
            name=_("List Files"),
            description=_("List directory contents with details"),
            command_template="ls -lah ",
            icon_name="folder-open-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.INSERT_AND_EXECUTE,
            cursor_position=0,  # Cursor at end so user can add path
            is_builtin=True,
            category=_("File Operations"),
            sort_order=2,
        ),
        # Compress - create archives
        CommandButton(
            id="builtin_compress",
            name=_("Compress"),
            description=_("Create compressed archives from files and folders"),
            command_template="tar -cJvf {output} {input}",
            icon_name="package-x-generic-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            is_builtin=True,
            category=_("File Operations"),
            sort_order=3,
            form_fields=[
                CommandFormField(
                    id="format",
                    label=_("Format"),
                    field_type=FieldType.DROPDOWN,
                    default_value="tar.xz",
                    options=[
                        ("tar.xz", _("tar.xz (xz)")),
                        ("tar.gz", _("tar.gz (gzip)")),
                        ("tar.bz2", _("tar.bz2 (bzip2)")),
                        ("tar.zst", _("tar.zst (zstd)")),
                        ("tar.lzma", _("tar.lzma (lzma)")),
                        ("tar", _("tar (no compression)")),
                        ("zip", _("zip")),
                    ],
                    tooltip=_("Archive format to use"),
                    template_key="format",
                ),
                CommandFormField(
                    id="input",
                    label=_("Files/Folders"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("file1 file2 folder/"),
                    tooltip=_("Files and folders to compress"),
                    required=True,
                    template_key="input",
                ),
                CommandFormField(
                    id="output",
                    label=_("Archive Name"),
                    field_type=FieldType.TEXT,
                    default_value="archive.tar.xz",
                    placeholder=_("archive.tar.xz"),
                    tooltip=_("Name of the archive file to create"),
                    template_key="output",
                ),
            ],
        ),
        # Extract - extract archives
        CommandButton(
            id="builtin_extract",
            name=_("Extract"),
            description=_("Extract files from compressed archives"),
            command_template="tar -xJvf {input}",
            icon_name="extract-archive-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            is_builtin=True,
            category=_("File Operations"),
            sort_order=4,
            form_fields=[
                CommandFormField(
                    id="input",
                    label=_("Archive File"),
                    field_type=FieldType.FILE_PATH,
                    default_value="",
                    placeholder=_("archive.tar.xz"),
                    tooltip=_("Archive file to extract"),
                    required=True,
                    template_key="input",
                ),
                CommandFormField(
                    id="output",
                    label=_("Destination"),
                    field_type=FieldType.DIRECTORY_PATH,
                    default_value="",
                    placeholder=_("Leave empty for current directory"),
                    tooltip=_("Destination directory (optional)"),
                    template_key="output",
                ),
            ],
        ),
        # Systemctl - system service management
        CommandButton(
            id="builtin_systemctl",
            name=_("Systemctl"),
            description=_("Manage system services with systemctl"),
            command_template="systemctl {action} {service}",
            icon_name="system-run-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            is_builtin=True,
            category=_("System"),
            sort_order=1,
            form_fields=[
                CommandFormField(
                    id="action",
                    label=_("Action"),
                    field_type=FieldType.DROPDOWN,
                    default_value="status",
                    options=[
                        ("status", _("Status")),
                        ("start", _("Start")),
                        ("stop", _("Stop")),
                        ("restart", _("Restart")),
                        ("enable", _("Enable")),
                        ("disable", _("Disable")),
                        ("is-active", _("Is Active")),
                        ("is-enabled", _("Is Enabled")),
                        ("list-units --type=service", _("List Services")),
                        ("list-units --type=service --state=running", _("List Running")),
                        ("list-units --type=service --state=failed", _("List Failed")),
                    ],
                    tooltip=_("Action to perform on the service"),
                    template_key="action",
                ),
                CommandFormField(
                    id="service",
                    label=_("Service Name"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("e.g., nginx, sshd, docker"),
                    tooltip=_("Name of the service (leave empty for list actions)"),
                    template_key="service",
                ),
                CommandFormField(
                    id="user_scope",
                    label=_("User Scope"),
                    field_type=FieldType.SWITCH,
                    default_value=False,
                    tooltip=_("Operate on user services (--user flag)"),
                    command_flag="--user",
                    template_key="user_flag",
                ),
            ],
        ),
        # Journalctl - system journal viewer
        CommandButton(
            id="builtin_journalctl",
            name=_("Journalctl"),
            description=_("View system logs with journalctl"),
            command_template="journalctl {unit_flag} {follow_flag} {lines_flag} {priority_flag} {since_flag}",
            icon_name="document-open-recent-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            is_builtin=True,
            category=_("System"),
            sort_order=2,
            form_fields=[
                CommandFormField(
                    id="unit",
                    label=_("Unit/Service"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("e.g., nginx, sshd (optional)"),
                    tooltip=_("Filter logs by service unit name"),
                    template_key="unit_flag",
                ),
                CommandFormField(
                    id="follow",
                    label=_("Follow (live)"),
                    field_type=FieldType.SWITCH,
                    default_value=False,
                    tooltip=_("Follow new log entries in real-time"),
                    command_flag="-f",
                    template_key="follow_flag",
                ),
                CommandFormField(
                    id="lines",
                    label=_("Number of Lines"),
                    field_type=FieldType.NUMBER,
                    default_value="",
                    placeholder=_("e.g., 100 (empty for all)"),
                    tooltip=_("Show last N lines (leave empty for all)"),
                    template_key="lines_flag",
                    min_value=1,
                ),
                CommandFormField(
                    id="priority",
                    label=_("Priority"),
                    field_type=FieldType.DROPDOWN,
                    default_value="",
                    options=[
                        ("", _("All")),
                        ("-p 0", _("Emergency (0)")),
                        ("-p 1", _("Alert (1)")),
                        ("-p 2", _("Critical (2)")),
                        ("-p 3", _("Error (3)")),
                        ("-p 4", _("Warning (4)")),
                        ("-p 5", _("Notice (5)")),
                        ("-p 6", _("Info (6)")),
                        ("-p 7", _("Debug (7)")),
                    ],
                    tooltip=_("Filter by log priority level"),
                    template_key="priority_flag",
                ),
                CommandFormField(
                    id="since",
                    label=_("Since"),
                    field_type=FieldType.DROPDOWN,
                    default_value="",
                    options=[
                        ("", _("All time")),
                        ("--since today", _("Today")),
                        ("--since yesterday", _("Since Yesterday")),
                        ("--since '1 hour ago'", _("Last Hour")),
                        ("--since '24 hours ago'", _("Last 24 Hours")),
                        ("--since '7 days ago'", _("Last 7 Days")),
                        ("-b", _("Current Boot")),
                        ("-b -1", _("Previous Boot")),
                    ],
                    tooltip=_("Filter logs by time period"),
                    template_key="since_flag",
                ),
            ],
        ),
        # Pacman - Arch Linux package manager
        CommandButton(
            id="builtin_pacman",
            name=_("Pacman"),
            description=_("Arch Linux package manager operations"),
            command_template="sudo pacman {action} {package}",
            icon_name="system-software-install-symbolic",
            display_mode=DisplayMode.ICON_AND_TEXT,
            execution_mode=ExecutionMode.SHOW_DIALOG,
            is_builtin=True,
            category=_("System"),
            sort_order=3,
            form_fields=[
                CommandFormField(
                    id="action",
                    label=_("Action"),
                    field_type=FieldType.DROPDOWN,
                    default_value="-Sy",
                    options=[
                        ("-Sy", _("Install")),
                        ("-R", _("Remove")),
                        ("-Rs", _("Remove with Dependencies")),
                        ("-Rns", _("Remove All (configs too)")),
                        ("-Ss", _("Search")),
                        ("-Si", _("Package Info")),
                        ("-Qi", _("Local Package Info")),
                        ("-Ql", _("List Package Files")),
                        ("-Syu", _("System Update")),
                        ("-Syyu", _("Force Refresh & Update")),
                        ("-Sc", _("Clean Cache")),
                        ("-Scc", _("Clean All Cache")),
                        ("-Q", _("List Installed")),
                        ("-Qe", _("List Explicitly Installed")),
                        ("-Qdt", _("List Orphans")),
                    ],
                    tooltip=_("Package manager action to perform"),
                    template_key="action",
                ),
                CommandFormField(
                    id="package",
                    label=_("Package Name"),
                    field_type=FieldType.TEXT,
                    default_value="",
                    placeholder=_("e.g., firefox, vim (optional for updates)"),
                    tooltip=_("Package name for install/remove/search actions"),
                    template_key="package",
                ),
            ],
        ),
    ]


class CommandButtonManager:
    """
    Manages loading, saving, and accessing command buttons.
    Handles both built-in and user-defined commands, with support for
    customizing builtins and hiding commands.
    """

    _instance: Optional["CommandButtonManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.logger = get_logger("zashterminal.data.command_manager")
        self.config_paths = get_config_paths()
        self.custom_commands_file = self.config_paths.CONFIG_DIR / "command_buttons.json"
        self.customized_builtins_file = self.config_paths.CONFIG_DIR / "customized_builtins.json"
        self.hidden_commands_file = self.config_paths.CONFIG_DIR / "hidden_commands.json"
        self.command_prefs_file = self.config_paths.CONFIG_DIR / "command_prefs.json"
        self._data_lock = threading.RLock()

        self._builtin_commands: List[CommandButton] = []
        self._custom_commands: List[CommandButton] = []
        self._customized_builtins: Dict[str, dict] = {}  # id -> customized data
        self._hidden_command_ids: set = set()
        self._command_prefs: Dict[str, dict] = {}  # command_id -> preferences (e.g., send_to_all)

        self._load_builtin_commands()
        self._load_custom_commands()
        self._load_customized_builtins()
        self._load_hidden_commands()
        self._load_command_prefs()

        self._initialized = True

    def _load_builtin_commands(self):
        """Load the built-in example commands."""
        with self._data_lock:
            self._builtin_commands = get_builtin_commands()
            self.logger.info(f"Loaded {len(self._builtin_commands)} built-in commands.")

    def _load_custom_commands(self):
        """Load user-defined commands from file."""
        with self._data_lock:
            if not self.custom_commands_file.exists():
                self._custom_commands = []
                return

            try:
                with open(self.custom_commands_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._custom_commands = [
                        CommandButton.from_dict(cmd) for cmd in data
                    ]
                self.logger.info(f"Loaded {len(self._custom_commands)} custom commands.")
            except (json.JSONDecodeError, FileNotFoundError, KeyError) as e:
                self.logger.error(f"Failed to load custom commands: {e}")
                self._custom_commands = []

    def _load_customized_builtins(self):
        """Load customizations for built-in commands."""
        with self._data_lock:
            if not self.customized_builtins_file.exists():
                self._customized_builtins = {}
                return

            try:
                with open(self.customized_builtins_file, "r", encoding="utf-8") as f:
                    self._customized_builtins = json.load(f)
                self.logger.info(f"Loaded {len(self._customized_builtins)} customized builtins.")
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.logger.error(f"Failed to load customized builtins: {e}")
                self._customized_builtins = {}

    def _load_hidden_commands(self):
        """Load list of hidden command IDs."""
        with self._data_lock:
            if not self.hidden_commands_file.exists():
                self._hidden_command_ids = set()
                return

            try:
                with open(self.hidden_commands_file, "r", encoding="utf-8") as f:
                    self._hidden_command_ids = set(json.load(f))
                self.logger.info(f"Loaded {len(self._hidden_command_ids)} hidden commands.")
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.logger.error(f"Failed to load hidden commands: {e}")
                self._hidden_command_ids = set()

    def save_custom_commands(self):
        """Save user-defined commands to file."""
        with self._data_lock:
            try:
                # Ensure directory exists
                self.custom_commands_file.parent.mkdir(parents=True, exist_ok=True)

                data_to_save = [cmd.to_dict() for cmd in self._custom_commands]
                with open(self.custom_commands_file, "w", encoding="utf-8") as f:
                    json.dump(data_to_save, f, indent=2, ensure_ascii=False)
                self.logger.info("Custom commands saved successfully.")
            except Exception as e:
                self.logger.error(f"Failed to save custom commands: {e}")

    def _save_customized_builtins(self):
        """Save customized builtin commands."""
        with self._data_lock:
            try:
                self.customized_builtins_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.customized_builtins_file, "w", encoding="utf-8") as f:
                    json.dump(self._customized_builtins, f, indent=2, ensure_ascii=False)
                self.logger.info("Customized builtins saved successfully.")
            except Exception as e:
                self.logger.error(f"Failed to save customized builtins: {e}")

    def _save_hidden_commands(self):
        """Save hidden commands list."""
        with self._data_lock:
            try:
                self.hidden_commands_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.hidden_commands_file, "w", encoding="utf-8") as f:
                    json.dump(list(self._hidden_command_ids), f, indent=2)
                self.logger.info("Hidden commands saved successfully.")
            except Exception as e:
                self.logger.error(f"Failed to save hidden commands: {e}")

    def _load_command_prefs(self):
        """Load per-command preferences (e.g., send_to_all)."""
        with self._data_lock:
            if not self.command_prefs_file.exists():
                self._command_prefs = {}
                return

            try:
                with open(self.command_prefs_file, "r", encoding="utf-8") as f:
                    self._command_prefs = json.load(f)
                self.logger.info(f"Loaded command preferences for {len(self._command_prefs)} commands.")
            except (json.JSONDecodeError, FileNotFoundError) as e:
                self.logger.error(f"Failed to load command prefs: {e}")
                self._command_prefs = {}

    def _save_command_prefs(self):
        """Save per-command preferences."""
        with self._data_lock:
            try:
                self.command_prefs_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.command_prefs_file, "w", encoding="utf-8") as f:
                    json.dump(self._command_prefs, f, indent=2, ensure_ascii=False)
                self.logger.info("Command preferences saved successfully.")
            except Exception as e:
                self.logger.error(f"Failed to save command prefs: {e}")

    def get_command_pref(self, command_id: str, pref_key: str, default=None):
        """Get a preference value for a command."""
        with self._data_lock:
            return self._command_prefs.get(command_id, {}).get(pref_key, default)

    def set_command_pref(self, command_id: str, pref_key: str, value):
        """Set a preference value for a command and save."""
        with self._data_lock:
            if command_id not in self._command_prefs:
                self._command_prefs[command_id] = {}
            self._command_prefs[command_id][pref_key] = value
            self._save_command_prefs()

    def get_all_commands(self) -> List[CommandButton]:
        """Get all commands (built-in with customizations applied, and custom)."""
        with self._data_lock:
            result = []

            # Add built-in commands (with customizations applied)
            for cmd in self._builtin_commands:
                if cmd.id in self._customized_builtins:
                    # Apply customizations
                    customized = CommandButton.from_dict(self._customized_builtins[cmd.id])
                    customized.is_builtin = True  # Keep it marked as builtin
                    result.append(customized)
                else:
                    result.append(cmd)

            # Add custom commands
            result.extend(self._custom_commands)

            return result

    def get_builtin_commands(self) -> List[CommandButton]:
        """Get only built-in commands (with customizations applied)."""
        with self._data_lock:
            result = []
            for cmd in self._builtin_commands:
                if cmd.id in self._customized_builtins:
                    customized = CommandButton.from_dict(self._customized_builtins[cmd.id])
                    customized.is_builtin = True
                    result.append(customized)
                else:
                    result.append(cmd)
            return result

    def get_custom_commands(self) -> List[CommandButton]:
        """Get only custom commands."""
        with self._data_lock:
            return list(self._custom_commands)

    def get_command_by_id(self, command_id: str) -> Optional[CommandButton]:
        """Get a command by its ID (with customizations applied for builtins)."""
        with self._data_lock:
            # Check builtins first
            for cmd in self._builtin_commands:
                if cmd.id == command_id:
                    if command_id in self._customized_builtins:
                        customized = CommandButton.from_dict(self._customized_builtins[command_id])
                        customized.is_builtin = True
                        return customized
                    return cmd

            # Check custom commands
            for cmd in self._custom_commands:
                if cmd.id == command_id:
                    return cmd

            return None

    def get_categories(self) -> List[str]:
        """Get all unique categories."""
        with self._data_lock:
            categories = set()
            for cmd in self.get_all_commands():
                if cmd.category:
                    categories.add(cmd.category)
            return sorted(list(categories))

    def is_builtin_customized(self, command_id: str) -> bool:
        """Check if a builtin command has been customized."""
        with self._data_lock:
            return command_id in self._customized_builtins

    def is_command_hidden(self, command_id: str) -> bool:
        """Check if a command is hidden."""
        with self._data_lock:
            return command_id in self._hidden_command_ids

    def hide_command(self, command_id: str):
        """Hide a command from the interface."""
        with self._data_lock:
            self._hidden_command_ids.add(command_id)
            self._save_hidden_commands()
            self.logger.info(f"Hidden command: {command_id}")

    def unhide_command(self, command_id: str):
        """Unhide a command."""
        with self._data_lock:
            self._hidden_command_ids.discard(command_id)
            self._save_hidden_commands()
            self.logger.info(f"Unhidden command: {command_id}")

    def get_hidden_command_ids(self) -> List[str]:
        """Get list of hidden command IDs."""
        with self._data_lock:
            return list(self._hidden_command_ids)

    def is_command_pinned(self, command_id: str) -> bool:
        """Check if a command is pinned to the toolbar."""
        return self.get_command_pref(command_id, "pinned", False)

    def pin_command(self, command_id: str):
        """Pin a command to the toolbar."""
        self.set_command_pref(command_id, "pinned", True)
        self.logger.info(f"Pinned command to toolbar: {command_id}")

    def unpin_command(self, command_id: str):
        """Unpin a command from the toolbar."""
        self.set_command_pref(command_id, "pinned", False)
        self.logger.info(f"Unpinned command from toolbar: {command_id}")

    def get_pinned_commands(self) -> List["CommandButton"]:
        """Get all commands that are pinned to the toolbar, in order."""
        with self._data_lock:
            pinned = []
            all_commands = self.get_all_commands()
            for cmd in all_commands:
                if self.is_command_pinned(cmd.id) and not self.is_command_hidden(cmd.id):
                    pinned.append(cmd)
            return pinned

    def add_custom_command(self, command: CommandButton):
        """Add a new custom command."""
        with self._data_lock:
            # Generate ID if not provided
            if not command.id:
                command.id = generate_id()
            command.is_builtin = False
            self._custom_commands.append(command)
            self.save_custom_commands()

    def update_command(self, command: CommandButton):
        """Update an existing command (custom or builtin customization)."""
        with self._data_lock:
            # Check if it's a builtin command
            builtin_ids = {cmd.id for cmd in self._builtin_commands}

            if command.id in builtin_ids:
                # Store customization for builtin
                self._customized_builtins[command.id] = command.to_dict()
                self._save_customized_builtins()
                self.logger.info(f"Saved customization for builtin: {command.id}")
                return

            # Update custom command
            for i, cmd in enumerate(self._custom_commands):
                if cmd.id == command.id:
                    self._custom_commands[i] = command
                    self.save_custom_commands()
                    return

            # If not found, add it as new custom command
            self.add_custom_command(command)

    def restore_builtin_default(self, command_id: str):
        """Restore a builtin command to its default configuration."""
        with self._data_lock:
            if command_id in self._customized_builtins:
                del self._customized_builtins[command_id]
                self._save_customized_builtins()
                self.logger.info(f"Restored default for builtin: {command_id}")

    def remove_command(self, command_id: str):
        """Remove a custom command by ID."""
        with self._data_lock:
            self._custom_commands = [
                cmd for cmd in self._custom_commands if cmd.id != command_id
            ]
            self.save_custom_commands()

    def reorder_commands(self, command_ids: List[str]):
        """Reorder custom commands based on the given ID order."""
        with self._data_lock:
            id_to_cmd = {cmd.id: cmd for cmd in self._custom_commands}
            reordered = []
            for cmd_id in command_ids:
                if cmd_id in id_to_cmd:
                    reordered.append(id_to_cmd[cmd_id])
            # Add any commands not in the list at the end
            for cmd in self._custom_commands:
                if cmd.id not in command_ids:
                    reordered.append(cmd)
            self._custom_commands = reordered
            self.save_custom_commands()


def get_command_button_manager() -> CommandButtonManager:
    """Get the singleton CommandButtonManager instance."""
    return CommandButtonManager()

