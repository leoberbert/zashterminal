# zashterminal/ui/dialogs/ai_config_dialog.py

"""AI Assistant configuration dialog."""

import threading
from typing import List, Tuple

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk

from ...settings.manager import SettingsManager
from ...terminal import ai_providers
from ...terminal.ai_providers import (
    API_ANTHROPIC,
    API_OPENAI,
    CUSTOM_ID,
    LOCAL_ID,
    PROVIDERS,
    provider_label,
)
from ...utils.logger import get_logger
from ...utils.translation_utils import _
from .base_dialog import close_on_escape


class AIConfigDialog(Adw.PreferencesWindow):
    """Dialog for configuring AI assistant settings."""

    __gsignals__ = {
        "setting-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
    }

    # Wire formats offered for the "Other (custom)" provider
    API_FORMATS = (API_OPENAI, API_ANTHROPIC)

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
        # True while the dialog fills fields itself (not user edits)
        self._loading = False

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

        self.provider_row = Adw.ComboRow(
            title=_("Provider"),
            subtitle=_("Choose a cloud provider, a local server or your own endpoint."),
        )
        self.provider_row.set_model(
            Gtk.StringList.new([provider_label(p) for p in PROVIDERS])
        )
        self.provider_row.set_enable_search(True)
        current_provider = self.settings_manager.get("ai_assistant_provider", "groq")
        self.provider_row.set_selected(self._get_provider_index(current_provider))
        self.provider_row.connect("notify::selected", self._on_provider_changed)
        provider_group.add(self.provider_row)

        # Base URL row (local server or custom endpoint)
        self.base_url_row = Adw.EntryRow(title=_("Base URL"))
        self.base_url_row.connect("changed", self._on_base_url_changed)
        provider_group.add(self.base_url_row)

        # API format (custom endpoint only)
        self.api_format_row = Adw.ComboRow(
            title=_("API Format"),
            subtitle=_("Request format: /chat/completions (OpenAI) or /messages (Anthropic)."),
        )
        self.api_format_row.set_model(
            Gtk.StringList.new(
                [
                    _("OpenAI-compatible"),
                    _("Anthropic-compatible"),
                ]
            )
        )
        current_format = self.settings_manager.get("ai_custom_api_format", API_OPENAI)
        self.api_format_row.set_selected(
            self.API_FORMATS.index(current_format)
            if current_format in self.API_FORMATS
            else 0
        )
        self.api_format_row.connect("notify::selected", self._on_api_format_changed)
        provider_group.add(self.api_format_row)

        # API Key group
        api_group = Adw.PreferencesGroup()
        page.add(api_group)

        self.api_key_row = Adw.PasswordEntryRow(title=_("API Key"))
        self.api_key_row.set_text(self.settings_manager.get("ai_assistant_api_key", ""))
        self.api_key_row.connect("changed", self._on_api_key_changed)
        api_group.add(self.api_key_row)

        # Model selection group
        model_group = Adw.PreferencesGroup()
        page.add(model_group)

        self.model_row = Adw.EntryRow(title=_("Model Identifier"))
        self.model_row.connect("changed", self._on_model_changed)
        model_group.add(self.model_row)

        # Browse models button (queries the provider's model list)
        self.browse_models_row = Adw.ActionRow(
            title=_("Browse Available Models"),
            subtitle=_("Search and select from the models offered by the provider."),
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

        self.site_url_row = Adw.EntryRow(title=_("Site URL (optional)"))
        self.site_url_row.set_text(self.settings_manager.get("ai_openrouter_site_url", ""))
        self.site_url_row.connect("changed", self._on_site_url_changed)
        self.openrouter_group.add(self.site_url_row)

        self.site_name_row = Adw.EntryRow(title=_("Site Name (optional)"))
        self.site_name_row.set_text(self.settings_manager.get("ai_openrouter_site_name", ""))
        self.site_name_row.connect("changed", self._on_site_name_changed)
        self.openrouter_group.add(self.site_name_row)

        self._update_ui_for_provider(current_provider, reset_model=False)

    def _get_provider_index(self, provider_id: str) -> int:
        """Get the index of a provider in PROVIDERS."""
        for i, provider in enumerate(PROVIDERS):
            if provider.id == provider_id:
                return i
        return 0

    def _get_selected_provider_id(self) -> str:
        """Get the currently selected provider ID."""
        index = self.provider_row.get_selected()
        if 0 <= index < len(PROVIDERS):
            return PROVIDERS[index].id
        return PROVIDERS[0].id

    def _update_ui_for_provider(self, provider_id: str, reset_model: bool) -> None:
        """Show the fields the selected provider needs."""
        provider = ai_providers.get_provider(provider_id) or PROVIDERS[0]
        is_local = provider.id == LOCAL_ID
        is_custom = provider.id == CUSTOM_ID

        self._loading = True
        if is_local:
            url = self.settings_manager.get("ai_local_base_url", "") or provider.base_url
        else:
            url = self.settings_manager.get("ai_custom_base_url", "")
        self.base_url_row.set_text(url)
        # Keys are stored per provider: show the one of the selected provider.
        self.api_key_row.set_text(self.settings_manager.get("ai_assistant_api_key", ""))
        if reset_model:
            # A model id only makes sense for the provider it belongs to.
            self.model_row.set_text(provider.default_model)
        else:
            self.model_row.set_text(
                self.settings_manager.get("ai_assistant_model", "")
                or provider.default_model
            )
        self._loading = False

        self.base_url_row.set_visible(is_local or is_custom)
        self.base_url_row.set_title(
            _("Base URL (e.g. https://api.example.com/v1)") if is_custom else _("Base URL")
        )
        self.api_format_row.set_visible(is_custom)
        self.openrouter_group.set_visible(provider.id == "openrouter")
        self.model_row.set_title(_("Model Name") if is_local else _("Model Identifier"))
        if provider.key_required:
            self.api_key_row.set_title(
                _("{provider} API Key").format(provider=provider_label(provider))
            )
        else:
            self.api_key_row.set_title(_("API Key (if required)"))

    def _on_provider_changed(self, combo_row, _param) -> None:
        """Handle provider selection change."""
        provider_id = self._get_selected_provider_id()
        self.settings_manager.set("ai_assistant_provider", provider_id)
        self._update_ui_for_provider(provider_id, reset_model=True)
        self._on_model_changed(self.model_row)
        self.emit("setting-changed", "ai_assistant_provider", provider_id)

    def _on_base_url_changed(self, entry_row) -> None:
        """Handle base URL change (local server or custom endpoint)."""
        if self._loading:
            return
        url = entry_row.get_text().strip()
        key = (
            "ai_local_base_url"
            if self._get_selected_provider_id() == LOCAL_ID
            else "ai_custom_base_url"
        )
        self.settings_manager.set(key, url)
        self.emit("setting-changed", key, url)

    def _on_api_format_changed(self, combo_row, _param) -> None:
        index = combo_row.get_selected()
        value = self.API_FORMATS[index] if 0 <= index < len(self.API_FORMATS) else API_OPENAI
        self.settings_manager.set("ai_custom_api_format", value)
        self.emit("setting-changed", "ai_custom_api_format", value)

    def _on_api_key_changed(self, entry_row) -> None:
        """Handle API key change (stored in libsecret via SettingsManager)."""
        if self._loading:
            return
        key = entry_row.get_text().strip()
        try:
            self.settings_manager.set("ai_assistant_api_key", key)
            self.emit("setting-changed", "ai_assistant_api_key", key)
        except Exception as e:
            self.logger.error(f"Failed to store AI API key: {e}")
            entry_row.add_css_class("error")
            toast = Adw.Toast(
                title=_("Could not store API key securely. Is the system keyring available?")
            )
            toast.set_timeout(5)
            self.add_toast(toast)

    def _on_model_changed(self, entry_row) -> None:
        """Handle model change."""
        if self._loading:
            return
        model = entry_row.get_text().strip()
        self.settings_manager.set("ai_assistant_model", model)
        self.emit("setting-changed", "ai_assistant_model", model)

    def _on_site_url_changed(self, entry_row) -> None:
        url = entry_row.get_text().strip()
        self.settings_manager.set("ai_openrouter_site_url", url)
        self.emit("setting-changed", "ai_openrouter_site_url", url)

    def _on_site_name_changed(self, entry_row) -> None:
        name = entry_row.get_text().strip()
        self.settings_manager.set("ai_openrouter_site_name", name)
        self.emit("setting-changed", "ai_openrouter_site_name", name)

    def _on_enable_changed(self, switch_row, _param) -> None:
        enabled = switch_row.get_active()
        self.settings_manager.set("ai_assistant_enabled", enabled)
        self.emit("setting-changed", "ai_assistant_enabled", enabled)

    def _on_browse_models_clicked(self, button) -> None:
        """Open a searchable dialog with the provider's models."""
        try:
            config = ai_providers.build_config(self.settings_manager.get)
        except RuntimeError as exc:
            self._show_toast(str(exc))
            return
        if config.provider.key_required and not config.api_key:
            self._show_toast(_("Please enter an API key first."))
            return
        if not config.base_url:
            self._show_toast(_("Configure the base URL in the AI Assistant settings."))
            return
        dialog = ModelBrowserDialog(self, config)
        dialog.connect("model-selected", self._on_model_browser_selected)
        dialog.present()

    def _on_model_browser_selected(self, dialog, model_id: str) -> None:
        self.model_row.set_text(model_id)
        dialog.close()

    def _show_toast(self, message: str) -> None:
        self.add_toast(Adw.Toast(title=message))


class ModelBrowserDialog(Adw.Window):
    """Searchable dialog for browsing and selecting a provider's models."""

    __gsignals__ = {
        "model-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, parent_window, config: "ai_providers.ProviderConfig"):
        super().__init__(
            title=_("Select {provider} Model").format(
                provider=provider_label(config.provider)
            ),
            transient_for=parent_window,
            modal=True,
            default_width=800,
            default_height=600,
        )
        close_on_escape(self)
        self.add_css_class("zashterminal-dialog")
        self.config = config
        self.logger = get_logger("zashterminal.ui.dialogs.model_browser")
        self._all_models: List[Tuple[str, str]] = []
        self._filtered_models: List[Tuple[str, str]] = []
        self._fetching = False

        self._setup_ui()
        self._fetch_models()

    def _setup_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)
        header.set_show_start_title_buttons(False)
        cancel_button = Gtk.Button(label=_("Cancel"))
        cancel_button.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_button)
        toolbar_view.add_top_bar(header)

        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        toolbar_view.set_content(main_box)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text=_("Search models by name or ID...")
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        main_box.append(self.search_entry)

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

        self.count_label = Gtk.Label(label="", css_classes=["dim-label"], halign=Gtk.Align.START)
        main_box.append(self.count_label)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add_css_class("card")
        main_box.append(scrolled)

        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("boxed-list")
        self.list_box.connect("row-activated", self._on_row_activated)
        scrolled.set_child(self.list_box)

    def _fetch_models(self) -> None:
        if self._fetching:
            return
        self._fetching = True
        self.spinner.start()
        self.status_box.set_visible(True)
        self.count_label.set_visible(False)
        threading.Thread(target=self._fetch_models_thread, daemon=True).start()

    def _fetch_models_thread(self) -> None:
        try:
            models = ai_providers.list_models(self.config)
            GLib.idle_add(self._on_fetch_success, models)
        except Exception as e:
            GLib.idle_add(self._on_fetch_error, str(e))

    def _on_fetch_success(self, models: List[Tuple[str, str]]) -> None:
        self._fetching = False
        self.spinner.stop()
        self.status_box.set_visible(False)
        self._all_models = models
        self._filtered_models = models
        self._update_model_list()
        self.search_entry.grab_focus()

    def _on_fetch_error(self, error: str) -> None:
        self._fetching = False
        self.spinner.stop()
        self.status_label.set_text(_("Failed to load models: {error}").format(error=error))
        self.status_label.add_css_class("error")

    def _on_search_changed(self, search_entry) -> None:
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
        while (row := self.list_box.get_row_at_index(0)) is not None:
            self.list_box.remove(row)

        total = len(self._all_models)
        shown = len(self._filtered_models)
        if total == shown:
            self.count_label.set_text(_("{count} models available").format(count=total))
        else:
            self.count_label.set_text(
                _("Showing {shown} of {total} models").format(shown=shown, total=total)
            )
        self.count_label.set_visible(True)

        # Limit to the first 100 rows for performance
        for model_id, model_name in self._filtered_models[:100]:
            self.list_box.append(self._create_model_row(model_id, model_name))

        if len(self._filtered_models) > 100:
            hint_label = Gtk.Label(
                label=_("Showing first 100 results. Refine your search to see more."),
                css_classes=["dim-label"],
                margin_top=8,
                margin_bottom=8,
            )
            self.list_box.append(Gtk.ListBoxRow(child=hint_label, selectable=False))

    def _create_model_row(self, model_id: str, model_name: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.model_id = model_id
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=8,
            margin_bottom=8,
            margin_start=12,
            margin_end=12,
        )
        box.append(
            Gtk.Label(
                label=model_name,
                xalign=0,
                css_classes=["heading"],
                wrap=True,
                wrap_mode=2,  # WORD_CHAR
            )
        )
        if model_name != model_id:
            box.append(
                Gtk.Label(
                    label=model_id,
                    xalign=0,
                    css_classes=["dim-label", "caption"],
                    selectable=True,
                )
            )
        row.set_child(box)
        return row

    def _on_row_activated(self, list_box, row) -> None:
        if hasattr(row, "model_id"):
            self.emit("model-selected", row.model_id)
