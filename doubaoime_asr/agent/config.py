from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import sys
from typing import Any


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
    microphone_device: int | str | None = None
    credential_path: str | None = None
    render_debounce_ms: int = 80

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
        return cls(**data)

    def save(self, path: str | Path | None = None) -> Path:
        config_path = Path(path) if path is not None else self.default_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return config_path
