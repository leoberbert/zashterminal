# zashterminal/terminal/ai_providers.py

"""AI providers: registry, configuration and HTTP clients.

Three wire formats cover the market:

* ``openai``    - ``POST {base}/chat/completions`` (OpenAI, Groq, Mistral,
  DeepSeek, xAI, Perplexity, Together, OpenRouter, Ollama/LM Studio, ...)
* ``anthropic`` - ``POST {base}/messages`` (Anthropic and compatible gateways)
* ``gemini``    - Google ``generateContent``

The "custom" provider lets the user point any OpenAI- or Anthropic-compatible
endpoint (base URL + API key + model).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..utils.translation_utils import _

API_OPENAI = "openai"
API_ANTHROPIC = "anthropic"
API_GEMINI = "gemini"

CUSTOM_ID = "custom"
LOCAL_ID = "local"

ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 4096
REQUEST_TIMEOUT = 120


@dataclass(frozen=True)
class AIProvider:
    id: str
    label: str
    api: str
    base_url: str
    default_model: str
    key_required: bool = True


PROVIDERS: Tuple[AIProvider, ...] = (
    AIProvider("openai", "OpenAI", API_OPENAI, "https://api.openai.com/v1", "gpt-5.4-mini"),
    AIProvider(
        "anthropic", "Anthropic (Claude)", API_ANTHROPIC, "https://api.anthropic.com/v1",
        "claude-haiku-5-5",
    ),
    AIProvider(
        "gemini", "Google Gemini", API_GEMINI,
        "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash",
    ),
    AIProvider("xai", "xAI (Grok)", API_OPENAI, "https://api.x.ai/v1", "grok-4.7"),
    AIProvider("mistral", "Mistral AI", API_OPENAI, "https://api.mistral.ai/v1", "mistral-small-latest"),
    AIProvider("deepseek", "DeepSeek", API_OPENAI, "https://api.deepseek.com", "deepseek-flash"),
    AIProvider("groq", "Groq", API_OPENAI, "https://api.groq.com/openai/v1", "llama-3.1-8b-instant"),
    AIProvider("perplexity", "Perplexity", API_OPENAI, "https://api.perplexity.ai", "sonar"),
    AIProvider(
        "together", "Together AI", API_OPENAI, "https://api.together.xyz/v1",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    ),
    AIProvider(
        "openrouter", "OpenRouter", API_OPENAI, "https://openrouter.ai/api/v1",
        "openrouter/polaris-alpha",
    ),
    AIProvider(
        LOCAL_ID, "Local (Ollama/LM Studio)", API_OPENAI, "http://localhost:11434/v1",
        "llama3.2", key_required=False,
    ),
    # Label is translated where it is shown (see provider_label()).
    AIProvider(CUSTOM_ID, "Other (custom)", API_OPENAI, "", "", key_required=False),
)

_BY_ID = {p.id: p for p in PROVIDERS}


def get_provider(provider_id: str) -> Optional[AIProvider]:
    return _BY_ID.get((provider_id or "").strip())


def provider_label(provider: AIProvider) -> str:
    return _("Other (custom)") if provider.id == CUSTOM_ID else provider.label


@dataclass
class ProviderConfig:
    provider: AIProvider
    api: str
    base_url: str
    api_key: str
    model: str
    extra_headers: Dict[str, str] = field(default_factory=dict)

    @property
    def streams(self) -> bool:
        return self.api in (API_OPENAI, API_ANTHROPIC)


Getter = Callable[[str, Any], Any]


def _text(get: Getter, key: str, default: str = "") -> str:
    value = get(key, default)
    return value.strip() if isinstance(value, str) else default


def build_config(get: Getter) -> ProviderConfig:
    """Resolve the active provider from settings (``get(key, default)``)."""
    provider = get_provider(_text(get, "ai_assistant_provider"))
    if provider is None:
        raise RuntimeError(_("Select an AI provider in the AI Assistant settings."))
    api, base_url = provider.api, provider.base_url
    if provider.id == LOCAL_ID:
        base_url = _text(get, "ai_local_base_url") or provider.base_url
    elif provider.id == CUSTOM_ID:
        base_url = _text(get, "ai_custom_base_url")
        if _text(get, "ai_custom_api_format", API_OPENAI) == API_ANTHROPIC:
            api = API_ANTHROPIC
    headers: Dict[str, str] = {}
    if provider.id == "openrouter":
        site_url = _text(get, "ai_openrouter_site_url")
        site_name = _text(get, "ai_openrouter_site_name")
        if site_url:
            headers["HTTP-Referer"] = site_url
        if site_name:
            headers["X-Title"] = site_name
    return ProviderConfig(
        provider=provider,
        api=api,
        base_url=base_url.rstrip("/"),
        api_key=_text(get, "ai_assistant_api_key"),
        model=_text(get, "ai_assistant_model") or provider.default_model,
        extra_headers=headers,
    )


def missing_configuration(get: Getter) -> List[str]:
    provider = get_provider(_text(get, "ai_assistant_provider"))
    if provider is None:
        return ["provider"]
    missing: List[str] = []
    if provider.id in (LOCAL_ID, CUSTOM_ID):
        key = "ai_local_base_url" if provider.id == LOCAL_ID else "ai_custom_base_url"
        if not _text(get, key):
            missing.append("base_url")
    if provider.key_required and not _text(get, "ai_assistant_api_key"):
        missing.append("api_key")
    if not (_text(get, "ai_assistant_model") or provider.default_model):
        missing.append("model")
    return missing


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def openai_messages(messages: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    out = []
    for message in messages:
        content = message.get("content", "")
        if not isinstance(content, str) or not content:
            continue
        role = message.get("role", "user")
        out.append({"role": role if role in ("system", "user", "assistant") else "user", "content": content})
    return out


def anthropic_messages(
    messages: Iterable[Dict[str, str]],
) -> Tuple[str, List[Dict[str, str]]]:
    """``(system, messages)``: system prompt split out, roles alternating and
    starting with ``user`` as the Messages API requires."""
    system_parts: List[str] = []
    out: List[Dict[str, str]] = []
    for message in openai_messages(messages):
        if message["role"] == "system":
            system_parts.append(message["content"])
            continue
        if out and out[-1]["role"] == message["role"]:
            out[-1] = {"role": message["role"], "content": out[-1]["content"] + "\n\n" + message["content"]}
        else:
            out.append(dict(message))
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": "."})
    return "\n\n".join(system_parts), out


def gemini_conversation(
    messages: Iterable[Dict[str, str]],
) -> Tuple[str, List[Dict[str, Any]]]:
    system_instruction = ""
    contents: List[Dict[str, Any]] = []
    for message in openai_messages(messages):
        if message["role"] == "system" and not system_instruction:
            system_instruction = message["content"]
            continue
        role = "model" if message["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})
    if not contents:
        contents.append({"role": "user", "parts": [{"text": ""}]})
    return system_instruction, contents


def _headers(config: ProviderConfig) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api == API_ANTHROPIC:
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if config.api_key:
            headers["x-api-key"] = config.api_key
    elif config.api == API_GEMINI:
        if config.api_key:
            headers["x-goog-api-key"] = config.api_key
    elif config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    headers.update(config.extra_headers)
    return headers


def format_http_error(label: str, status: int, body: str) -> str:
    """Readable error from a provider response body (OpenAI/Anthropic/Google shapes)."""
    message = body.strip() or _("Unknown error.")
    detail = ""
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or message
            metadata = error.get("metadata") or {}
            extra = [str(metadata[k]) for k in ("provider_name", "raw") if metadata.get(k)]
            if extra:
                detail = f" ({' | '.join(extra)})"
        elif isinstance(error, str):
            message = error
        elif isinstance(payload.get("message"), str):
            message = payload["message"]
    return _("{provider} returned HTTP {status}: {message}{detail}").format(
        provider=label, status=status, message=message, detail=detail
    )


def _text_of(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("text")
        )
    return content if isinstance(content, str) else ""


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


def _requests():
    import requests  # lazy: keeps startup fast

    return requests


def _post(config: ProviderConfig, url: str, payload: Dict[str, Any], stream: bool):
    requests = _requests()
    label = provider_label(config.provider)
    if not config.base_url:
        raise RuntimeError(_("Configure the base URL in the AI Assistant settings."))
    try:
        response = requests.post(
            url, headers=_headers(config), json=payload, timeout=REQUEST_TIMEOUT, stream=stream
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            _("Failed to reach {provider}: {error}").format(provider=label, error=exc)
        ) from exc
    if response.status_code >= 400:
        raise RuntimeError(format_http_error(label, response.status_code, response.text))
    return response


def iter_sse_data(lines: Iterable[bytes]) -> Iterable[Dict[str, Any]]:
    """JSON objects from ``data:`` lines of a server-sent event stream."""
    for raw in lines:
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def stream_chunk(api: str, event: Dict[str, Any]) -> str:
    """Text carried by one streamed event."""
    if api == API_ANTHROPIC:
        if event.get("type") == "content_block_delta":
            delta = event.get("delta") or {}
            return delta.get("text", "") if delta.get("type") in (None, "text_delta") else ""
        if event.get("type") == "error":
            error = event.get("error") or {}
            raise RuntimeError(error.get("message") or _("Unknown error."))
        return ""
    choices = event.get("choices") or []
    if choices and isinstance(choices[0], dict):
        return _text_of((choices[0].get("delta") or {}).get("content", ""))
    return ""


def chat(
    config: ProviderConfig,
    messages: List[Dict[str, str]],
    on_chunk: Optional[Callable[[str], None]] = None,
) -> str:
    """Send the conversation; streams through ``on_chunk`` when supported."""
    label = provider_label(config.provider)
    stream = bool(on_chunk) and config.streams

    if config.api == API_GEMINI:
        system, contents = gemini_conversation(messages)
        payload: Dict[str, Any] = {"contents": contents}
        if system:
            payload["system_instruction"] = {"parts": [{"text": system}]}
        url = f"{config.base_url}/models/{config.model}:generateContent"
        data = _post(config, url, payload, False).json()
        texts = [
            part["text"]
            for candidate in data.get("candidates") or []
            for part in ((candidate.get("content") or {}).get("parts") or [])
            if isinstance(part, dict) and part.get("text")
        ]
        if not texts:
            raise RuntimeError(_("{provider} did not return any usable content.").format(provider=label))
        return "\n".join(texts)

    if config.api == API_ANTHROPIC:
        system, convo = anthropic_messages(messages)
        payload = {"model": config.model, "max_tokens": ANTHROPIC_MAX_TOKENS, "messages": convo}
        if system:
            payload["system"] = system
        url = f"{config.base_url}/messages"
    else:
        payload = {"model": config.model, "messages": openai_messages(messages)}
        url = f"{config.base_url}/chat/completions"

    if stream:
        payload["stream"] = True
        response = _post(config, url, payload, True)
        collected = []
        for event in iter_sse_data(response.iter_lines()):
            chunk = stream_chunk(config.api, event)
            if chunk:
                collected.append(chunk)
                on_chunk(chunk)
        return "".join(collected)

    data = _post(config, url, payload, False).json()
    if config.api == API_ANTHROPIC:
        text = _text_of(data.get("content"))
    else:
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
        text = _text_of(message.get("content")) if isinstance(message, dict) else ""
    if not text.strip():
        raise RuntimeError(_("{provider} did not return any usable content.").format(provider=label))
    return text.strip()


def list_models(config: ProviderConfig) -> List[Tuple[str, str]]:
    """``[(model id, display name)]`` from the provider's model listing."""
    requests = _requests()
    if not config.base_url:
        raise RuntimeError(_("Configure the base URL in the AI Assistant settings."))
    response = requests.get(f"{config.base_url}/models", headers=_headers(config), timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            format_http_error(provider_label(config.provider), response.status_code, response.text)
        )
    return parse_model_list(config.api, response.json())


def parse_model_list(api: str, data: Any) -> List[Tuple[str, str]]:
    models: List[Tuple[str, str]] = []
    if api == API_GEMINI:
        for item in (data or {}).get("models") or []:
            methods = item.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            model_id = str(item.get("name", "")).removeprefix("models/")
            if model_id:
                models.append((model_id, item.get("displayName") or model_id))
    else:
        items = data.get("data") if isinstance(data, dict) else data
        for item in items or []:
            if isinstance(item, dict) and item.get("id"):
                models.append((item["id"], item.get("display_name") or item.get("name") or item["id"]))
    models.sort(key=lambda m: m[1].lower())
    return models
