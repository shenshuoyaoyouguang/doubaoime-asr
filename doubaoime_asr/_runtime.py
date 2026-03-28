from __future__ import annotations

import os
import sys
from pathlib import Path

_DLL_DIRECTORY_HANDLES: list[object] = []
_OPUS_DLL_NAMES = ("opus.dll", "libopus.dll")


def _candidate_opus_dirs() -> list[Path]:
    package_dir = Path(__file__).resolve().parent
    return [package_dir, package_dir.parent]


def ensure_opus_runtime() -> None:
    if sys.platform != "win32":
        return

    path_value = os.environ.get("PATH", "")
    existing_entries = {
        entry.casefold()
        for entry in path_value.split(os.pathsep)
        if entry
    }

    dirs_to_add: list[str] = []
    for directory in _candidate_opus_dirs():
        if not directory.exists():
            continue
        if not any((directory / dll_name).exists() for dll_name in _OPUS_DLL_NAMES):
            continue

        directory_str = str(directory)
        directory_key = directory_str.casefold()
        if directory_key in existing_entries:
            continue

        dirs_to_add.append(directory_str)
        existing_entries.add(directory_key)

    if not dirs_to_add:
        return

    os.environ["PATH"] = os.pathsep.join(
        [*dirs_to_add, path_value] if path_value else dirs_to_add
    )

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return

    for directory in dirs_to_add:
        _DLL_DIRECTORY_HANDLES.append(add_dll_directory(directory))
