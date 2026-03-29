import sys

from doubaoime_asr.agent import win_privileges


def test_filter_relaunch_args_drops_worker_only_flags():
    filtered = win_privileges.filter_relaunch_args(
        ["--console", "--worker", "--worker-log-path", "worker.log", "--mode", "inject"]
    )

    assert filtered == ["--console", "--mode", "inject"]


def test_build_admin_relaunch_command_python_mode():
    executable, params = win_privileges.build_admin_relaunch_command(
        ["--console", "--mode", "inject"],
        executable=r"C:\Python312\python.exe",
        frozen=False,
    )

    assert executable == r"C:\Python312\python.exe"
    assert params == '-m doubaoime_asr.agent.stable_main --console --mode inject'


def test_build_admin_relaunch_command_frozen_mode():
    executable, params = win_privileges.build_admin_relaunch_command(
        ["--console", "--mode", "inject"],
        executable=r"C:\Apps\doubao-voice-agent.exe",
        frozen=True,
    )

    assert executable == r"C:\Apps\doubao-voice-agent.exe"
    assert params == '--console --mode inject'


def test_restart_as_admin_uses_runas_shell_execute(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    def fake_shell_execute(executable: str, params: str, cwd: str | None) -> int:
        calls.append((executable, params, cwd))
        return 64

    monkeypatch.setattr(win_privileges, "_shell_execute_runas", fake_shell_execute)

    restarted = win_privileges.restart_as_admin(
        ["--console"],
        executable=sys.executable,
        frozen=False,
        cwd=r"C:\Work",
    )

    assert restarted is True
    assert calls == [
        (
            sys.executable,
            '-m doubaoime_asr.agent.stable_main --console',
            r"C:\Work",
        )
    ]
