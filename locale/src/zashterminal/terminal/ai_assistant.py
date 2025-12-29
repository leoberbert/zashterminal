# zashterminal/terminal/ai_assistant.py

"""AI assistant integration for Zashterminal terminals."""

from __future__ import annotations

import json
import os
import re
import threading
import weakref
from typing import Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("GLib", "2.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject

from ..data.ai_history_manager import get_ai_history_manager
from ..utils.logger import get_logger
from ..utils.translation_utils import _

# Lazy-loaded requests module (avoid import overhead on startup)
_requests_module = None


def _get_requests():
    """Get the requests module, importing lazily on first use."""
    global _requests_module
    if _requests_module is None:
        import requests
        _requests_module = requests
    return _requests_module


# Pre-compiled regex patterns for text formatting
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
_PLUS_WHITESPACE_PATTERN = re.compile(r"\s*\+\s*")
_SEMICOLON_NEWLINE_PATTERN = re.compile(r";\s*\n")
_SEMICOLON_SENTENCE_PATTERN = re.compile(r";\s*(?=[A-ZÁÀÃÂÉÊÍÓÔÕÚÜÇ0-9])")
_BOLD_ASTERISK_PATTERN = re.compile(r"\*\*([^*]+)\*\*")
_BOLD_UNDERSCORE_PATTERN = re.compile(r"__([^_]+)__")
_NUMBERED_LIST_START_PATTERN = re.compile(r"(?<!\n)(\d+\.)")
_NUMBERED_LIST_FIX_PATTERN = re.compile(r"\n\s*(\d+)\s*(?=\n\d)\n")
_DASH_LIST_PATTERN = re.compile(r"\n\s*-\s+")
_ASTERISK_LIST_PATTERN = re.compile(r"\n\s*\*\s+")
_MULTIPLE_NEWLINES_PATTERN = re.compile(r"\n{3,}")


class TerminalAiAssistant(GObject.Object):
    """Coordinates conversations with an external AI service."""

    __gsignals__ = {
        # Signal emitted when streaming message chunks arrive
        # Args: (chunk: str, is_done: bool)
        "streaming-chunk": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),
        # Signal emitted when a full response is ready
        # Args: (reply: str, commands: list)
        "response-ready": (GObject.SignalFlags.RUN_FIRST, None, (str, object)),
        # Signal emitted on error
        # Args: (error_message: str)
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
    DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"
    DEFAULT_OPENROUTER_MODEL = "openrouter/polaris-alpha"
    DEFAULT_LOCAL_MODEL = "llama3.2"

    # PROMPT OTIMIZADO, DIRETO E DINÂMICO
    _SYSTEM_PROMPT_TEMPLATE = (
        "You are an expert Linux terminal assistant running on {os_context}."
        " Your goal is to provide accurate, safe, and executable command-line solutions."
        "\n\n"
        "**CRITICAL RULES:**\n"
        "1. **OUTPUT FORMAT:** You must respond with RAW JSON only. Do NOT wrap the output in markdown code blocks (like ```json ... ```).\n"
        '2. **JSON STRUCTURE:** {{ "reply": "<explanation using markdown>", "commands": ["<cmd1>", "<cmd2>"] }}\n'
        "3. **LANGUAGE:** Respond strictly in {language}.\n"
        "4. **SCOPE:** Answer only Linux, networking, coding, and sysadmin questions. Politely refuse off-topic requests.\n"
        "\n"
        "**FIELD DETAILS:**\n"
        "- 'reply': The explanation text. You MAY use Markdown (bold, lists, inline code) inside this string for readability.\n"
        "- 'commands': A list of standalone, executable shell commands appropriate for {os_context}. Do not include placeholders like '<file>' unless necessary.\n"
    )

    @staticmethod
    def _detect_os_context() -> str:
        """Detects the OS name and base to give context to the AI."""
        os_name = "Linux"
        base_distro = ""

        # Try os-release (Standard modern Linux)
        if os.path.exists("/etc/os-release"):
            try:
                with open("/etc/os-release", "r") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            os_name = line.split("=", 1)[1].strip().strip('"')
                        elif line.startswith("ID_LIKE="):
                            base_distro = line.split("=", 1)[1].strip().strip('"')
            except Exception:
                pass
        # Fallback to lsb-release (Legacy/Specific)
        elif os.path.exists("/etc/lsb-release"):
            try:
                with open("/etc/lsb-release", "r") as f:
                    for line in f:
                        if line.startswith("DISTRIB_DESCRIPTION="):
                            os_name = line.split("=", 1)[1].strip().strip('"')
            except Exception:
                pass

        # If we found a base (e.g., "arch" for BigLinux/Manjaro, or "debian" for Ubuntu), include it
        if base_distro:
            return f"{os_name} (based on {base_distro})"
        return os_name

    @classmethod
    def _get_system_prompt(cls) -> str:
        """Get the system prompt with the system's default language and OS context."""
        import locale

        try:
            # Get the system language
            lang_code = locale.getdefaultlocale()[0] or "en_US"
            # Map common locale codes to language names
            lang_map = {
                "pt": "Portuguese",
                "en": "English",
                "es": "Spanish",
                "fr": "French",
                "de": "German",
                "it": "Italian",
                "zh": "Chinese",
                "ja": "Japanese",
                "ko": "Korean",
                "ru": "Russian",
                "ar": "Arabic",
                "nl": "Dutch",
                "pl": "Polish",
                "tr": "Turkish",
                "uk": "Ukrainian",
                "cs": "Czech",
                "sv": "Swedish",
                "da": "Danish",
                "fi": "Finnish",
                "no": "Norwegian",
                "hu": "Hungarian",
                "ro": "Romanian",
                "bg": "Bulgarian",
                "el": "Greek",
                "he": "Hebrew",
                "hr": "Croatian",
                "sk": "Slovak",
                "et": "Estonian",
                "is": "Icelandic",
            }
            lang_prefix = lang_code.split("_")[0].lower()
            language = lang_map.get(lang_prefix, "English")
        except Exception:
            language = "English"

        os_context = cls._detect_os_context()

        return cls._SYSTEM_PROMPT_TEMPLATE.format(
            language=language, os_context=os_context
        )

    def __init__(self, window, settings_manager, terminal_manager):
        super().__init__()
        self.logger = get_logger("zashterminal.terminal.ai_assistant")
        self._window_ref = weakref.ref(window)
        self.settings_manager = settings_manager
        self.terminal_manager = terminal_manager
        self._conversations: Dict[int, List[Dict[str, str]]] = {}
        self._terminal_refs: Dict[int, weakref.ReferenceType] = {}
        self._inflight: Dict[int, bool] = {}
        self._lock = threading.RLock()
        self._history_manager_instance = None  # Lazy loaded via property
        # Callbacks for streaming updates
        self._streaming_callback: Optional[Callable[[str, bool], None]] = None

    @property
    def _history_manager(self):
        """Lazy load the AI history manager on first access."""
        if self._history_manager_instance is None:
            self._history_manager_instance = get_ai_history_manager()
        return self._history_manager_instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def is_enabled(self) -> bool:
        return self.settings_manager.get("ai_assistant_enabled", False)

    def missing_configuration(self) -> List[str]:
        missing = []
        provider = self.settings_manager.get("ai_assistant_provider", "").strip()
        api_key = self.settings_manager.get("ai_assistant_api_key", "").strip()
        if not provider:
            missing.append("provider")
            return missing
        if provider in {"groq", "gemini", "openrouter"}:
            if not api_key:
                missing.append("api_key")
        elif provider == "local":
            # Local providers may not need API key
            base_url = self.settings_manager.get("ai_local_base_url", "").strip()
            if not base_url:
                missing.append("base_url")
        else:
            missing.append("provider")
        return missing

    def request_assistance(
        self,
        terminal,
        prompt: str,
        streaming_callback: Optional[Callable[[str, bool], None]] = None,
    ) -> bool:
        """Kick off an assistant request for the provided terminal."""
        if not prompt:
            return False
        if not self.is_enabled():
            self._queue_toast(
                "Enable the AI assistant in Preferences before requesting help."
            )
            return False
        try:
            terminal_id = self._ensure_terminal_reference(terminal)
        except ValueError:
            self._queue_toast("Unable to identify the active terminal.")
            return False

        with self._lock:
            if self._inflight.get(terminal_id):
                self._queue_toast(
                    "The assistant is still processing the previous request."
                )
                return False
            self._inflight[terminal_id] = True
            self._streaming_callback = streaming_callback

        # Save user message to history
        self._history_manager.add_user_message(prompt)

        worker = threading.Thread(
            target=self._process_request_thread, args=(terminal_id, prompt), daemon=True
        )
        worker.start()
        return True

    def request_assistance_simple(
        self,
        prompt: str,
        streaming_callback: Optional[Callable[[str, bool], None]] = None,
    ) -> bool:
        """
        Request assistance without a specific terminal context.
        Used by the AI overlay panel.
        """
        if not prompt:
            return False
        if not self.is_enabled():
            self._queue_toast(
                "Enable the AI assistant in Preferences before requesting help."
            )
            return False

        # Use a special terminal_id for non-terminal requests
        terminal_id = -1  # Special ID for overlay panel

        with self._lock:
            if self._inflight.get(terminal_id):
                self._queue_toast(
                    "The assistant is still processing the previous request."
                )
                return False
            self._inflight[terminal_id] = True
            self._streaming_callback = streaming_callback

        # Save user message to history
        self._history_manager.add_user_message(prompt)

        worker = threading.Thread(
            target=self._process_request_thread, args=(terminal_id, prompt), daemon=True
        )
        worker.start()
        return True

    def clear_conversation_for_terminal(self, terminal) -> None:
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            return
        with self._lock:
            self._cleanup_terminal_state(terminal_id)

    def clear_all_conversations(self) -> None:
        with self._lock:
            self._conversations.clear()
            self._terminal_refs.clear()
            self._inflight.clear()

    def handle_setting_changed(self, key: str, _old_value: Any, new_value: Any) -> None:
        if key == "ai_assistant_enabled" and not new_value:
            self.clear_all_conversations()
        if key in {
            "ai_assistant_provider",
            "ai_assistant_api_key",
            "ai_assistant_model",
            "ai_openrouter_site_url",
            "ai_openrouter_site_name",
            "ai_local_base_url",
        }:
            self.clear_all_conversations()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_terminal_reference(self, terminal) -> int:
        terminal_id = getattr(terminal, "terminal_id", None)
        if terminal_id is None:
            raise ValueError("terminal is missing a terminal_id attribute")

        if terminal_id not in self._terminal_refs:
            self._terminal_refs[terminal_id] = weakref.ref(
                terminal,
                lambda _ref, tid=terminal_id: self._cleanup_terminal_state(tid),
            )
        return terminal_id

    def _process_request_thread(self, terminal_id: int, prompt: str) -> None:
        try:
            if self._should_decline_code_request(prompt):
                self._build_messages(terminal_id, prompt)
                refusal = "Desculpe, no momento não estou programado para gerar scripts complexos, apenas comandos diretos."
                self._record_assistant_message(terminal_id, refusal)
                # Save to history
                self._history_manager.add_assistant_message(refusal)
                GLib.idle_add(
                    self._display_assistant_reply,
                    terminal_id,
                    refusal,
                    [],
                    [],
                )
                return

            messages = self._build_messages(terminal_id, prompt)
            config = self._load_configuration()

            # Check if we should use streaming
            if self._streaming_callback:
                content = self._perform_streaming_request(config, messages)
            else:
                content = self._perform_request(config, messages)

            reply, commands, code_snippets = self._parse_assistant_payload(content)
            self._record_assistant_message(terminal_id, reply)
            # Save to history with commands (convert dicts to strings for storage)
            command_strings_for_history = [
                cmd.get("command", "")
                for cmd in commands
                if isinstance(cmd, dict) and cmd.get("command")
            ]
            self._history_manager.add_assistant_message(
                reply, command_strings_for_history
            )

            GLib.idle_add(
                self._display_assistant_reply,
                terminal_id,
                reply,
                commands,
                code_snippets,
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("AI assistant request failed: %s", exc)
            error_message = "Sorry, I couldn't complete the request: {}".format(exc)
            self._record_assistant_message(terminal_id, error_message)
            GLib.idle_add(
                self._display_error_reply,
                terminal_id,
                error_message,
            )
            # Emit error signal
            GLib.idle_add(self.emit, "error", error_message)
        finally:
            with self._lock:
                self._inflight.pop(terminal_id, None)
                self._streaming_callback = None

    def _build_messages(self, terminal_id: int, prompt: str) -> List[Dict[str, str]]:
        with self._lock:
            history = self._conversations.setdefault(terminal_id, [])
            history.append({"role": "user", "content": prompt})
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": self._get_system_prompt()}
            ]
            messages.extend(history)
            return messages

    def _load_configuration(self) -> Dict[str, str]:
        config = {
            "provider": self.settings_manager.get("ai_assistant_provider", "").strip(),
            "model": self.settings_manager.get("ai_assistant_model", "").strip(),
            "api_key": self.settings_manager.get("ai_assistant_api_key", "").strip(),
        }
        config["openrouter_site_url"] = self.settings_manager.get(
            "ai_openrouter_site_url", ""
        ).strip()
        config["openrouter_site_name"] = self.settings_manager.get(
            "ai_openrouter_site_name", ""
        ).strip()
        config["local_base_url"] = self.settings_manager.get(
            "ai_local_base_url", "http://localhost:11434/v1"
        ).strip()
        if not config["provider"]:
            raise RuntimeError(
                "Select a provider in Preferences > Terminal > AI Assistant."
            )
        if config["provider"] == "groq" and not config["model"]:
            config["model"] = self.DEFAULT_GROQ_MODEL
        elif config["provider"] == "gemini" and not config["model"]:
            config["model"] = self.DEFAULT_GEMINI_MODEL
        elif config["provider"] == "openrouter" and not config["model"]:
            config["model"] = self.DEFAULT_OPENROUTER_MODEL
        elif config["provider"] == "local" and not config["model"]:
            config["model"] = self.DEFAULT_LOCAL_MODEL
        return config

    def _perform_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        provider = config["provider"]
        if provider == "groq":
            return self._perform_groq_request(config, messages)
        if provider == "gemini":
            return self._perform_gemini_request(config, messages)
        if provider == "openrouter":
            return self._perform_openrouter_request(config, messages)
        if provider == "local":
            return self._perform_local_request(config, messages)
        raise RuntimeError(f"Provider '{provider}' is not supported in this version.")

    def _perform_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        """Perform a streaming request, sending chunks via callback."""
        provider = config["provider"]
        if provider == "local":
            return self._perform_local_streaming_request(config, messages)
        if provider == "openrouter":
            return self._perform_openrouter_streaming_request(config, messages)
        if provider == "groq":
            return self._perform_groq_streaming_request(config, messages)
        # Fall back to non-streaming for providers that don't support it well
        return self._perform_request(config, messages)

    def _perform_local_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        """Perform request to local OpenAI-compatible API (Ollama, LM Studio, etc.)."""
        requests = _get_requests()

        base_url = config.get("local_base_url", "http://localhost:11434/v1").rstrip("/")
        model = config.get("model", "").strip() or self.DEFAULT_LOCAL_MODEL

        payload_messages = self._build_openai_messages(messages)
        url = f"{base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        api_key = config.get("api_key", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the local AI service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP error {response.status_code}: {response.text.strip()}"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError("Local AI returned an invalid JSON response.") from exc

        choices = response_data.get("choices") or []
        if not choices:
            raise RuntimeError("The server response did not contain any suggestions.")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("text")
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Local AI did not return any usable content.")
        return content.strip()

    def _perform_local_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        """Perform streaming request to local OpenAI-compatible API."""
        requests = _get_requests()

        base_url = config.get("local_base_url", "http://localhost:11434/v1").rstrip("/")
        model = config.get("model", "").strip() or self.DEFAULT_LOCAL_MODEL

        payload_messages = self._build_openai_messages(messages)
        url = f"{base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": True,
        }

        headers = {"Content-Type": "application/json"}
        api_key = config.get("api_key", "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=120, stream=True
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the local AI service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP error {response.status_code}: {response.text.strip()}"
            )

        full_content = ""
        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        chunk = delta.get("content", "")
                        if chunk:
                            full_content += chunk
                            if self._streaming_callback:
                                GLib.idle_add(self._streaming_callback, chunk, False)
                except json.JSONDecodeError:
                    continue

        if self._streaming_callback:
            GLib.idle_add(self._streaming_callback, "", True)

        return full_content

    def _perform_groq_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        """Perform streaming request to Groq API."""
        requests = _get_requests()

        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the Groq API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_GROQ_MODEL

        payload_messages = self._build_openai_messages(messages)
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": True,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=60, stream=True
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the Groq service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(self._format_openrouter_error(response))

        full_content = ""
        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        chunk = delta.get("content", "")
                        if chunk:
                            full_content += chunk
                            if self._streaming_callback:
                                GLib.idle_add(self._streaming_callback, chunk, False)
                except json.JSONDecodeError:
                    continue

        if self._streaming_callback:
            GLib.idle_add(self._streaming_callback, "", True)

        return full_content

    def _perform_openrouter_streaming_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        """Perform streaming request to OpenRouter API."""
        requests = _get_requests()

        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the OpenRouter API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_OPENROUTER_MODEL
        payload_messages = self._build_openai_messages(messages)
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload: Dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "stream": True,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        site_url = config.get("openrouter_site_url")
        site_name = config.get("openrouter_site_name")
        if site_url:
            headers["HTTP-Referer"] = site_url
        if site_name:
            headers["X-Title"] = site_name

        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=60, stream=True
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to query the OpenRouter service: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP error {response.status_code}: {response.text.strip()}"
            )

        full_content = ""
        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        chunk = delta.get("content", "")
                        if chunk:
                            full_content += chunk
                            if self._streaming_callback:
                                GLib.idle_add(self._streaming_callback, chunk, False)
                except json.JSONDecodeError:
                    continue

        if self._streaming_callback:
            GLib.idle_add(self._streaming_callback, "", True)

        return full_content

    def _perform_gemini_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        requests = _get_requests()

        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the Gemini API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_GEMINI_MODEL

        system_instruction, contents = self._build_gemini_conversation(messages)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the Gemini service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(self._format_openrouter_error(response))

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError("Gemini returned an invalid JSON response.") from exc

        candidates = response_data.get("candidates") or []
        if not candidates:
            raise RuntimeError("The server response did not contain any suggestions.")

        collected: List[str] = []
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not parts:
                continue
            for part in parts:
                if isinstance(part, dict) and part.get("text"):
                    collected.append(part["text"])

        if collected:
            return "\n".join(collected)

        raise RuntimeError("Gemini did not return any usable content.")

    def _perform_groq_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        requests = _get_requests()

        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the Groq API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_GROQ_MODEL

        payload_messages = self._build_openai_messages(messages)
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload: Dict[str, Any] = {"model": model, "messages": payload_messages}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to query the Groq service: {exc}") from exc

        if response.status_code >= 400:
            raise RuntimeError(self._format_openrouter_error(response))

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError("Groq returned an invalid JSON response.") from exc

        choices = response_data.get("choices") or []
        if not choices:
            raise RuntimeError("The server response did not contain any suggestions.")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("text")
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq did not return any usable content.")
        return content.strip()

    def _perform_openrouter_request(
        self, config: Dict[str, str], messages: List[Dict[str, str]]
    ) -> str:
        requests = _get_requests()

        api_key = config.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("Configure the OpenRouter API key in Preferences.")

        model = config.get("model", "").strip() or self.DEFAULT_OPENROUTER_MODEL
        payload_messages = self._build_openai_messages(messages)
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload: Dict[str, Any] = {"model": model, "messages": payload_messages}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        site_url = config.get("openrouter_site_url")
        site_name = config.get("openrouter_site_name")
        if site_url:
            headers["HTTP-Referer"] = site_url
        if site_name:
            headers["X-Title"] = site_name

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Failed to query the OpenRouter service: {exc}"
            ) from exc

        if response.status_code >= 400:
            raise RuntimeError(
                f"HTTP error {response.status_code}: {response.text.strip()}"
            )

        try:
            response_data = response.json()
        except ValueError as exc:
            raise RuntimeError("OpenRouter returned an invalid JSON response.") from exc

        choices = response_data.get("choices") or []
        if not choices:
            raise RuntimeError("The server response did not contain any suggestions.")

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("text")
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter did not return any usable content.")
        return content.strip()

    def _format_openrouter_error(self, response: Any) -> str:
        """Format error from HTTP response. Response type is requests.Response."""
        status = response.status_code
        fallback = response.text.strip() or _("Unknown error.")
        try:
            payload = response.json()
        except ValueError:
            return _("OpenRouter respondeu com HTTP {status}: {message}").format(
                status=status, message=fallback
            )

        error_obj = payload.get("error")
        if not isinstance(error_obj, dict):
            return _("OpenRouter respondeu com HTTP {status}: {message}").format(
                status=status, message=fallback
            )

        message = error_obj.get("message")
        metadata = error_obj.get("metadata", {})
        provider_name = metadata.get("provider_name")
        raw_detail = metadata.get("raw")
        details = []
        if provider_name:
            details.append(provider_name)
        if raw_detail:
            details.append(raw_detail)
        extra = f" ({' | '.join(details)})" if details else ""

        clean_message = message or fallback
        return _("OpenRouter respondeu com HTTP {status}: {message}{detail}").format(
            status=status,
            message=clean_message,
            detail=extra,
        )

    def _build_gemini_conversation(
        self, messages: List[Dict[str, str]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        system_instruction = ""
        contents: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            text = message.get("content", "")
            if not text:
                continue
            if role == "system" and not system_instruction:
                system_instruction = text
                continue
            mapped_role = "model" if role == "assistant" else "user"
            contents.append({
                "role": mapped_role,
                "parts": [{"text": text}],
            })
        if not contents:
            contents.append({"role": "user", "parts": [{"text": ""}]})
        return system_instruction, contents

    def _build_openai_messages(
        self, messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        formatted: List[Dict[str, str]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if not isinstance(content, str) or not content:
                continue
            role_mapped = role
            if role not in {"system", "user", "assistant"}:
                role_mapped = "user"
            formatted.append({"role": role_mapped, "content": content})
        return formatted

    def _parse_assistant_payload(
        self, content: str
    ) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
        # Tenta limpar marcadores de markdown que as IAs adoram colocar
        clean_content = self._clean_response(content)

        reply_text = ""
        commands: List[Dict[str, str]] = []
        code_snippets: List[Dict[str, str]] = []
        payload = None

        # Tentativa 1: Parse direto do JSON limpo
        try:
            payload = json.loads(clean_content)
        except json.JSONDecodeError:
            # Tentativa 2: Tentar encontrar o primeiro objeto JSON válido na string
            payload = self._extract_json_object(clean_content)

        if isinstance(payload, dict):
            # Sucesso no JSON
            reply_text = payload.get("reply", "")
            commands_field = payload.get("commands", [])
            commands = self._normalize_commands(commands_field)
        else:
            # FALHA NO JSON (Fallback):
            # A IA respondeu texto puro. Vamos usar o texto como resposta
            # e tentar extrair comandos via Regex dos blocos de código.
            reply_text = content  # Usa o conteúdo original para manter formatação

            # Extrai comandos de blocos de código bash/sh/zsh automaticamente
            # Regex procura por ```bash ou ```sh seguido de conteúdo
            code_block_pattern = r"```(?:bash|sh|zsh)?\n(.*?)```"
            matches = re.findall(code_block_pattern, content, re.DOTALL)

            for match in matches:
                cmd_str = match.strip()
                # Evita adicionar scripts longos como botões de comando único
                if cmd_str and len(cmd_str.splitlines()) == 1:
                    commands.append({
                        "command": cmd_str,
                        "description": "Suggested command",
                    })

        return reply_text, commands, code_snippets

    def _clean_response(self, raw_content: str) -> str:
        """Remove markdown code fences wrapping the JSON."""
        clean = raw_content.strip()

        # Remove ```json no início e ``` no fim
        if clean.startswith("```"):
            # Encontra a primeira quebra de linha
            first_newline = clean.find("\n")
            if first_newline != -1:
                # Remove a primeira linha (ex: ```json)
                clean = clean[first_newline + 1 :]

            # Remove o fechamento ``` se existir no final
            if clean.endswith("```"):
                clean = clean[:-3]

        return clean.strip()

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        start = text.find("{")
        while start != -1:
            brace_level = 0
            for end in range(start, len(text)):
                char = text[end]
                if char == "{":
                    brace_level += 1
                elif char == "}":
                    brace_level -= 1
                    if brace_level == 0:
                        candidate = text[start : end + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            start = text.find("{", start + 1)
        return None

    def _normalize_commands(self, value: Any) -> List[Dict[str, str]]:
        commands: List[Dict[str, str]] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    commands.append({"command": item.strip(), "description": ""})
                elif isinstance(item, dict):
                    candidate = item.get("command") or item.get("cmd")
                    description = item.get("description") or ""
                    if isinstance(candidate, str) and candidate.strip():
                        commands.append({
                            "command": candidate.strip(),
                            "description": description.strip()
                            if isinstance(description, str)
                            else "",
                        })
        elif isinstance(value, str) and value.strip():
            commands.append({"command": value.strip(), "description": ""})
        return commands

    @staticmethod
    def _should_decline_code_request(prompt: str) -> bool:
        """Detect requests that explicitly ask for code or scripts."""
        if not isinstance(prompt, str):
            return False
        lowered = prompt.lower()
        if not lowered:
            return False
        code_terms = {
            "codigo",
            "código",
            "code",
            "script",
            "shell script",
            "programa",
            "programação",
            "function",
            "função",
            "classe",
            "snippet",
            "trecho de código",
            "escreva um",
        }
        request_terms = {
            "gere",
            "gerar",
            "crie",
            "criar",
            "escreva",
            "escrever",
            "forneça",
            "mostrar",
            "mostre",
            "faça",
            "montar",
            "monta",
            "me dê",
            "me mostre",
            "me forneça",
            "poderia",
            "pode",
        }
        has_code_term = any(term in lowered for term in code_terms) or "```" in lowered
        has_request_term = any(term in lowered for term in request_terms)
        return has_code_term and has_request_term

    def _record_assistant_message(self, terminal_id: int, message: str) -> None:
        with self._lock:
            history = self._conversations.setdefault(terminal_id, [])
            history.append({"role": "assistant", "content": message})

    def _display_assistant_reply(
        self,
        terminal_id: int,
        reply: str,
        commands: List[Dict[str, str]],
        code_snippets: List[Dict[str, str]],
    ) -> bool:
        # Extract command strings for the signal
        command_strings = [
            cmd.get("command", "") for cmd in commands if isinstance(cmd, dict)
        ]

        # Emit response-ready signal for the chat panel
        self.emit("response-ready", reply, command_strings)

        # For terminal_id == -1 (overlay panel), skip terminal output
        if terminal_id == -1:
            return False

        terminal = self._get_terminal(terminal_id)
        window = self._window_ref()
        if not terminal or not window:
            # Fallback to terminal output if window not available
            if terminal:
                terminal.feed(
                    ("\n[AI Assistant] {}\n".format(reply.strip())).encode("utf-8")
                )
                for info in commands:
                    command_text = info.get("command") if isinstance(info, dict) else ""
                    if command_text:
                        terminal.feed(
                            (
                                "[AI Assistant] Command: {}\n".format(command_text)
                            ).encode("utf-8")
                        )
                for snippet in code_snippets:
                    code_text = snippet.get("code") if isinstance(snippet, dict) else ""
                    if code_text:
                        terminal.feed(
                            (
                                "[AI Assistant] Code suggestion:\n{}\n".format(
                                    code_text
                                )
                            ).encode("utf-8")
                        )
            return False

        try:
            formatted_reply = self._format_reply_for_dialog(reply)
            window.show_ai_response_dialog(
                terminal, formatted_reply, commands, code_snippets
            )
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("Failed to show AI response dialog: %s", exc)
            terminal.feed(
                (
                    "\n[AI Assistant] {}\n".format(self._format_reply_for_dialog(reply))
                ).encode("utf-8")
            )
        return False

    @staticmethod
    def _format_reply_for_dialog(text: str) -> str:
        """Improve readability by normalizing inline code and list formatting."""
        if not isinstance(text, str):
            return ""

        cleaned = text
        cleaned = cleaned.replace("\r\n", "\n")
        cleaned = cleaned.replace("\\n", "\n").replace("\\t", "\t")
        cleaned = _INLINE_CODE_PATTERN.sub(r"\1", cleaned)
        cleaned = _PLUS_WHITESPACE_PATTERN.sub(" ", cleaned)
        cleaned = _SEMICOLON_NEWLINE_PATTERN.sub("\n", cleaned)
        cleaned = _SEMICOLON_SENTENCE_PATTERN.sub(".\n", cleaned)
        cleaned = _BOLD_ASTERISK_PATTERN.sub(r"\1", cleaned)
        cleaned = _BOLD_UNDERSCORE_PATTERN.sub(r"\1", cleaned)
        cleaned = _NUMBERED_LIST_START_PATTERN.sub(r"\n\1", cleaned)
        cleaned = _NUMBERED_LIST_FIX_PATTERN.sub(r"\n\1", cleaned)
        cleaned = _DASH_LIST_PATTERN.sub("\n• ", cleaned)
        cleaned = _ASTERISK_LIST_PATTERN.sub("\n• ", cleaned)
        cleaned = _MULTIPLE_NEWLINES_PATTERN.sub("\n\n", cleaned)

        lines = []
        previous_blank = False
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                if not previous_blank:
                    lines.append("")
                    previous_blank = True
                continue
            lines.append(line)
            previous_blank = False

        return "\n".join(lines).strip()

    def _display_error_reply(self, terminal_id: int, message: str) -> bool:
        self._queue_toast(message)
        return False

    def _get_terminal(self, terminal_id: int):
        ref = self._terminal_refs.get(terminal_id)
        return ref() if ref else None

    def _cleanup_terminal_state(self, terminal_id: int) -> None:
        self._conversations.pop(terminal_id, None)
        self._terminal_refs.pop(terminal_id, None)
        self._inflight.pop(terminal_id, None)

    def _queue_toast(self, message: str) -> None:
        def _show_toast():
            window = self._window_ref()
            if window and hasattr(window, "toast_overlay"):
                toast = Adw.Toast(title=message)
                window.toast_overlay.add_toast(toast)
            return False

        GLib.idle_add(_show_toast)
