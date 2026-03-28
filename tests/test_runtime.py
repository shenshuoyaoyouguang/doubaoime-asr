import os
from pathlib import Path

from doubaoime_asr import _runtime


def test_ensure_opus_runtime_adds_windows_dll_dir_once(
    monkeypatch,
    tmp_path: Path,
):
    dll_dir = tmp_path / "vendor"
    dll_dir.mkdir()
    (dll_dir / "opus.dll").write_bytes(b"")

    added_dirs: list[str] = []

    monkeypatch.setattr(_runtime.sys, "platform", "win32")
    monkeypatch.setattr(_runtime, "_candidate_opus_dirs", lambda: [dll_dir])
    monkeypatch.setenv("PATH", r"C:\Windows\System32")
    monkeypatch.setattr(
        _runtime.os,
        "add_dll_directory",
        lambda directory: added_dirs.append(directory) or directory,
        raising=False,
    )
    _runtime._DLL_DIRECTORY_HANDLES.clear()

    _runtime.ensure_opus_runtime()
    _runtime.ensure_opus_runtime()

    path_entries = os.environ["PATH"].split(os.pathsep)
    assert path_entries[0] == str(dll_dir)
    assert path_entries.count(str(dll_dir)) == 1
    assert added_dirs == [str(dll_dir)]
