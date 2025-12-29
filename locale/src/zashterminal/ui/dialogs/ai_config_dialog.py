# zashterminal/ui/dialogs/ai_config_dialog.py

"""AI Assistant configuration dialog."""

import threading
from typing import List, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ...settings.manager import SettingsManager
from ...utils.logger import get_logger
from ...utils.translation_utils import _


class AIConfigDialog(Adw.PreferencesWindow):
    """Dialog for configuring AI assistant settings."""

    __gsignals__ = {
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    # Provider configurations
    PROVIDERS = [
        ("groq", "Groq", "https://api.groq.com/openai/v1"),
        ("gemini", "Gemini", "https://generativelanguage.googleapis.com"),
        ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
        ("local", "Local (Ollama/LM Studio)", "http://localhost:11434/v1"),
    ]

    DEFAULT_MODELS = {
        "groq": "llama-3.1-8b-instant",
        "gemini": "gemini-2.5-flash",
        "openrouter": "openrouter/polaris-alpha",
        "local": "llama3.2",
    }

    def __init__(self, parent_window, settings_manager: SettingsManager):
        super().__init__(
            title=_("Configure AI Assistant"),
            transient_for=parent_window,
            modal=True,
            default_width=750,
            default_height=600,
            search_enabled=False,
        )
        self.add_css_class("zashterminal-dialog")
        self.logger = get_logger("zashterminal.ui.dialogs.ai_config")
        self.settings_manager = settings_manager

        self._setup_ui()
        self.logger.info("AI config dialog initialized")

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        page = Adw.PreferencesPage(title=_("AI Assistant"))
        self.add(page)

        # Enable/Disable group - MUST be first element user sees
        enable_group = Adw.PreferencesGroup()
        page.add(enable_group)

        self.enable_switch = Adw.SwitchRow(
            title=_("Enable AI Assistant"),
            subtitle=_("Show the AI Assistant button in the header bar."),
        )
        self.enable_switch.set_active(
            self.settings_manager.get("ai_assistant_enabled", False)
        )
        self.enable_switch.connect("notify::active", self._on_enable_changed)
        enable_group.add(self.enable_switch)

        # Provider selection group
        provider_group = Adw.PreferencesGroup()
        page.add(provider_group)

        # Provider combo row
        self.provider_row = Adw.ComboRow(
            title=_("Provider"),
            subtitle=_("Choose between cloud providers or local models."),
        )
        provider_model = Gtk.StringList.new([label for _, label, _ in self.PROVIDERS])
        self.provider_row.set_model(provider_model)

        # Set current provider
        current_provider = self.settings_manager.get("ai_assistant_provider", "groq")
        provider_index = self._get_provider_index(current_provider)
        self.provider_row.set_selected(provider_index)
        self.provider_row.connect("notify::selected", self._on_provider_changed)
        provider_group.add(self.provider_row)

        # Base URL row (for local providers)
        self.base_url_row = Adw.EntryRow(
            title=_("Base URL"),
        )
        self.base_url_row.set_text(
            self.settings_manager.get("ai_local_base_url", "http://localhost:11434/v1")
        )
        self.base_url_row.connect("changed", self._on_base_url_changed)
        provider_group.add(self.base_url_row)

        # API Key group
        api_group = Adw.PreferencesGroup()
        page.add(api_group)

        # API Key row
        self.api_key_row = Adw.PasswordEntryRow(
            title=_("API Key"),
        )
        self.api_key_row.set_text(
            self.settings_manager.get("ai_assistant_api_key", "")
        )
        self.api_key_row.connect("changed", self._on_api_key_changed)
        api_group.add(self.api_key_row)

        # Model selection group
        model_group = Adw.PreferencesGroup()
        page.add(model_group)

        # Model entry row
        self.model_row = Adw.EntryRow(
            title=_("Model Identifier"),
        )
        self.model_row.set_text(
            self.settings_manager.get("ai_assistant_model", "")
        )
        self.model_row.connect("changed", self._on_model_changed)
        model_group.add(self.model_row)

        # Browse models button (for OpenRouter - opens searchable dialog)
        self.browse_models_row = Adw.ActionRow(
            title=_("Browse Available Models"),
            subtitle=_("Search and select from available OpenRouter models."),
        )
        self.browse_models_button = Gtk.Button(label=_("Browse Models"))
        self.browse_models_button.set_valign(Gtk.Align.CENTER)
        self.browse_models_button.connect("clicked", self._on_browse_models_clicked)
        self.browse_models_row.add_suffix(self.browse_models_button)
        self.browse_models_row.set_activatable_widget(self.browse_models_button)
        model_group.add(self.browse_models_row)

        # OpenRouter-specific settings group
        self.openrouter_group = Adw.PreferencesGroup(
            title=_("OpenRouter Settings"),
            description=_("Additional settings for OpenRouter API rankings."),
        )
        page.add(self.openrouter_group)

        # Site URL row
        self.site_url_row = Adw.EntryRow(
            title=_("Site URL (optional)"),
        )
        self.site_url_row.set_text(
            self.settings_manager.get("ai_openrouter_site_url", "")
        )
        self.site_url_row.connect("changed", self._on_site_url_changed)
        self.openrouter_group.add(self.site_url_row)

        # Site name row
        self.site_name_row = Adw.EntryRow(
            title=_("Site Name (optional)"),
        )
        self.site_name_row.set_text(
            self.settings_manager.get("ai_openrouter_site_name", "")
        )
        self.site_name_row.connect("changed", self._on_site_name_changed)
        self.openrouter_group.add(self.site_name_row)

        # Update UI based on current provider
        self._update_ui_for_provider(current_provider)

    def _get_provider_index(self, provider_id: str) -> int:
        """Get the index of a provider in the PROVIDERS list."""
        for i, (pid, _name, _desc) in enumerate(self.PROVIDERS):
            if pid == provider_id:
                return i
        return 0

    def _get_selected_provider_id(self) -> str:
        """Get the currently selected provider ID."""
        index = self.provider_row.get_selected()
        if 0 <= index < len(self.PROVIDERS):
            return self.PROVIDERS[index][0]
        return "groq"

    def _update_ui_for_provider(self, provider_id: str) -> None:
        """Update UI elements based on the selected provider."""
        is_local = provider_id == "local"
        is_openrouter = provider_id == "openrouter"

        # Show/hide base URL for local provider
        self.base_url_row.set_visible(is_local)

        # Show/hide API key (local may not need it)
        self.api_key_row.set_sensitive(not is_local or False)  # Local may or may not need API key

        # Show/hide browse models button (only for OpenRouter)
        self.browse_models_row.set_visible(is_openrouter)

        # Show/hide OpenRouter-specific settings
        self.openrouter_group.set_visible(is_openrouter)

        # Update model placeholder
        default_model = self.DEFAULT_MODELS.get(provider_id, "")
        self.model_row.set_text(
            self.settings_manager.get("ai_assistant_model", "") or default_model
        )

        # Update subtitles based on provider
        if provider_id == "groq":
            self.model_row.set_title(_("Model Identifier"))
            self.api_key_row.set_title(_("Groq API Key"))
        elif provider_id == "gemini":
            self.model_row.set_title(_("Model Identifier"))
            self.api_key_row.set_title(_("Google AI Studio API Key"))
        elif provider_id == "openrouter":
            self.model_row.set_title(_("Model Identifier"))
            self.api_key_row.set_title(_("OpenRouter API Key"))
        elif provider_id == "local":
            self.model_row.set_title(_("Model Name"))
            self.api_key_row.set_title(_("API Key (if required)"))

    def _on_provider_changed(self, combo_row, _param) -> None:
        """Handle provider selection change."""
        provider_id = self._get_selected_provider_id()
        self.settings_manager.set("ai_assistant_provider", provider_id)
        self._update_ui_for_provider(provider_id)
        self.emit("setting-changed", "ai_assistant_provider", provider_id)

    def _on_base_url_changed(self, entry_row) -> None:
        """Handle base URL change."""
        url = entry_row.get_text().strip()
        self.settings_manager.set("ai_local_base_url", url)
        self.emit("setting-changed", "ai_local_base_url", url)

    def _on_api_key_changed(self, entry_row) -> None:
        """Handle API key change."""
        key = entry_row.get_text().strip()
        self.settings_manager.set("ai_assistant_api_key", key)
        self.emit("setting-changed", "ai_assistant_api_key", key)

    def _on_model_changed(self, entry_row) -> None:
        """Handle model change."""
        model = entry_row.get_text().strip()
        self.settings_manager.set("ai_assistant_model", model)
        self.emit("setting-changed", "ai_assistant_model", model)

    def _on_site_url_changed(self, entry_row) -> None:
        """Handle site URL change."""
        url = entry_row.get_text().strip()
        self.settings_manager.set("ai_openrouter_site_url", url)
        self.emit("setting-changed", "ai_openrouter_site_url", url)

    def _on_site_name_changed(self, entry_row) -> None:
        """Handle site name change."""
        name = entry_row.get_text().strip()
        self.settings_manager.set("ai_openrouter_site_name", name)
        self.emit("setting-changed", "ai_openrouter_site_name", name)

    def _on_enable_changed(self, switch_row, _param) -> None:
        """Handle enable/disable change."""
        enabled = switch_row.get_active()
        self.settings_manager.set("ai_assistant_enabled", enabled)
        self.emit("setting-changed", "ai_assistant_enabled", enabled)

    def _on_browse_models_clicked(self, button) -> None:
        """Open a searchable dialog to browse and select OpenRouter models."""
        api_key = self.settings_manager.get("ai_assistant_api_key", "").strip()
        if not api_key:
            self._show_toast(_("Please enter an API key first."))
            return

        # Open the model browser dialog
        dialog = OpenRouterModelBrowserDialog(self, api_key)
        dialog.connect("model-selected", self._on_model_browser_selected)
        dialog.present()

    def _on_model_browser_selected(self, dialog, model_id: str) -> None:
        """Handle model selection from the browser dialog."""
        self.model_row.set_text(model_id)
        dialog.close()

    def _show_toast(self, message: str) -> None:
        """Show a toast notification."""
        toast = Adw.Toast(title=message)
        self.add_toast(toast)


class OpenRouterModelBrowserDialog(Adw.Window):
    """Searchable dialog for browsing and selecting OpenRouter models."""

    __gsignals__ = {
        "model-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent_window, api_key: str):
        super().__init__(
            title=_("Select OpenRouter Model"),
            transient_for=parent_window,
            modal=True,
            default_width=800,
            default_height=600,
        )
        self.add_css_class("zashterminal-dialog")
        self.api_key = api_key
        self.logger = get_logger("zashterminal.ui.dialogs.model_browser")
        self._all_models: List[Tuple[str, str]] = []
        self._filtered_models: List[Tuple[str, str]] = []
        self._fetching = False

        self._setup_ui()
        self._fetch_models()

    def _setup_ui(self) -> None:
        """Set up the dialog UI."""
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        # Header bar
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)

        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)

        toolbar_view.add_top_bar(header)

        # Main content
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        toolbar_view.set_content(main_box)

        # Search entry
        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search models by name or ID...")
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        main_box.append(self.search_entry)

        # Status label / spinner
        self.status_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            margin_top=8,
            margin_bottom=8,
        )
        self.spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(label=_("Loading models..."))
        self.status_box.append(self.spinner)
        self.status_box.append(self.status_label)
        main_box.append(self.status_box)

        # Results count label
        self.count_label = Gtk.Label(
            label="",
            css_classes=["dim-label"],
            halign=Gtk.Align.START,
        )
        main_box.append(self.count_label)

        # Scrolled window with model list
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("card")
        main_box.append(scrolled)

        # Model list
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("boxed-list")
        self.list_box.connect("row-activated", self._on_row_activated)
        scrolled.set_child(self.list_box)

    def _fetch_models(self) -> None:
        """Fetch models from OpenRouter API."""
        if self._fetching:
            return

        self._fetching = True
        self.spinner.start()
        self.status_box.set_visible(True)
        self.count_label.set_visible(False)

        thread = threading.Thread(
            target=self._fetch_models_thread,
            daemon=True,
        )
        thread.start()

    def _fetch_models_thread(self) -> None:
        """Fetch models in a background thread."""
        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            response = requests.get(
                "https://openrouter.ai/api/v1/models",
                headers=headers,
                timeout=30,
            )

            if response.status_code >= 400:
                GLib.idle_add(self._on_fetch_error, f"HTTP {response.status_code}")
                return

            data = response.json()
            models = data.get("data", [])

            # Extract model id and name
            model_list = []
            for model in models:
                model_id = model.get("id", "")
                model_name = model.get("name", model_id)
                if model_id:
                    model_list.append((model_id, model_name))

            # Sort by name
            model_list.sort(key=lambda x: x[1].lower())

            GLib.idle_add(self._on_fetch_success, model_list)

        except Exception as e:
            GLib.idle_add(self._on_fetch_error, str(e))

    def _on_fetch_success(self, models: List[Tuple[str, str]]) -> None:
        """Handle successful model fetch."""
        self._fetching = False
        self.spinner.stop()
        self.status_box.set_visible(False)

        self._all_models = models
        self._filtered_models = models
        self._update_model_list()

        # Focus search entry
        self.search_entry.grab_focus()

    def _on_fetch_error(self, error: str) -> None:
        """Handle fetch error."""
        self._fetching = False
        self.spinner.stop()
        self.status_label.set_text(
            _("Failed to load models: {error}").format(error=error)
        )
        self.status_label.add_css_class("error")

    def _on_search_changed(self, search_entry) -> None:
        """Filter models based on search text."""
        search_text = search_entry.get_text().lower().strip()

        if not search_text:
            self._filtered_models = self._all_models
        else:
            self._filtered_models = [
                (mid, name)
                for mid, name in self._all_models
                if search_text in mid.lower() or search_text in name.lower()
            ]

        self._update_model_list()

    def _update_model_list(self) -> None:
        """Update the model list display."""
        # Clear existing items
        while True:
            row = self.list_box.get_row_at_index(0)
            if row is None:
                break
            self.list_box.remove(row)

        # Update count label
        total = len(self._all_models)
        shown = len(self._filtered_models)
        if total == shown:
            self.count_label.set_text(_("{count} models available").format(count=total))
        else:
            self.count_label.set_text(
                _("Showing {shown} of {total} models").format(shown=shown, total=total)
            )
        self.count_label.set_visible(True)

        # Add filtered models (limit to first 100 for performance)
        for model_id, model_name in self._filtered_models[:100]:
            row = self._create_model_row(model_id, model_name)
            self.list_box.append(row)

        if len(self._filtered_models) > 100:
            hint_label = Gtk.Label(
                label=_("Showing first 100 results. Refine your search to see more."),
                css_classes=["dim-label"],
                margin_top=8,
                margin_bottom=8,
            )
            hint_row = Gtk.ListBoxRow(child=hint_label, selectable=False)
            self.list_box.append(hint_row)

    def _create_model_row(self, model_id: str, model_name: str) -> Gtk.ListBoxRow:
        """Create a row for a model."""
        row = Gtk.ListBoxRow()
        row.model_id = model_id  # Store ID for later retrieval

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )

        # Model name (prominent)
        name_label = Gtk.Label(
            label=model_name,
            xalign=0,
            css_classes=["heading"],
            wrap=True,
            wrap_mode=2,  # WORD_CHAR
        )
        box.append(name_label)

        # Model ID (smaller, dim)
        id_label = Gtk.Label(
            label=model_id,
            xalign=0,
            css_classes=["dim-label", "caption"],
            selectable=True,
        )
        box.append(id_label)

        row.set_child(box)
        return row

    def _on_row_activated(self, list_box, row) -> None:
        """Handle row activation (selection)."""
        if hasattr(row, "model_id"):
            self.emit("model-selected", row.model_id)
