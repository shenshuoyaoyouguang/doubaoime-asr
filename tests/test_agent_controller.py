import sys
import types


if "pywinauto" not in sys.modules:
    pywinauto_stub = types.ModuleType("pywinauto")
    pywinauto_stub.Desktop = object
    keyboard_stub = types.ModuleType("pywinauto.keyboard")
    keyboard_stub.send_keys = lambda *args, **kwargs: None
    sys.modules["pywinauto"] = pywinauto_stub
    sys.modules["pywinauto.keyboard"] = keyboard_stub

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.stable_simple_app import StableVoiceInputApp


def test_build_worker_command_python_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Python312\python.exe")

    config = AgentConfig(hotkey="f8", credential_path=r"C:\tmp\cred.json")
    app = StableVoiceInputApp(config)

    command = app._build_worker_command()

    assert command[:3] == [r"C:\Python312\python.exe", "-m", "doubaoime_asr.agent.stable_main"]
    assert "--worker" in command
    assert "--credential-path" in command


def test_build_worker_command_frozen_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\doubao-voice-agent.exe")

    config = AgentConfig(hotkey="f8", credential_path=r"C:\tmp\cred.json")
    app = StableVoiceInputApp(config)

    command = app._build_worker_command()

    assert command[0] == r"C:\Apps\doubao-voice-agent.exe"
    assert command[1] == "--worker"
