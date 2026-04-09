from __future__ import annotations

import time

from doubaoime_asr.asr import ASRProbeResult, probe_asr_session
from doubaoime_asr.config import ASRConfig


class ASRPreflightGate:
    """ASR 会话前预检，带短 TTL 成功缓存。"""

    def __init__(self, logger, *, cache_ttl_ms: int = 15000) -> None:
        self._logger = logger
        self._cache_ttl_ms = cache_ttl_ms
        self._last_ok_at: float | None = None
        self._last_result: ASRProbeResult | None = None

    async def ensure_available(self, credential_path: str | None, auto_rotate_device: bool = False) -> ASRProbeResult:
        now = time.monotonic()
        if (
            self._last_result is not None
            and self._last_result.ok
            and self._last_ok_at is not None
            and (now - self._last_ok_at) * 1000 < self._cache_ttl_ms
        ):
            self._logger.info(
                "asr_preflight_cache_hit ttl_ms=%s age_ms=%s",
                self._cache_ttl_ms,
                int((now - self._last_ok_at) * 1000),
            )
            return self._last_result

        self._logger.info("asr_preflight_started")
        result = await probe_asr_session(ASRConfig(
            credential_path=credential_path,
            auto_rotate_device=auto_rotate_device
        ))
        if result.ok:
            self._last_ok_at = now
            self._last_result = result
            self._logger.info("asr_preflight_ok latency_ms=%s", result.latency_ms)
            return result

        self.invalidate()
        self._logger.warning(
            "asr_preflight_failed stage=%s message=%s latency_ms=%s",
            result.stage,
            result.message,
            result.latency_ms,
        )
        return result

    def invalidate(self) -> None:
        self._last_ok_at = None
        self._last_result = None
