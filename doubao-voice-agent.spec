# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(globals().get("SPECPATH", Path.cwd()))
PATHEX = [str(ROOT)]
HIDDEN_IMPORTS = [
    'doubaoime_asr',
    'doubaoime_asr.agent',
    'doubaoime_asr.agent.stable_main',
    'doubaoime_asr.agent.stable_simple_app',
    'doubaoime_asr.agent.worker_main',
]


def find_overlay_binary() -> list[tuple[str, str]]:
    candidates = [
        ROOT / "build" / "overlay_ui" / "Release" / "overlay_ui.exe",
        ROOT / "build" / "overlay_ui" / "RelWithDebInfo" / "overlay_ui.exe",
        ROOT / "build" / "overlay_ui" / "MinSizeRel" / "overlay_ui.exe",
        ROOT / "build" / "overlay_ui" / "overlay_ui.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [(str(candidate), ".")]
    raise FileNotFoundError("overlay_ui.exe not found; run scripts/build_overlay_ui.ps1 first")


a = Analysis(
    ['scripts\\voice_agent_entry.py'],
    pathex=PATHEX,
    binaries=find_overlay_binary(),
    datas=[('opus.dll', '.'), ('libgcc_s_seh-1.dll', '.'), ('libwinpthread-1.dll', '.')],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='doubao-voice-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='doubao-voice-agent',
)
