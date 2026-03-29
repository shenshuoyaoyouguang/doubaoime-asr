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
SUPPORTED_MODES = ("inject", "recognize")


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
    hotkey: str = "f8"
    hotkey_vk: int | None = None
    hotkey_display: str | None = None
    mode: str = "inject"
    microphone_device: int | str | None = None
    credential_path: str | None = None
    injection_policy: str = INJECTION_POLICY_DIRECT_THEN_CLIPBOARD
    render_debounce_ms: int = 80
    overlay_render_fps: int = 30
    overlay_font_size: int = 14
    overlay_max_width: int = 620
    overlay_opacity_percent: int = 92
    overlay_bottom_offset: int = 120
    overlay_animation_ms: int = 150

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

        data: dict[str, Any] = asdict(base)
        for key in data:
            if key in payload:
                data[key] = payload[key]
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
        return cls(**data)

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
        display = str(hotkey_display).strip() if isinstance(hotkey_display, str) and hotkey_display.strip() else vk_to_display(vk)
        legacy_hotkey = vk_to_hotkey(vk) or normalize_hotkey(display)
        return legacy_hotkey, vk, display
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
