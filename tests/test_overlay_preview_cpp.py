from pathlib import Path

from doubaoime_asr.agent.overlay_preview_cpp import find_overlay_executable, overlay_executable_candidates


def test_overlay_candidates_prefer_env_override(tmp_path: Path):
    overlay = tmp_path / "custom" / "overlay_ui.exe"
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"")

    candidates = overlay_executable_candidates(
        env={"DOUBAO_OVERLAY_EXE": str(overlay)},
        executable=str(tmp_path / "python.exe"),
        frozen=False,
        module_file=tmp_path / "repo" / "doubaoime_asr" / "agent" / "overlay_preview_cpp.py",
    )

    assert candidates[0] == overlay


def test_find_overlay_executable_in_frozen_bundle(tmp_path: Path):
    bundle_dir = tmp_path / "dist"
    bundle_dir.mkdir()
    overlay = bundle_dir / "overlay_ui.exe"
    overlay.write_bytes(b"")

    found = find_overlay_executable(
        env={},
        executable=str(bundle_dir / "doubao-voice-agent.exe"),
        frozen=True,
        module_file=tmp_path / "repo" / "doubaoime_asr" / "agent" / "overlay_preview_cpp.py",
    )

    assert found == overlay
