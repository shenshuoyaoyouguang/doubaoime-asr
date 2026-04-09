from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any

from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey


INJECTION_POLICY_DIRECT_ONLY = "direct_only"
INJECTION_POLICY_DIRECT_THEN_CLIPBOARD = "direct_then_clipboard"
SUPPORTED_INJECTION_POLICIES = (
    INJECTION_POLICY_DIRECT_ONLY,
    INJECTION_POLICY_DIRECT_THEN_CLIPBOARD,
)
STREAMING_TEXT_MODE_SAFE_INLINE = "safe_inline"
STREAMING_TEXT_MODE_OVERLAY_ONLY = "overlay_only"
SUPPORTED_STREAMING_TEXT_MODES = (
    STREAMING_TEXT_MODE_SAFE_INLINE,
    STREAMING_TEXT_MODE_OVERLAY_ONLY,
)
FINAL_COMMIT_SOURCE_RAW = "raw"
FINAL_COMMIT_SOURCE_POLISHED = "polished"
SUPPORTED_FINAL_COMMIT_SOURCES = (
    FINAL_COMMIT_SOURCE_RAW,
    FINAL_COMMIT_SOURCE_POLISHED,
)
POLISH_MODE_OFF = "off"
POLISH_MODE_LIGHT = "light"
POLISH_MODE_OLLAMA = "ollama"
SUPPORTED_POLISH_MODES = (
    POLISH_MODE_OFF,
    POLISH_MODE_LIGHT,
    POLISH_MODE_OLLAMA,
)
CAPTURE_OUTPUT_POLICY_OFF = "off"
CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT = "mute_system_output"
SUPPORTED_CAPTURE_OUTPUT_POLICIES = (
    CAPTURE_OUTPUT_POLICY_OFF,
    CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT,
)
CURRENT_CONFIG_VERSION = 2
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen35-opus-fixed:latest"
DEFAULT_OLLAMA_KEEP_ALIVE = "15m"
DEFAULT_OLLAMA_PROMPT_TEMPLATE = """润色下面这句语音识别文本：保留原意、数字和专有名词；删除口头语和重复；补上自然标点；只输出结果。
{text}
"""
SUPPORTED_MODES = ("inject", "recognize")
DEFAULT_AGENT_HOTKEY = "right_ctrl"
DEFAULT_AGENT_HOTKEY_VK = vk_from_hotkey(DEFAULT_AGENT_HOTKEY)
DEFAULT_AGENT_HOTKEY_DISPLAY = vk_to_display(DEFAULT_AGENT_HOTKEY_VK)


def default_agent_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "DoubaoVoiceInput"
    return Path.home() / ".doubao-voice-input"


def default_log_dir() -> Path:
    return default_agent_dir() / "logs"


def _credential_candidates() -> list[Path]:
    candidates: list[Path] = []
    cwd = Path.cwd()
    candidates.append(cwd / "credentials.json")

    executable = Path(sys.executable).resolve()
    candidates.append(executable.parent / "credentials.json")
    candidates.append(executable.parent.parent / "credentials.json")

    package_file = Path(__file__).resolve()
    for parent in package_file.parents:
        candidates.append(parent / "credentials.json")

    seen: set[str] = set()
    deduped: list[Path] = []
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def discover_preferred_credential_path() -> Path | None:
    for candidate in _credential_candidates():
        if candidate.exists():
            return candidate
    return None


@dataclass(slots=True)
class AgentConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    hotkey: str = DEFAULT_AGENT_HOTKEY
    hotkey_vk: int | None = DEFAULT_AGENT_HOTKEY_VK
    hotkey_display: str | None = DEFAULT_AGENT_HOTKEY_DISPLAY
    mode: str = "inject"
    microphone_device: int | str | None = None
    credential_path: str | None = None
    injection_policy: str = INJECTION_POLICY_DIRECT_THEN_CLIPBOARD
    streaming_text_mode: str = STREAMING_TEXT_MODE_SAFE_INLINE
    final_commit_source: str = FINAL_COMMIT_SOURCE_POLISHED
    capture_output_policy: str = CAPTURE_OUTPUT_POLICY_OFF
    render_debounce_ms: int = 80
    overlay_render_fps: int = 60
    overlay_font_size: int = 14
    overlay_max_width: int = 620
    overlay_opacity_percent: int = 92
    overlay_bottom_offset: int = 120
    overlay_animation_ms: int = 80
    worker_ready_timeout_ms: int = 2500
    worker_cold_ready_timeout_ms: int = 5000
    worker_exit_grace_timeout_ms: int = 2000
    worker_kill_wait_timeout_ms: int = 2000
    polish_mode: str = POLISH_MODE_LIGHT
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    polish_timeout_ms: int = 800
    ollama_warmup_enabled: bool = True
    ollama_keep_alive: str = DEFAULT_OLLAMA_KEEP_ALIVE
    ollama_prompt_template: str = DEFAULT_OLLAMA_PROMPT_TEMPLATE
    auto_rotate_device: bool = False
    """每次启动时自动注册新的设备ID，用于规避 ExceededConcurrentQuota 错误"""

    @classmethod
    def default_dir(cls) -> Path:
        return default_agent_dir()

    @classmethod
    def default_path(cls) -> Path:
        return cls.default_dir() / "config.json"

    @classmethod
    def default_log_dir(cls) -> Path:
        return default_log_dir()

    @classmethod
    def default_log_path(cls) -> Path:
        return default_log_dir() / "agent.log"

    @classmethod
    def default_controller_log_path(cls) -> Path:
        return default_log_dir() / "controller.log"

    @classmethod
    def default_overlay_log_path(cls) -> Path:
        return default_log_dir() / "overlay.log"

    @classmethod
    def default_worker_log_dir(cls) -> Path:
        return default_log_dir() / "workers"

    @classmethod
    def default(cls) -> "AgentConfig":
        root = cls.default_dir()
        credential_path = discover_preferred_credential_path()
        return cls(
            credential_path=str(credential_path or (root / "credentials.json")),
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AgentConfig":
        config_path = Path(path) if path is not None else cls.default_path()
        base = cls.default()
        fallback_default_path = str(cls.default_dir() / "credentials.json")

        if not config_path.exists():
            base.save(config_path)
            return base

        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return base
        if not isinstance(payload, dict):
            return base

        data: dict[str, Any] = asdict(base)
        for key in data:
            if key in payload:
                data[key] = payload[key]
        config_version = _coerce_config_version(payload.get("config_version"))
        data["config_version"] = max(config_version, CURRENT_CONFIG_VERSION)
        migrated_hotkey_defaults = config_version < CURRENT_CONFIG_VERSION
        if migrated_hotkey_defaults:
            data["hotkey"] = DEFAULT_AGENT_HOTKEY
            data["hotkey_vk"] = DEFAULT_AGENT_HOTKEY_VK
            data["hotkey_display"] = DEFAULT_AGENT_HOTKEY_DISPLAY
        if data.get("credential_path") == fallback_default_path and base.credential_path != fallback_default_path:
            data["credential_path"] = base.credential_path
        hotkey_value, hotkey_vk, hotkey_display = _sanitize_hotkey_model(
            hotkey=data.get("hotkey"),
            hotkey_vk=data.get("hotkey_vk"),
            hotkey_display=data.get("hotkey_display"),
            fallback_hotkey=base.hotkey,
            fallback_vk=base.effective_hotkey_vk(),
            fallback_display=base.effective_hotkey_display(),
        )
        data["hotkey"] = hotkey_value
        data["hotkey_vk"] = hotkey_vk
        data["hotkey_display"] = hotkey_display
        data["mode"] = data.get("mode") if data.get("mode") in SUPPORTED_MODES else base.mode
        data["injection_policy"] = (
            data.get("injection_policy")
            if data.get("injection_policy") in SUPPORTED_INJECTION_POLICIES
            else base.injection_policy
        )
        data["streaming_text_mode"] = (
            data.get("streaming_text_mode")
            if data.get("streaming_text_mode") in SUPPORTED_STREAMING_TEXT_MODES
            else base.streaming_text_mode
        )
        data["final_commit_source"] = (
            data.get("final_commit_source")
            if data.get("final_commit_source") in SUPPORTED_FINAL_COMMIT_SOURCES
            else base.final_commit_source
        )
        data["capture_output_policy"] = (
            data.get("capture_output_policy")
            if data.get("capture_output_policy") in SUPPORTED_CAPTURE_OUTPUT_POLICIES
            else base.capture_output_policy
        )
        data["polish_mode"] = (
            data.get("polish_mode")
            if data.get("polish_mode") in SUPPORTED_POLISH_MODES
            else base.polish_mode
        )
        data["render_debounce_ms"] = _clamp_int(data.get("render_debounce_ms"), base.render_debounce_ms, 0, 1000)
        data["overlay_render_fps"] = _clamp_int(data.get("overlay_render_fps"), base.overlay_render_fps, 1, 120)
        data["overlay_font_size"] = _clamp_int(data.get("overlay_font_size"), base.overlay_font_size, 10, 36)
        data["overlay_max_width"] = _clamp_int(data.get("overlay_max_width"), base.overlay_max_width, 320, 1200)
        data["overlay_opacity_percent"] = _clamp_int(
            data.get("overlay_opacity_percent"),
            base.overlay_opacity_percent,
            35,
            100,
        )
        data["overlay_bottom_offset"] = _clamp_int(
            data.get("overlay_bottom_offset"),
            base.overlay_bottom_offset,
            20,
            500,
        )
        data["overlay_animation_ms"] = _clamp_int(
            data.get("overlay_animation_ms"),
            base.overlay_animation_ms,
            0,
            600,
        )
        data["worker_ready_timeout_ms"] = _clamp_int(
            data.get("worker_ready_timeout_ms"),
            base.worker_ready_timeout_ms,
            500,
            15000,
        )
        data["worker_cold_ready_timeout_ms"] = _clamp_int(
            data.get("worker_cold_ready_timeout_ms"),
            base.worker_cold_ready_timeout_ms,
            1000,
            30000,
        )
        data["worker_cold_ready_timeout_ms"] = max(
            data["worker_cold_ready_timeout_ms"],
            data["worker_ready_timeout_ms"],
        )
        data["worker_exit_grace_timeout_ms"] = _clamp_int(
            data.get("worker_exit_grace_timeout_ms"),
            base.worker_exit_grace_timeout_ms,
            200,
            10000,
        )
        data["worker_kill_wait_timeout_ms"] = _clamp_int(
            data.get("worker_kill_wait_timeout_ms"),
            base.worker_kill_wait_timeout_ms,
            200,
            10000,
        )
        data["ollama_base_url"] = _sanitize_non_empty_text(
            data.get("ollama_base_url"),
            base.ollama_base_url,
            strip_trailing_slash=True,
        )
        data["ollama_model"] = _sanitize_optional_text(data.get("ollama_model"))
        data["polish_timeout_ms"] = _clamp_int(
            data.get("polish_timeout_ms"),
            base.polish_timeout_ms,
            100,
            5000,
        )
        data["ollama_warmup_enabled"] = _coerce_bool(
            data.get("ollama_warmup_enabled"),
            base.ollama_warmup_enabled,
        )
        data["ollama_keep_alive"] = _sanitize_non_empty_text(
            data.get("ollama_keep_alive"),
            base.ollama_keep_alive,
        )
        prompt_template = data.get("ollama_prompt_template")
        data["ollama_prompt_template"] = (
            prompt_template
            if isinstance(prompt_template, str) and prompt_template.strip()
            else base.ollama_prompt_template
        )
        config = cls(**data)
        if migrated_hotkey_defaults:
            try:
                config.save(config_path)
            except OSError:
                pass
        return config

    def save(self, path: str | Path | None = None) -> Path:
        config_path = Path(path) if path is not None else self.default_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return config_path

    def overlay_style_payload(self) -> dict[str, str]:
        return {
            "font_size": str(self.overlay_font_size),
            "max_width": str(self.overlay_max_width),
            "opacity_percent": str(self.overlay_opacity_percent),
            "bottom_offset": str(self.overlay_bottom_offset),
            "animation_ms": str(self.overlay_animation_ms),
        }

    def effective_hotkey_vk(self) -> int:
        if self.hotkey_vk is not None:
            return int(self.hotkey_vk)
        return vk_from_hotkey(self.hotkey)

    def effective_hotkey_display(self) -> str:
        if self.hotkey_display:
            return self.hotkey_display
        if self.hotkey_vk is not None:
            return vk_to_display(int(self.hotkey_vk))
        return vk_to_display(vk_from_hotkey(self.hotkey))

    def worker_ready_timeout_seconds(self, *, cold_start: bool = False) -> float:
        timeout_ms = (
            self.worker_cold_ready_timeout_ms
            if cold_start
            else self.worker_ready_timeout_ms
        )
        return timeout_ms / 1000.0

    def worker_exit_grace_timeout_seconds(self) -> float:
        return self.worker_exit_grace_timeout_ms / 1000.0

    def worker_kill_wait_timeout_seconds(self) -> float:
        return self.worker_kill_wait_timeout_ms / 1000.0


def _sanitize_hotkey(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    normalized = normalize_hotkey(value)
    try:
        vk_from_hotkey(normalized)
    except ValueError:
        return fallback
    return normalized


def _sanitize_hotkey_model(
    *,
    hotkey: Any,
    hotkey_vk: Any,
    hotkey_display: Any,
    fallback_hotkey: str,
    fallback_vk: int,
    fallback_display: str,
) -> tuple[str, int, str]:
    try:
        vk = int(hotkey_vk)
        if vk <= 0:
            raise ValueError
        canonical_hotkey = vk_to_hotkey(vk)
        if canonical_hotkey is not None:
            return canonical_hotkey, vk, vk_to_display(vk)
        display = str(hotkey_display).strip() if isinstance(hotkey_display, str) and hotkey_display.strip() else vk_to_display(vk)
        return normalize_hotkey(display), vk, display
    except (TypeError, ValueError):
        pass

    normalized = _sanitize_hotkey(hotkey, fallback_hotkey)
    try:
        vk = vk_from_hotkey(normalized)
        return normalized, vk, vk_to_display(vk)
    except ValueError:
        return fallback_hotkey, fallback_vk, fallback_display


def _clamp_int(value: Any, fallback: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, number))


def _coerce_config_version(value: Any) -> int:
    try:
        version = int(value)
    except (TypeError, ValueError):
        return 0
    return version if version > 0 else 0


def _sanitize_optional_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _sanitize_non_empty_text(value: Any, fallback: str, *, strip_trailing_slash: bool = False) -> str:
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if strip_trailing_slash:
        text = text.rstrip("/")
    return text or fallback


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return fallback
