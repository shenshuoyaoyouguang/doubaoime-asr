from pathlib import Path
import sys

from doubaoime_asr.agent.config import AgentConfig, discover_preferred_credential_path


def test_agent_config_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    config = AgentConfig(
        hotkey="f9",
        microphone_device="USB Mic",
        credential_path=str(tmp_path / "credentials.json"),
        render_debounce_ms=120,
    )

    config.save(path)
    loaded = AgentConfig.load(path)

    assert loaded == config


def test_agent_config_creates_default_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    loaded = AgentConfig.load()

    assert Path(loaded.default_path()).exists()
    assert AgentConfig.default_log_path() == tmp_path / "DoubaoVoiceInput" / "logs" / "agent.log"


def test_discover_preferred_credential_path_prefers_cwd(tmp_path: Path, monkeypatch):
    cred = tmp_path / "credentials.json"
    cred.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    assert discover_preferred_credential_path() == cred


def test_agent_config_load_migrates_old_default_credential_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_cred = workspace / "credentials.json"
    repo_cred.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    config_path = tmp_path / "custom-config.json"
    old_default = str(Path(tmp_path / "appdata") / "DoubaoVoiceInput" / "credentials.json")
    config_path.write_text(
        f'{{"hotkey":"f8","microphone_device":null,"credential_path":"{old_default.replace("\\", "\\\\")}","render_debounce_ms":80}}',
        encoding="utf-8",
    )

    loaded = AgentConfig.load(config_path)

    assert loaded.credential_path == str(repo_cred)
