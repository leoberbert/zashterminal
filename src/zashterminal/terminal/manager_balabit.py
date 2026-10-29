# zashterminal/terminal/manager_balabit.py
"""Balabit / One Identity gateway keyboard-interactive auth helpers."""

from typing import Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk, Vte

from ..sessions.models import SessionItem
from ..utils.translation_utils import _


class TerminalBalabitMixin:
    def _on_terminal_contents_changed_for_gateway_auth(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """Detect Balabit keyboard-interactive banner and offer a credential dialog."""
        info = self.registry.get_terminal_info(terminal_id)
        if not info or info.get("type") != "ssh":
            return

        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            if col_count <= 0 or row_count <= 0:
                return
            start_row = max(0, row_count - 60)
            result = terminal.get_text_range_format(
                Vte.Format.TEXT,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if not result or not result[0]:
                return
            text_lower = result[0].lower()
            recent_lines = [
                line.strip().lower() for line in result[0].splitlines() if line.strip()
            ]
            last_prompt_line = recent_lines[-1] if recent_lines else ""
        except Exception:
            return

        # If we have a pending sequence, try to advance it based on visible prompts.
        if terminal_id in self._balabit_gateway_pending_auth:
            self._advance_balabit_gateway_auth_sequence(terminal, terminal_id)

        # Require the banner to be visible and the terminal to currently look like
        # it is waiting for gateway interactive input. This avoids re-triggering
        # from stale banner text left in scrollback after successful login.
        has_gateway_banner = (
            "gateway authentication and authorization" in text_lower
            and "please specify the requested information" in text_lower
        )

        # If banner reappears after a submitted attempt, consider the previous
        # auth sequence stale and re-open dialog (e.g. wrong password/OTP).
        if has_gateway_banner and terminal_id in self._balabit_gateway_prompt_submitted:
            if (
                "please specify the requested information" in last_prompt_line
                or "gateway authentication and authorization" in last_prompt_line
            ):
                self._balabit_gateway_pending_auth.pop(terminal_id, None)
                self._balabit_gateway_prompt_shown.discard(terminal_id)
                self._balabit_gateway_prompt_submitted.discard(terminal_id)

        looks_like_gateway_prompt = (
            "please specify the requested information" in last_prompt_line
            or "gateway user" in last_prompt_line
            or "gateway username" in last_prompt_line
            or "gateway password:" in last_prompt_line
        )
        if not has_gateway_banner or not looks_like_gateway_prompt:
            return

        if terminal_id in self._balabit_gateway_prompt_shown:
            # Allow showing the dialog again when a previous submission is done and
            # the gateway has returned to the auth banner (e.g. wrong password/retry).
            if (
                terminal_id in self._balabit_gateway_prompt_submitted
                and terminal_id not in self._balabit_gateway_pending_auth
            ):
                self._balabit_gateway_prompt_shown.discard(terminal_id)
                self._balabit_gateway_prompt_submitted.discard(terminal_id)
            else:
                return

        self._balabit_gateway_prompt_shown.add(terminal_id)
        GLib.idle_add(self._show_balabit_gateway_auth_dialog, terminal, terminal_id)

    def _advance_balabit_gateway_auth_sequence(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> None:
        """Send pending Balabit auth responses only when the corresponding prompt appears."""
        pending = self._balabit_gateway_pending_auth.get(terminal_id)
        if not pending:
            return

        try:
            col_count = terminal.get_column_count()
            row_count = terminal.get_row_count()
            if col_count <= 0 or row_count <= 0:
                return
            start_row = max(0, row_count - 30)
            result = terminal.get_text_range_format(
                Vte.Format.TEXT,
                start_row,
                0,
                row_count - 1,
                col_count - 1,
            )
            if not result or not result[0]:
                return
            recent_text = result[0]
            recent_lower = recent_text.lower()
            recent_lines = [line.strip().lower() for line in recent_text.splitlines() if line.strip()]
            last_prompt_line = recent_lines[-1] if recent_lines else ""
        except Exception:
            return
        # Step 1: Gateway username prompt
        if (
            pending.get("gateway_username_pending")
            and "gateway username:" in last_prompt_line
        ):
            self._send_text_to_terminal(
                terminal, pending.get("gateway_username", ""), terminal_id, "gateway_username"
            )
            pending["gateway_username_pending"] = ""
            return
        # Step 2: Gateway password prompt
        if (
            pending.get("gateway_password_pending")
            and "gateway password:" in last_prompt_line
        ):
            self._send_text_to_terminal(
                terminal, pending.get("gateway_password", ""), terminal_id, "gateway_password"
            )
            pending["gateway_password_pending"] = ""
            return

        # Step 3: Destination/server password prompt
        # Only inspect the latest visible non-empty prompt line; scanning the whole
        # buffer causes false positives while Balabit still shows/repeats "Gateway password:"
        # (especially with MFA/token approval delays).
        has_target_password_prompt = (
            "'s password:" in last_prompt_line
            or (
                last_prompt_line.endswith("password:")
                and "gateway password:" not in last_prompt_line
            )
        )
        if pending.get("target_password_pending") and has_target_password_prompt:
            # Defensive guard: still on the gateway prompt (Balabit may redraw it while waiting for MFA).
            if "gateway password:" in last_prompt_line:
                return
            if not (pending.get("target_password") or ""):
                if not pending.get("target_password_prompt_open"):
                    pending["target_password_prompt_open"] = "1"
                    GLib.idle_add(
                        self._show_balabit_target_password_dialog,
                        terminal,
                        terminal_id,
                    )
                return
            self._send_text_to_terminal(
                terminal, pending.get("target_password", ""), terminal_id, "target_password"
            )
            pending["target_password_pending"] = ""

        # Cleanup once all deferred steps are done.
        if not pending.get("gateway_password_pending") and not pending.get("target_password_pending"):
            self._balabit_gateway_pending_auth.pop(terminal_id, None)

    def _retry_balabit_gateway_auth_sequence(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        """Periodically retry gateway auth progression for prompts that are not rendered reliably."""
        pending = self._balabit_gateway_pending_auth.get(terminal_id)
        if not pending:
            return False

        self._advance_balabit_gateway_auth_sequence(terminal, terminal_id)
        pending = self._balabit_gateway_pending_auth.get(terminal_id)
        if not pending:
            return False

        retries_left = int(pending.get("gateway_retry_left", "0") or "0")
        if retries_left <= 0:
            if pending.get("gateway_password_pending"):
                self.logger.warning(
                    f"Gateway password prompt not detected for terminal {terminal_id}; sending fallback input."
                )
                self._send_text_to_terminal(
                    terminal,
                    pending.get("gateway_password", ""),
                    terminal_id,
                    "gateway_password_fallback",
                )
                pending["gateway_password_pending"] = ""

            if (
                not pending.get("gateway_password_pending")
                and not pending.get("target_password_pending")
            ):
                self._balabit_gateway_pending_auth.pop(terminal_id, None)
            return False

        pending["gateway_retry_left"] = str(retries_left - 1)
        return True

    def _fallback_send_gateway_password(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        """
        Fallback for gateways that never render a visible password prompt.

        If the gateway password is still pending shortly after username submission,
        send it proactively once.
        """
        pending = self._balabit_gateway_pending_auth.get(terminal_id)
        if not pending:
            return False
        if pending.get("gateway_password_pending"):
            self.logger.info(
                f"Gateway password prompt not visible for terminal {terminal_id}; sending proactive fallback."
            )
            self._send_text_to_terminal(
                terminal,
                pending.get("gateway_password", ""),
                terminal_id,
                "gateway_password_proactive_fallback",
            )
            pending["gateway_password_pending"] = ""
            if not pending.get("target_password_pending"):
                self._balabit_gateway_pending_auth.pop(terminal_id, None)
        return False

    def _send_text_to_terminal(
        self, terminal: Vte.Terminal, value: str, terminal_id: int, label: str
    ) -> None:
        """Send a single line to the PTY."""
        payload = f"{(value or '').replace(chr(13), '')}\n".encode("utf-8")
        try:
            if hasattr(terminal, "feed_child_binary"):
                terminal.feed_child_binary(payload)
            else:
                terminal.feed_child(payload)
            self.logger.info(
                f"Sent Balabit auth step '{label}' for terminal {terminal_id}"
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to send Balabit auth step '{label}' for terminal {terminal_id}: {e}"
            )

    def _show_balabit_gateway_auth_dialog(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        """Show a local dialog and send Balabit gateway credentials to the PTY."""
        try:
            if terminal is None or terminal.get_parent() is None:
                return False
        except Exception:
            return False

        info = self.registry.get_terminal_info(terminal_id) or {}
        identifier = info.get("identifier")
        session = identifier if isinstance(identifier, SessionItem) else None

        dialog = Gtk.Dialog(
            title=_("Gateway Authentication"),
            transient_for=self.parent_window,
            modal=True,
            use_header_bar=True,
        )
        dialog.add_css_class("zashterminal-dialog")
        dialog.set_resizable(False)
        dialog.set_default_size(560, -1)
        dialog.set_default_size(520, -1)
        try:
            titlebar = dialog.get_titlebar()
            if titlebar:
                titlebar.add_css_class("main-header-bar")
        except Exception:
            pass
        cancel_btn = dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        send_btn = dialog.add_button(_("Send"), Gtk.ResponseType.OK)
        if cancel_btn:
            cancel_btn.add_css_class("flat")
        if send_btn:
            send_btn.add_css_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        description = Gtk.Label(
            label=_(
                "This SSH gateway requested keyboard-interactive authentication. "
                "Enter the gateway user and password to send them to the terminal session. "
                "If your gateway expects blank values, leave the fields empty and click Send."
            )
        )
        description.set_wrap(True)
        description.set_max_width_chars(64)
        description.set_xalign(0.0)
        description.set_max_width_chars(56)
        description.add_css_class("dim-label")
        content.append(description)

        form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        form_box.add_css_class("card")
        form_box.set_margin_top(4)
        form_box.set_margin_bottom(4)
        form_box.set_margin_start(2)
        form_box.set_margin_end(2)
        content.append(form_box)

        user_label = Gtk.Label(label=_("Gateway User"))
        user_label.set_xalign(0.0)
        user_label.add_css_class("caption")
        user_label.add_css_class("dim-label")
        form_box.append(user_label)
        user_entry = Gtk.Entry()
        if hasattr(user_entry, "set_activates_default"):
            user_entry.set_activates_default(True)
        user_entry.set_width_chars(36)
        form_box.append(user_entry)

        password_label = Gtk.Label(label=_("Gateway Password"))
        password_label.set_xalign(0.0)
        password_label.add_css_class("caption")
        password_label.add_css_class("dim-label")
        form_box.append(password_label)
        password_entry = Gtk.PasswordEntry()
        if hasattr(password_entry, "set_activates_default"):
            password_entry.set_activates_default(True)
        password_entry.set_width_chars(36)
        form_box.append(password_entry)

        def _submit_dialog_from_entry(_entry) -> None:
            try:
                dialog.response(Gtk.ResponseType.OK)
            except Exception:
                pass

        try:
            user_entry.connect("activate", _submit_dialog_from_entry)
        except Exception:
            pass
        try:
            password_entry.connect("activate", _submit_dialog_from_entry)
        except Exception:
            pass

        hint = Gtk.Label(
            label=_(
                "Some gateways hide the prompt text, so the terminal can appear stuck "
                "until the credentials are sent."
            )
        )
        hint.set_wrap(True)
        hint.set_max_width_chars(64)
        hint.set_xalign(0.0)
        hint.set_max_width_chars(56)
        hint.add_css_class("dim-label")
        content.append(hint)

        def on_response(dlg: Gtk.Dialog, response_id: int) -> None:
                    try:
                        if response_id == Gtk.ResponseType.OK:
                            gateway_user = user_entry.get_text().replace("\r", "")
                            gateway_password = password_entry.get_text().replace("\r", "")
                            target_password = ""
                            if (
                                session
                                and hasattr(session, "uses_password_auth")
                                and session.uses_password_auth()
                            ):
                                try:
                                    target_password = (session.auth_value or "").replace("\r", "")
                                except Exception:
                                    target_password = ""

                            # Modificação: Não envia nada cegamente. Apenas coloca na fila.
                            self._balabit_gateway_pending_auth[terminal_id] = {
                                "gateway_username": gateway_user,
                                "gateway_username_pending": "1", # Nova flag adicionada
                                "gateway_password": gateway_password,
                                "gateway_password_pending": "1",
                                "target_password": target_password,
                                "target_password_pending": "1" if target_password else "",
                                "gateway_retry_left": "25",
                            }
                            self._balabit_gateway_prompt_submitted.add(terminal_id)
                            
                            # Chama o avanço. Se o prompt já estiver na tela, ele envia.
                            self._advance_balabit_gateway_auth_sequence(terminal, terminal_id)
                            
                            # IMPORTANTE: Remova os GLib.timeout_add do _fallback_send_gateway_password 
                            # e do _retry_balabit_gateway_auth_sequence daqui, pois eles estragam o fluxo do MFA.
                    finally:
                        dlg.destroy()

        dialog.connect("response", on_response)
        dialog.present()
        user_entry.grab_focus()
        return False

    def _show_balabit_target_password_dialog(
        self, terminal: Vte.Terminal, terminal_id: int
    ) -> bool:
        """Ask for the destination SSH password when it is not available in the session store."""
        pending = self._balabit_gateway_pending_auth.get(terminal_id)
        if not pending:
            return False
        try:
            if terminal is None or terminal.get_parent() is None:
                pending["target_password_prompt_open"] = ""
                return False
        except Exception:
            pending["target_password_prompt_open"] = ""
            return False

        dialog = Gtk.Dialog(
            title=_("Server Password"),
            transient_for=self.parent_window,
            modal=True,
            use_header_bar=True,
        )
        dialog.add_css_class("zashterminal-dialog")
        dialog.set_resizable(False)
        try:
            titlebar = dialog.get_titlebar()
            if titlebar:
                titlebar.add_css_class("main-header-bar")
        except Exception:
            pass
        cancel_btn = dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        send_btn = dialog.add_button(_("Send"), Gtk.ResponseType.OK)
        if cancel_btn:
            cancel_btn.add_css_class("flat")
        if send_btn:
            send_btn.add_css_class("suggested-action")
        dialog.set_default_response(Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        content.set_spacing(16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        label = Gtk.Label(
            label=_(
                "The target server password is not saved for this session. "
                "Enter it to continue this SSH login."
            )
        )
        label.set_wrap(True)
        label.set_xalign(0.0)
        label.add_css_class("dim-label")
        content.append(label)

        entry_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry_box.add_css_class("card")
        content.append(entry_box)

        password_label = Gtk.Label(label=_("Server Password"))
        password_label.set_xalign(0.0)
        password_label.add_css_class("caption")
        password_label.add_css_class("dim-label")
        entry_box.append(password_label)

        password_entry = Gtk.PasswordEntry()
        if hasattr(password_entry, "set_activates_default"):
            password_entry.set_activates_default(True)
        entry_box.append(password_entry)

        def _submit_target_dialog_from_entry(_entry) -> None:
            try:
                dialog.response(Gtk.ResponseType.OK)
            except Exception:
                pass

        try:
            password_entry.connect("activate", _submit_target_dialog_from_entry)
        except Exception:
            pass

        def on_response(dlg: Gtk.Dialog, response_id: int) -> None:
            try:
                pending_now = self._balabit_gateway_pending_auth.get(terminal_id)
                if not pending_now:
                    return
                pending_now["target_password_prompt_open"] = ""
                if response_id == Gtk.ResponseType.OK:
                    target_password = password_entry.get_text().replace("\r", "")
                    pending_now["target_password"] = target_password
                    self._send_text_to_terminal(
                        terminal, target_password, terminal_id, "target_password"
                    )
                pending_now["target_password_pending"] = ""
                if not pending_now.get("gateway_password_pending"):
                    self._balabit_gateway_pending_auth.pop(terminal_id, None)
            finally:
                dlg.destroy()

        dialog.connect("response", on_response)
        dialog.present()
        password_entry.grab_focus()
        return False

