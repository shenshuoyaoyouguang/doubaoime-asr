param(
    [string]$Python = ".\\venv_win\\Scripts\\python.exe"
)

$ErrorActionPreference = "Stop"

& $Python -m pip install -e ".[desktop,build]"

& $Python -m PyInstaller `
  --noconfirm `
  --clean `
  --noupx `
  --onedir `
  --windowed `
  --name "doubao-voice-agent" `
  --add-data "opus.dll;." `
  --add-data "libgcc_s_seh-1.dll;." `
  --add-data "libwinpthread-1.dll;." `
  "scripts\\voice_agent_entry.py"
