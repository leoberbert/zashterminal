"""AI providers: configuration and wire formats against a local fake API."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zashterminal.terminal import ai_providers as ap


class _Handler(BaseHTTPRequestHandler):
    seen = []  # (method, path, headers, body)

    def log_message(self, *_args):
        pass

    def _reply(self, status, body, content_type="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        _Handler.seen.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/openai/models":
            self._reply(200, json.dumps({"data": [{"id": "b-model"}, {"id": "a-model"}]}))
        elif self.path == "/anthropic/models":
            self._reply(200, json.dumps({"data": [{"id": "c-1", "display_name": "Claude One"}]}))
        else:
            self._reply(404, "{}")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append(("POST", self.path, dict(self.headers), body))
        if self.path == "/openai/chat/completions":
            if body.get("stream"):
                events = [{"choices": [{"delta": {"content": "Hel"}}]},
                          {"choices": [{"delta": {"content": "lo"}}]}]
                sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
                self._reply(200, sse, "text/event-stream")
            else:
                self._reply(200, json.dumps({"choices": [{"message": {"content": " Hi "}}]}))
        elif self.path == "/anthropic/messages":
            if body.get("stream"):
                events = [
                    {"type": "message_start"},
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Ol"}},
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "á"}},
                    {"type": "message_stop"},
                ]
                sse = "".join(f"event: x\ndata: {json.dumps(e)}\n\n" for e in events)
                self._reply(200, sse, "text/event-stream")
            else:
                self._reply(200, json.dumps({"content": [{"type": "text", "text": "Claude says hi"}]}))
        elif self.path == "/gemini/models/gem-1:generateContent":
            self._reply(200, json.dumps(
                {"candidates": [{"content": {"parts": [{"text": "gemini ok"}]}}]}))
        elif self.path == "/error/chat/completions":
            self._reply(401, json.dumps({"error": {"message": "Invalid API key"}}))
        else:
            self._reply(404, "{}")


class AIProvidersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.seen.clear()

    def custom(self, path, api_format="openai", model="m1", key="sk-test"):
        settings = {
            "ai_assistant_provider": "custom",
            "ai_custom_base_url": f"{self.base}/{path}/",
            "ai_custom_api_format": api_format,
            "ai_assistant_model": model,
            "ai_assistant_api_key": key,
        }
        return ap.build_config(lambda k, d=None: settings.get(k, d))

    # -- configuration -------------------------------------------------------

    def test_known_providers_have_endpoint_and_default_model(self):
        for provider in ap.PROVIDERS:
            if provider.id == ap.CUSTOM_ID:
                continue
            self.assertTrue(provider.base_url.startswith("http"), provider.id)
            self.assertTrue(provider.default_model, provider.id)
        ids = {p.id for p in ap.PROVIDERS}
        for expected in ("openai", "anthropic", "gemini", "xai", "mistral", "deepseek", "custom"):
            self.assertIn(expected, ids)

    def test_missing_configuration(self):
        def get(settings):
            return lambda k, d=None: settings.get(k, d)

        self.assertEqual(ap.missing_configuration(get({})), ["provider"])
        self.assertEqual(
            ap.missing_configuration(get({"ai_assistant_provider": "openai"})), ["api_key"]
        )
        self.assertEqual(
            ap.missing_configuration(get({"ai_assistant_provider": "custom"})),
            ["base_url", "model"],
        )
        ok = {"ai_assistant_provider": "custom", "ai_custom_base_url": "http://x", "ai_assistant_model": "m"}
        self.assertEqual(ap.missing_configuration(get(ok)), [])  # key optional

    def test_provider_default_model_used_when_none_set(self):
        cfg = ap.build_config(lambda k, d=None: {"ai_assistant_provider": "anthropic"}.get(k, d))
        self.assertEqual(cfg.model, ap.get_provider("anthropic").default_model)
        self.assertEqual(cfg.api, ap.API_ANTHROPIC)

    # -- wire formats ----------------------------------------------------------

    def test_openai_compatible_request_and_stream(self):
        cfg = self.custom("openai")
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        self.assertEqual(ap.chat(cfg, msgs), "Hi")
        _m, path, headers, body = _Handler.seen[-1]
        self.assertEqual(path, "/openai/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(body["model"], "m1")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})

        chunks = []
        self.assertEqual(ap.chat(cfg, msgs, on_chunk=chunks.append), "Hello")
        self.assertEqual(chunks, ["Hel", "lo"])

    def test_anthropic_request_and_stream(self):
        cfg = self.custom("anthropic", api_format="anthropic")
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "assistant", "content": "earlier"},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]
        self.assertEqual(ap.chat(cfg, msgs), "Claude says hi")
        _m, path, headers, body = _Handler.seen[-1]
        self.assertEqual(path, "/anthropic/messages")
        self.assertEqual(headers["x-api-key"], "sk-test")
        self.assertEqual(headers["anthropic-version"], ap.ANTHROPIC_VERSION)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(body["system"], "sys")
        self.assertEqual(body["max_tokens"], ap.ANTHROPIC_MAX_TOKENS)
        roles = [m["role"] for m in body["messages"]]
        self.assertEqual(roles[0], "user")  # must start with user
        self.assertTrue(all(a != b for a, b in zip(roles, roles[1:])))  # alternating
        self.assertEqual(body["messages"][-1]["content"], "a\n\nb")

        chunks = []
        self.assertEqual(ap.chat(cfg, msgs, on_chunk=chunks.append), "Olá")
        self.assertEqual(chunks, ["Ol", "á"])

    def test_gemini_request(self):
        provider = ap.get_provider("gemini")
        cfg = ap.ProviderConfig(provider, ap.API_GEMINI, f"{self.base}/gemini", "g-key", "gem-1")
        self.assertFalse(cfg.streams)
        out = ap.chat(cfg, [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}])
        self.assertEqual(out, "gemini ok")
        _m, _p, headers, body = _Handler.seen[-1]
        self.assertEqual(headers["x-goog-api-key"], "g-key")
        self.assertEqual(body["system_instruction"]["parts"][0]["text"], "s")

    def test_http_error_is_readable(self):
        with self.assertRaises(RuntimeError) as ctx:
            ap.chat(self.custom("error"), [{"role": "user", "content": "x"}])
        self.assertIn("401", str(ctx.exception))
        self.assertIn("Invalid API key", str(ctx.exception))

    def test_list_models(self):
        self.assertEqual(
            ap.list_models(self.custom("openai")),
            [("a-model", "a-model"), ("b-model", "b-model")],
        )
        self.assertEqual(
            ap.list_models(self.custom("anthropic", api_format="anthropic")),
            [("c-1", "Claude One")],
        )
        gemini = ap.parse_model_list(ap.API_GEMINI, {"models": [
            {"name": "models/gem-1", "displayName": "Gem", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
        ]})
        self.assertEqual(gemini, [("gem-1", "Gem")])


if __name__ == "__main__":
    unittest.main()
