import unittest

from smt_quality_agent.drilldown_chat import build_chat_response, build_llm_grounding
from smt_quality_agent.llm import (
    PROVIDERS,
    build_request,
    chat_completion,
    masked_llm_config,
    normalize_llm_config,
    parse_response,
)


def config_for(provider, **overrides):
    return normalize_llm_config({
        "enabled": True,
        "provider": provider,
        "api_key": "sk-test",
        **overrides,
    })


class ProviderRegistryTest(unittest.TestCase):
    def test_all_six_providers_present(self) -> None:
        self.assertEqual(
            set(PROVIDERS),
            {"openai", "anthropic", "gemini", "deepseek", "qwen", "zhipu"},
        )
        for info in PROVIDERS.values():
            self.assertIn(info["protocol"], {"openai", "anthropic", "gemini"})
            self.assertTrue(info["base_url"].startswith("https://"))
            self.assertTrue(info["default_model"])

    def test_normalize_fills_provider_defaults(self) -> None:
        config = normalize_llm_config({"provider": "zhipu"})
        self.assertEqual(config["model"], PROVIDERS["zhipu"]["default_model"])
        self.assertEqual(config["base_url"], PROVIDERS["zhipu"]["base_url"])

    def test_unknown_provider_falls_back(self) -> None:
        config = normalize_llm_config({"provider": "nope"})
        self.assertIn(config["provider"], PROVIDERS)

    def test_masked_config_hides_key_and_lists_providers(self) -> None:
        masked = masked_llm_config(config_for("deepseek"))
        self.assertEqual(masked["api_key"], "******")
        self.assertTrue(masked["key_set"])
        self.assertEqual(set(masked["providers"]), set(PROVIDERS))


class RequestBuilderTest(unittest.TestCase):
    def test_openai_protocol_minimal_body(self) -> None:
        for provider in ("openai", "deepseek", "qwen", "zhipu"):
            url, headers, payload = build_request(config_for(provider), "sys", "q")
            self.assertEqual(url, PROVIDERS[provider]["base_url"])
            self.assertEqual(headers["Authorization"], "Bearer sk-test")
            # Minimal body: only model + messages, for maximum vendor compat.
            self.assertEqual(set(payload), {"model", "messages"})
            self.assertEqual(payload["messages"][0]["role"], "system")

    def test_anthropic_protocol(self) -> None:
        url, headers, payload = build_request(config_for("anthropic"), "sys", "q")
        self.assertIn("api.anthropic.com/v1/messages", url)
        self.assertEqual(headers["x-api-key"], "sk-test")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(payload["system"], "sys")
        self.assertIn("max_tokens", payload)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "q"}])

    def test_gemini_protocol(self) -> None:
        config = config_for("gemini")
        url, headers, payload = build_request(config, "sys", "q")
        self.assertTrue(url.endswith(f"/models/{config['model']}:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "sk-test")
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "sys")


class ResponseParserTest(unittest.TestCase):
    def test_openai_response(self) -> None:
        body = {"choices": [{"message": {"content": " 答案 "}}]}
        self.assertEqual(parse_response(config_for("deepseek"), body), "答案")

    def test_anthropic_response(self) -> None:
        body = {"content": [{"type": "text", "text": "答"}, {"type": "text", "text": "案"}]}
        self.assertEqual(parse_response(config_for("anthropic"), body), "答案")

    def test_gemini_response(self) -> None:
        body = {"candidates": [{"content": {"parts": [{"text": "答案"}]}}]}
        self.assertEqual(parse_response(config_for("gemini"), body), "答案")

    def test_empty_response_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_response(config_for("openai"), {"choices": []})


class ChatFallbackTest(unittest.TestCase):
    def make_trigger(self):
        return {
            "trigger_id": "TRG-abc12345",
            "model": "M",
            "pad_name": "C1_1",
            "main_defect_cn": "多锡",
            "analysis_contract": {
                "scope": {"category": "单Pad孤立异常", "confidence": "高"},
                "root_cause_candidates": [],
            },
            "parameter_check": {"verdict": "参数无偏离"},
            "param_events": [],
        }

    def test_disabled_config_uses_rules(self) -> None:
        response = build_chat_response(
            self.make_trigger(), "现场先查什么？",
            config=normalize_llm_config({"enabled": False}),
        )
        self.assertEqual(response["mode"], "rule")
        self.assertNotIn("fallback_reason", response)

    def test_llm_success(self) -> None:
        def fake_post(url, headers, payload, timeout):
            return {"choices": [{"message": {"content": "结论：…"}}]}

        response = build_chat_response(
            self.make_trigger(), "为什么？",
            config=config_for("deepseek"), post=fake_post,
        )
        self.assertEqual(response["mode"], "llm")
        self.assertEqual(response["provider"], "deepseek")
        self.assertEqual(response["answer"]["text"], "结论：…")

    def test_llm_failure_falls_back_to_rules(self) -> None:
        def fake_post(url, headers, payload, timeout):
            raise RuntimeError("HTTP 401: bad key")

        response = build_chat_response(
            self.make_trigger(), "现场先查什么？",
            config=config_for("deepseek"), post=fake_post,
        )
        self.assertEqual(response["mode"], "rule")
        self.assertIn("HTTP 401", response["fallback_reason"])
        self.assertIn("answer", response)

    def test_missing_key_never_calls_network(self) -> None:
        def exploding_post(url, headers, payload, timeout):
            raise AssertionError("network must not be touched")

        config = normalize_llm_config({"enabled": True, "api_key": ""})
        response = build_chat_response(
            self.make_trigger(), "q", config=config, post=exploding_post,
        )
        self.assertEqual(response["mode"], "rule")

    def test_grounding_carries_contract_and_mechanisms(self) -> None:
        grounding = build_llm_grounding(self.make_trigger())
        self.assertIn("单Pad孤立异常", grounding)
        self.assertIn("mech.understencil_residue", grounding)
        self.assertIn("无符号偏差幅度", grounding)
        self.assertIn("数据未采集", grounding)


class ChatCompletionTest(unittest.TestCase):
    def test_requires_api_key(self) -> None:
        config = normalize_llm_config({"enabled": True})
        with self.assertRaises(RuntimeError):
            chat_completion(config, "s", "q", post=lambda *args: {})


if __name__ == "__main__":
    unittest.main()
