import unittest
from unittest.mock import patch

import server


class FakeResponse:
    def __init__(self, data):
        self.data = data
        self.ok = True
        self.status_code = 200

    def json(self):
        return self.data


class OpenAIProviderTest(unittest.TestCase):
    def test_route_and_qualify(self):
        with patch.object(server, "OPENAI_API_KEY", "sk-test"):
            self.assertEqual(server.resolve_route("openai/gpt-5-nano-2025-08-07"), ("openai", "gpt-5-nano-2025-08-07"))
            self.assertEqual(server.resolve_route("gpt-5-nano-2025-08-07"), ("openai", "gpt-5-nano-2025-08-07"))
            self.assertEqual(server.resolve_route("qwen/qwen2.5-vl-72b-instruct"), (server.MODEL_PROVIDER, "qwen/qwen2.5-vl-72b-instruct"))
            self.assertEqual(server.qualify_model("gpt-5-nano-2025-08-07"), "openai/gpt-5-nano-2025-08-07")
            self.assertEqual(server.qualify_model("openai/gpt-5-nano-2025-08-07"), "openai/gpt-5-nano-2025-08-07")
        with patch.object(server, "OPENAI_API_KEY", ""):
            self.assertEqual(server.resolve_route("openai/gpt-4o"), (server.MODEL_PROVIDER, "openai/gpt-4o"))
            self.assertEqual(server.qualify_model("gpt-5-nano-2025-08-07"), "gpt-5-nano-2025-08-07")

    def test_gpt5_payload_shape_and_pinning(self):
        captured = {}

        def fake_post(endpoint, headers=None, json=None, timeout=None):
            captured.update(endpoint=endpoint, headers=headers, payload=json)
            return FakeResponse({
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "model": "gpt-5-nano-2025-08-07",
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            })

        with patch.object(server, "OPENAI_API_KEY", "sk-test"), patch.object(server.requests, "post", fake_post):
            result = server.benchmark_model_call("openai/gpt-5-nano-2025-08-07", [{"role": "user", "content": "hi"}])

        self.assertEqual(captured["endpoint"], "https://api.openai.com/v1/chat/completions")
        payload = captured["payload"]
        self.assertEqual(payload["model"], "gpt-5-nano-2025-08-07")
        self.assertNotIn("max_tokens", payload)
        self.assertEqual(payload["max_completion_tokens"], 100 + 512)
        self.assertNotIn("temperature", payload)
        self.assertNotIn("top_p", payload)
        self.assertEqual(payload["reasoning_effort"], "minimal")
        # The resolved model round-trips with the openai/ prefix, so the pinned check passes.
        self.assertEqual(result["model"], "openai/gpt-5-nano-2025-08-07")

    def test_non_reasoning_openai_model_keeps_temperature(self):
        captured = {}

        def fake_post(endpoint, headers=None, json=None, timeout=None):
            captured.update(payload=json)
            return FakeResponse({"choices": [{"message": {"content": "hello"}}], "model": "gpt-4o", "usage": {}})

        with patch.object(server, "OPENAI_API_KEY", "sk-test"), patch.object(server.requests, "post", fake_post):
            server.model_chat("openai/gpt-4o", [{"role": "user", "content": "hi"}], max_tokens=50)

        payload = captured["payload"]
        self.assertEqual(payload["max_completion_tokens"], 50)
        self.assertIn("temperature", payload)
        self.assertNotIn("reasoning_effort", payload)

    def test_openrouter_fallback_gives_reasoning_models_headroom(self):
        captured = {}

        def fake_post(endpoint, headers=None, json=None, timeout=None):
            captured.update(endpoint=endpoint, payload=json)
            return FakeResponse({"choices": [{"message": {"content": "ok"}}], "model": "openai/gpt-5-nano-2025-08-07", "usage": {}})

        with patch.object(server, "OPENAI_API_KEY", ""), patch.object(server, "OPENROUTER_API_KEY", "or-test"), \
                patch.object(server.requests, "post", fake_post):
            server.model_chat("openai/gpt-5-nano-2025-08-07", [{"role": "user", "content": "hi"}], max_tokens=100)

        self.assertIn("openrouter.ai", captured["endpoint"])
        self.assertEqual(captured["payload"]["max_tokens"], 100 + 512)

    def test_has_key_for_each_route(self):
        with patch.object(server, "OPENAI_API_KEY", "sk-test"), patch.object(server, "OPENROUTER_API_KEY", ""):
            self.assertTrue(server.has_key_for("openai/gpt-5-nano-2025-08-07"))
            if server.MODEL_PROVIDER == "openrouter":
                self.assertFalse(server.has_key_for("qwen/qwen2.5-vl-72b-instruct"))
        with patch.object(server, "OPENAI_API_KEY", ""), patch.object(server, "OPENROUTER_API_KEY", "or-test"):
            if server.MODEL_PROVIDER == "openrouter":
                self.assertTrue(server.has_key_for("openai/gpt-4o"))


if __name__ == "__main__":
    unittest.main()
