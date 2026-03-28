param(
    [string]$Python = ".\\venv_win\\Scripts\\python.exe",
    [string]$CMake = "cmake"
)

$ErrorActionPreference = "Stop"

& $Python -m pip install -e ".[desktop,build]"

& ".\\scripts\\build_overlay_ui.ps1" -CMake $CMake

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  "doubao-voice-agent.spec"
