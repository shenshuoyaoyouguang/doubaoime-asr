from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import logging
import re
import time
from typing import Final

import requests

from .config import (
    AgentConfig,
    DEFAULT_OLLAMA_KEEP_ALIVE,
    DEFAULT_OLLAMA_PROMPT_TEMPLATE,
    POLISH_MODE_LIGHT,
    POLISH_MODE_OFF,
    POLISH_MODE_OLLAMA,
)


_OLLAMA_WARMUP_TIMEOUT_S: Final[float] = 2.5
_OLLAMA_CONNECT_TIMEOUT_S: Final[float] = 0.15
_OLLAMA_MIN_READ_TIMEOUT_S: Final[float] = 0.6
_OLLAMA_READ_TIMEOUT_GRACE_S: Final[float] = 0.15
_OLLAMA_TAGS_CONNECT_TIMEOUT_S: Final[float] = 0.15
_OLLAMA_TAGS_READ_TIMEOUT_S: Final[float] = 0.4
_WARMUP_CONNECT_TIMEOUT_S: Final[float] = 0.5
_WARMUP_READ_TIMEOUT_S: Final[float] = 1.5
_OLLAMA_OPTIONS: Final[dict[str, float | int]] = {
    "temperature": 0.2,
    "top_p": 0.9,
    "num_predict": 128,
}
_FILLER_PREFIX_RE = re.compile(
    r"(^|[，。！？；、\s])(?:嗯+|呃+|额+|啊+|那个|就是)(?=[，。！？；、\s]+)",
)
_FILLER_SUFFIX_RE = re.compile(r"(?:啊+|呢|吧)(?=$|[。！？；\n])")
_REPEATED_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])([一-龥A-Za-z0-9]{1,8})(?:[，,\s]+\1){1,}(?![A-Za-z0-9])",
)
_REPEATED_FILLER_PHRASE_RE = re.compile(r"(这个|那个|就是|然后)\1+")
_SPACE_AROUND_CJK_PUNCT_RE = re.compile(r"\s*([，。！？；：、])\s*")
_TRAILING_SENTENCE_MARK_RE = re.compile(r"[。！？；]$")
_CJK_CHAR_RE = re.compile(r"[一-龥]")
_COMMAND_PREFIX_RE = re.compile(
    r"^(?:git|npm|pnpm|yarn|pip|python|py|node|npx|cd|ls|dir|cp|mv|rm|mkdir)(?:\s|$)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\?)*")
_UNIX_PATH_RE = re.compile(r"(?:^|[\s(])(?:~?/|/)[^\s]+")
_FILENAME_RE = re.compile(r"\b[\w.-]+\.(?:json|py|md|yaml|yml|toml|exe)\b", re.IGNORECASE)
_SHORTCUT_RE = re.compile(r"\b[A-Za-z]{1,10}(?:\s*\+\s*[A-Za-z]{1,10})+\b")
_SEARCH_LIKE_SUFFIXES: Final[tuple[str, ...]] = (
    "设计",
    "教程",
    "方案",
    "文档",
    "配置",
    "指南",
    "天气",
)


class OllamaUnavailableError(RuntimeError):
    """Ollama 本地服务不可用。"""


class OllamaInvalidResponseError(RuntimeError):
    """Ollama 返回了不可用结果。"""


class OllamaNoModelError(RuntimeError):
    """未配置或无法自动探测可用模型。"""


class OllamaBadPromptError(RuntimeError):
    """润色提示词配置不合法。"""


@dataclass(slots=True)
class PolishResult:
    text: str
    applied_mode: str
    latency_ms: int
    fallback_reason: str | None = None


def _looks_like_technical_text(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if _URL_RE.search(candidate):
        return True
    if _WINDOWS_PATH_RE.search(candidate):
        return True
    if _UNIX_PATH_RE.search(candidate):
        return True
    if _FILENAME_RE.search(candidate):
        return True
    if _COMMAND_PREFIX_RE.search(candidate):
        return True
    if _SHORTCUT_RE.search(candidate):
        return True
    if "--" in candidate:
        return True
    return False


def _looks_like_search_fragment(text: str) -> bool:
    candidate = text.strip()
    if not candidate or "\n" in candidate:
        return False
    if _looks_like_technical_text(candidate):
        return True
    if not _CJK_CHAR_RE.search(candidate):
        return True
    if len(candidate) <= 4:
        return True
    return any(candidate.endswith(suffix) for suffix in _SEARCH_LIKE_SUFFIXES)


def _should_append_period(text: str) -> bool:
    candidate = text.strip()
    if not candidate or "\n" in candidate:
        return False
    if _TRAILING_SENTENCE_MARK_RE.search(candidate):
        return False
    if _looks_like_search_fragment(candidate):
        return False
    return True


def apply_light_polish(text: str) -> str:
    candidate = text.strip()
    if not candidate:
        return ""

    candidate = re.sub(r"[ \t]+", " ", candidate)
    candidate = re.sub(r"\s*\n\s*", "\n", candidate)
    candidate = _FILLER_PREFIX_RE.sub(r"\1", candidate)
    candidate = re.sub(r"([。！？；]?)\s*(啊+|呢|吧)$", r"\1", candidate)
    candidate = re.sub(r"(^|[\n。！？；])\s+", r"\1", candidate)
    candidate = _REPEATED_TOKEN_RE.sub(r"\1", candidate)
    candidate = _REPEATED_FILLER_PHRASE_RE.sub(r"\1", candidate)
    candidate = candidate.replace("...", "……")
    candidate = re.sub(r"([，。！？；：、])\1+", r"\1", candidate)
    candidate = _SPACE_AROUND_CJK_PUNCT_RE.sub(r"\1", candidate)
    candidate = re.sub(r"\s{2,}", " ", candidate)
    candidate = candidate.strip(" ，。！？；：、")
    candidate = _FILLER_SUFFIX_RE.sub("", candidate)

    if not candidate:
        return text.strip()
    if _should_append_period(candidate):
        candidate = f"{candidate}。"
    return candidate


class TextPolisher:
    def __init__(self, logger: logging.Logger, config: AgentConfig) -> None:
        self._logger = logger
        self._config = replace(config)
        self._runtime_ollama_model: str | None = None

    def configure(self, config: AgentConfig) -> None:
        current_key = (
            self._config.ollama_base_url,
            self._config.ollama_model,
        )
        next_key = (
            config.ollama_base_url,
            config.ollama_model,
        )
        self._config = replace(config)
        if current_key != next_key:
            self._runtime_ollama_model = None

    async def polish(self, text: str) -> PolishResult:
        raw_text = text or ""
        config = replace(self._config)
        start_time = time.perf_counter()

        if config.polish_mode == POLISH_MODE_OFF or not raw_text.strip():
            return self._result(raw_text, config.polish_mode, start_time)

        if config.polish_mode == POLISH_MODE_LIGHT:
            polished = apply_light_polish(raw_text)
            return self._result(polished or raw_text, POLISH_MODE_LIGHT, start_time)

        if config.polish_mode != POLISH_MODE_OLLAMA:
            return self._fallback(raw_text, start_time, "invalid_mode")

        try:
            overall_timeout_s = self._polish_timeout_seconds(config)
            polished = await asyncio.wait_for(
                asyncio.to_thread(self._polish_ollama_sync, raw_text, config),
                timeout=overall_timeout_s,
            )
        except asyncio.TimeoutError:
            self._logger.warning("text_polish_timeout timeout_ms=%s", config.polish_timeout_ms)
            return self._fallback(raw_text, start_time, "timeout")
        except OllamaBadPromptError:
            self._logger.exception("text_polish_bad_prompt")
            return self._fallback(raw_text, start_time, "bad_prompt")
        except OllamaNoModelError:
            self._logger.warning("text_polish_no_model")
            return self._fallback(raw_text, start_time, "no_model")
        except OllamaUnavailableError:
            self._logger.warning("text_polish_unavailable")
            return self._fallback(raw_text, start_time, "unavailable")
        except OllamaInvalidResponseError:
            self._logger.warning("text_polish_invalid_response")
            return self._fallback(raw_text, start_time, "invalid_response")
        except Exception:
            self._logger.exception("text_polish_failed_unexpected")
            return self._fallback(raw_text, start_time, "unavailable")

        return self._result(polished, POLISH_MODE_OLLAMA, start_time)

    async def warmup(self) -> bool:
        config = replace(self._config)
        if config.polish_mode != POLISH_MODE_OLLAMA or not config.ollama_warmup_enabled:
            return False
        return await asyncio.wait_for(
            asyncio.to_thread(self._warmup_sync, config),
            timeout=_OLLAMA_WARMUP_TIMEOUT_S,
        )

    def _result(self, text: str, applied_mode: str, start_time: float) -> PolishResult:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return PolishResult(text=text, applied_mode=applied_mode, latency_ms=latency_ms)

    def _fallback(self, raw_text: str, start_time: float, reason: str) -> PolishResult:
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        return PolishResult(
            text=raw_text,
            applied_mode="raw_fallback",
            latency_ms=latency_ms,
            fallback_reason=reason,
        )

    def _polish_ollama_sync(self, text: str, config: AgentConfig) -> str:
        model_name = self._resolve_ollama_model_sync(config)
        if not model_name:
            raise OllamaNoModelError("no ollama model available")

        prompt = self._render_prompt(text, config.ollama_prompt_template)
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "keep_alive": config.ollama_keep_alive or DEFAULT_OLLAMA_KEEP_ALIVE,
            "options": dict(_OLLAMA_OPTIONS),
        }
        response = self._post_json(
            f"{config.ollama_base_url}/api/generate",
            payload,
            timeout=self._ollama_generate_timeout(config),
        )
        result_text = str(response.get("response", "")).strip()
        if not result_text:
            raise OllamaInvalidResponseError("empty response")
        return result_text

    def _warmup_sync(self, config: AgentConfig) -> bool:
        model_name = self._resolve_ollama_model_sync(config)
        if not model_name:
            self._logger.info("text_polisher_warmup_skipped reason=no_model")
            return False
        payload = {
            "model": model_name,
            "prompt": "",
            "stream": False,
            "keep_alive": config.ollama_keep_alive or DEFAULT_OLLAMA_KEEP_ALIVE,
        }
        self._post_json(
            f"{config.ollama_base_url}/api/generate",
            payload,
            timeout=(_WARMUP_CONNECT_TIMEOUT_S, _WARMUP_READ_TIMEOUT_S),
        )
        return True

    def _resolve_ollama_model_sync(self, config: AgentConfig) -> str | None:
        configured_model = config.ollama_model.strip()
        if configured_model:
            return configured_model
        if self._runtime_ollama_model:
            return self._runtime_ollama_model

        response = self._get_json(
            f"{config.ollama_base_url}/api/tags",
            timeout=(_OLLAMA_TAGS_CONNECT_TIMEOUT_S, _OLLAMA_TAGS_READ_TIMEOUT_S),
        )
        models = response.get("models")
        if not isinstance(models, list):
            raise OllamaInvalidResponseError("invalid tags payload")
        if len(models) == 1:
            model_name = str(models[0].get("name", "")).strip()
            if not model_name:
                raise OllamaInvalidResponseError("missing model name")
            self._runtime_ollama_model = model_name
            self._logger.info("text_polisher_runtime_model_detected model=%s", model_name)
            return model_name

        return None

    def _render_prompt(self, text: str, template: str) -> str:
        prompt_template = template.strip() if isinstance(template, str) else ""
        if not prompt_template:
            prompt_template = DEFAULT_OLLAMA_PROMPT_TEMPLATE
        if "{text}" not in prompt_template:
            self._logger.warning("text_polisher_prompt_missing_placeholder")
            prompt_template = DEFAULT_OLLAMA_PROMPT_TEMPLATE
            if "{text}" not in prompt_template:
                raise OllamaBadPromptError("default prompt template is invalid")
        try:
            return prompt_template.format(text=text.strip())
        except Exception as exc:
            self._logger.warning("text_polisher_prompt_format_failed", exc_info=exc)
            try:
                return DEFAULT_OLLAMA_PROMPT_TEMPLATE.format(text=text.strip())
            except Exception as fallback_exc:  # pragma: no cover - impossible without code regression
                raise OllamaBadPromptError("failed to render prompt") from fallback_exc

    def _polish_timeout_seconds(self, config: AgentConfig) -> float:
        return max(0.1, config.polish_timeout_ms / 1000.0)

    def _ollama_generate_timeout(self, config: AgentConfig) -> tuple[float, float]:
        read_timeout_s = max(
            _OLLAMA_MIN_READ_TIMEOUT_S,
            self._polish_timeout_seconds(config) + _OLLAMA_READ_TIMEOUT_GRACE_S,
        )
        return (_OLLAMA_CONNECT_TIMEOUT_S, read_timeout_s)

    def _post_json(
        self,
        url: str,
        payload: dict[str, object],
        *,
        timeout: tuple[float, float],
    ) -> dict[str, object]:
        try:
            response = requests.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise OllamaUnavailableError(str(exc)) from exc
        except ValueError as exc:
            raise OllamaInvalidResponseError("response is not json") from exc
        if not isinstance(data, dict):
            raise OllamaInvalidResponseError("response is not an object")
        return data

    def _get_json(self, url: str, *, timeout: tuple[float, float]) -> dict[str, object]:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            raise OllamaUnavailableError(str(exc)) from exc
        except ValueError as exc:
            raise OllamaInvalidResponseError("response is not json") from exc
        if not isinstance(data, dict):
            raise OllamaInvalidResponseError("response is not an object")
        return data
