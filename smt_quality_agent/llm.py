"""Multi-provider LLM client for the drilldown chat (docs/p2_llm_chat_design.md).

Six providers over three protocol adapters, pure stdlib (urllib). The request
builders and response parsers are pure functions so tests never touch the
network; the transport is injectable. openai-protocol request bodies carry
only ``model`` + ``messages`` — optional parameters like temperature or
max_tokens are rejected by some vendors' newer models, and the minimal body
works everywhere.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "llm.json"

DEFAULT_TIMEOUT_SECONDS = 30
ANTHROPIC_MAX_TOKENS = 1024
GEMINI_MAX_TOKENS = 1024

PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-5-mini",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-haiku-4-5-20251001",
    },
    "gemini": {
        "label": "Google Gemini",
        "protocol": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
    },
    "deepseek": {
        "label": "DeepSeek",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "label": "通义千问 Qwen",
        "protocol": "openai",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen-plus",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "protocol": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-4.6",
    },
}

DEFAULT_LLM_CONFIG: dict[str, Any] = {
    "enabled": False,
    "provider": "deepseek",
    "api_key": "",
    "model": "",
    "base_url": "",
    "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
}


def normalize_llm_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    config = {**DEFAULT_LLM_CONFIG, **(payload or {})}
    provider = str(config.get("provider") or "").strip().lower()
    if provider not in PROVIDERS:
        provider = DEFAULT_LLM_CONFIG["provider"]
    config["provider"] = provider
    config["enabled"] = bool(config.get("enabled"))
    config["api_key"] = str(config.get("api_key") or "")
    config["model"] = str(config.get("model") or "").strip() or PROVIDERS[provider]["default_model"]
    config["base_url"] = str(config.get("base_url") or "").strip() or PROVIDERS[provider]["base_url"]
    try:
        config["timeout_seconds"] = max(5, int(config.get("timeout_seconds")))
    except (TypeError, ValueError):
        config["timeout_seconds"] = DEFAULT_TIMEOUT_SECONDS
    return config


def load_llm_config() -> dict[str, Any]:
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            return normalize_llm_config(json.load(file))
    except (OSError, ValueError):
        return normalize_llm_config(None)


def save_llm_config(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_llm_config()
    incoming = dict(payload or {})
    if incoming.get("api_key") == "******":
        incoming["api_key"] = current.get("api_key", "")
    config = normalize_llm_config(incoming)
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return config


def masked_llm_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    masked = dict(config or load_llm_config())
    masked["key_set"] = bool(masked.get("api_key"))
    masked["api_key"] = "******" if masked.get("api_key") else ""
    masked["providers"] = {
        name: {key: info[key] for key in ("label", "base_url", "default_model")}
        for name, info in PROVIDERS.items()
    }
    return masked


def build_request(
    config: dict[str, Any],
    system: str,
    user_text: str,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Return (url, headers, payload) for the configured provider. Pure."""
    protocol = PROVIDERS[config["provider"]]["protocol"]
    model = config["model"]
    base_url = config["base_url"].rstrip("/")
    if protocol == "openai":
        return (
            base_url,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config['api_key']}",
            },
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
            },
        )
    if protocol == "anthropic":
        return (
            base_url,
            {
                "Content-Type": "application/json",
                "x-api-key": config["api_key"],
                "anthropic-version": "2023-06-01",
            },
            {
                "model": model,
                "max_tokens": ANTHROPIC_MAX_TOKENS,
                "system": system,
                "messages": [{"role": "user", "content": user_text}],
            },
        )
    if protocol == "gemini":
        return (
            f"{base_url}/models/{model}:generateContent",
            {
                "Content-Type": "application/json",
                "x-goog-api-key": config["api_key"],
            },
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user_text}]}],
                "generationConfig": {"maxOutputTokens": GEMINI_MAX_TOKENS},
            },
        )
    raise ValueError(f"unknown protocol: {protocol}")


def parse_response(config: dict[str, Any], body: dict[str, Any]) -> str:
    """Extract the answer text from a provider response. Pure."""
    protocol = PROVIDERS[config["provider"]]["protocol"]
    if protocol == "openai":
        choices = body.get("choices") or []
        if not choices:
            raise ValueError(f"响应无 choices: {_short(body)}")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise ValueError(f"响应无内容: {_short(body)}")
        return str(content).strip()
    if protocol == "anthropic":
        blocks = body.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if block.get("type") == "text"
        ).strip()
        if not text:
            raise ValueError(f"响应无文本块: {_short(body)}")
        return text
    if protocol == "gemini":
        candidates = body.get("candidates") or []
        if not candidates:
            raise ValueError(f"响应无 candidates: {_short(body)}")
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise ValueError(f"响应无文本: {_short(body)}")
        return text
    raise ValueError(f"unknown protocol: {protocol}")


def _short(body: Any, limit: int = 200) -> str:
    text = json.dumps(body, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode("utf-8", "replace")[:300]
        except OSError:
            pass
        raise RuntimeError(f"HTTP {error.code}: {detail or error.reason}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"网络错误: {error.reason}") from error


def chat_completion(
    config: dict[str, Any],
    system: str,
    user_text: str,
    post: Callable[[str, dict[str, str], dict[str, Any], int], dict[str, Any]] = _post_json,
) -> str:
    """One grounded question -> answer text. Raises on any failure so the
    caller can fall back to the rule-based responder."""
    if not config.get("api_key"):
        raise RuntimeError("未配置 API Key")
    url, headers, payload = build_request(config, system, user_text)
    body = post(url, headers, payload, config["timeout_seconds"])
    return parse_response(config, body)


def test_llm(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Connectivity check for the config dialog: one tiny real request."""
    config = normalize_llm_config(payload or load_llm_config())
    if payload and payload.get("api_key") == "******":
        config["api_key"] = load_llm_config().get("api_key", "")
    import time as _time

    start = _time.perf_counter()
    text = chat_completion(config, "你是连通性测试助手。", "请只回复两个字：正常")
    return {
        "ok": True,
        "provider": config["provider"],
        "model": config["model"],
        "latency_ms": round((_time.perf_counter() - start) * 1000),
        "reply": text[:80],
    }
