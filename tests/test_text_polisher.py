from __future__ import annotations

import asyncio
import logging
import time

import requests

from doubaoime_asr.agent.config import AgentConfig, POLISH_MODE_LIGHT, POLISH_MODE_OFF, POLISH_MODE_OLLAMA
from doubaoime_asr.agent.text_polisher import PolishResult, TextPolisher, apply_light_polish


class _Response:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")

    def json(self) -> dict[str, object]:
        return self._payload


def test_apply_light_polish_removes_fillers_and_punctuation_noise():
    assert apply_light_polish("嗯 这个 这个方案可以啊") == "这个方案可以。"


def test_polish_off_returns_raw_text():
    polisher = TextPolisher(logging.getLogger("polisher-test"), AgentConfig(polish_mode=POLISH_MODE_OFF))

    result = asyncio.run(polisher.polish("原文"))

    assert result == PolishResult(text="原文", applied_mode=POLISH_MODE_OFF, latency_ms=result.latency_ms)


def test_polish_light_uses_local_rules():
    polisher = TextPolisher(logging.getLogger("polisher-test"), AgentConfig(polish_mode=POLISH_MODE_LIGHT))

    result = asyncio.run(polisher.polish("嗯 我觉得这个这个方案可以啊"))

    assert result.applied_mode == POLISH_MODE_LIGHT
    assert result.text == "我觉得这个方案可以。"


def test_polish_ollama_success(monkeypatch):
    polisher = TextPolisher(
        logging.getLogger("polisher-test"),
        AgentConfig(polish_mode=POLISH_MODE_OLLAMA, ollama_model="qwen2.5:3b"),
    )

    def fake_post(url, json, timeout):
        assert url.endswith("/api/generate")
        assert json["model"] == "qwen2.5:3b"
        assert json["stream"] is False
        return _Response({"response": "润色结果。"})

    monkeypatch.setattr("doubaoime_asr.agent.text_polisher.requests.post", fake_post)

    result = asyncio.run(polisher.polish("原文"))

    assert result.applied_mode == POLISH_MODE_OLLAMA
    assert result.text == "润色结果。"


def test_polish_ollama_read_timeout_scales_with_polish_timeout(monkeypatch):
    polisher = TextPolisher(
        logging.getLogger("polisher-test"),
        AgentConfig(
            polish_mode=POLISH_MODE_OLLAMA,
            ollama_model="qwen2.5:3b",
            polish_timeout_ms=1500,
        ),
    )
    seen_timeouts: list[tuple[float, float]] = []

    def fake_post(url, json, timeout):
        seen_timeouts.append(timeout)
        return _Response({"response": "润色结果。"})

    monkeypatch.setattr("doubaoime_asr.agent.text_polisher.requests.post", fake_post)

    result = asyncio.run(polisher.polish("原文"))

    assert result.applied_mode == POLISH_MODE_OLLAMA
    assert seen_timeouts == [(0.15, 1.65)]


def test_polish_ollama_timeout_falls_back_to_raw(monkeypatch):
    polisher = TextPolisher(
        logging.getLogger("polisher-test"),
        AgentConfig(
            polish_mode=POLISH_MODE_OLLAMA,
            ollama_model="qwen2.5:3b",
            polish_timeout_ms=10,
        ),
    )

    def slow_polish(_text, _config):
        time.sleep(0.2)
        return "不会被用到"

    monkeypatch.setattr(polisher, "_polish_ollama_sync", slow_polish)

    result = asyncio.run(polisher.polish("原文"))

    assert result.applied_mode == "raw_fallback"
    assert result.fallback_reason == "timeout"
    assert result.text == "原文"


def test_polish_ollama_without_model_falls_back(monkeypatch):
    polisher = TextPolisher(
        logging.getLogger("polisher-test"),
        AgentConfig(polish_mode=POLISH_MODE_OLLAMA, ollama_model=""),
    )

    monkeypatch.setattr("doubaoime_asr.agent.text_polisher.requests.get", lambda *args, **kwargs: _Response({"models": []}))

    result = asyncio.run(polisher.polish("原文"))

    assert result.applied_mode == "raw_fallback"
    assert result.fallback_reason == "no_model"
    assert result.text == "原文"


def test_polish_ollama_retries_model_detection_after_no_model(monkeypatch):
    polisher = TextPolisher(
        logging.getLogger("polisher-test"),
        AgentConfig(polish_mode=POLISH_MODE_OLLAMA, ollama_model=""),
    )
    tag_payloads = [
        {"models": []},
        {"models": [{"name": "qwen2.5:3b"}]},
    ]
    seen_get_calls: list[str] = []

    def fake_get(url, timeout):
        seen_get_calls.append(url)
        return _Response(tag_payloads.pop(0))

    def fake_post(url, json, timeout):
        return _Response({"response": f"{json['model']}::润色结果"})

    monkeypatch.setattr("doubaoime_asr.agent.text_polisher.requests.get", fake_get)
    monkeypatch.setattr("doubaoime_asr.agent.text_polisher.requests.post", fake_post)

    first = asyncio.run(polisher.polish("原文"))
    second = asyncio.run(polisher.polish("原文"))

    assert first.applied_mode == "raw_fallback"
    assert first.fallback_reason == "no_model"
    assert second.applied_mode == POLISH_MODE_OLLAMA
    assert second.text == "qwen2.5:3b::润色结果"
    assert len(seen_get_calls) == 2


def test_warmup_uses_unique_local_model(monkeypatch):
    polisher = TextPolisher(
        logging.getLogger("polisher-test"),
        AgentConfig(polish_mode=POLISH_MODE_OLLAMA, ollama_model=""),
    )

    warmup_payloads: list[dict[str, object]] = []

    monkeypatch.setattr(
        "doubaoime_asr.agent.text_polisher.requests.get",
        lambda *args, **kwargs: _Response({"models": [{"name": "my-translator:latest"}]}),
    )

    def fake_post(url, json, timeout):
        warmup_payloads.append(json)
        return _Response({"done": True})

    monkeypatch.setattr("doubaoime_asr.agent.text_polisher.requests.post", fake_post)

    warmed = asyncio.run(polisher.warmup())

    assert warmed is True
    assert warmup_payloads[0]["model"] == "my-translator:latest"
