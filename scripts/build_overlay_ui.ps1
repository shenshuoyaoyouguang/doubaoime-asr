param(
    [string]$CMake = "cmake",
    [string]$BuildType = "Release"
)

$ErrorActionPreference = "Stop"

Push-Location (Join-Path $PSScriptRoot "..")
try {
    & $CMake --version | Out-Null
    & $CMake -S ".\\overlay_ui" -B ".\\build\\overlay_ui"
    & $CMake --build ".\\build\\overlay_ui" --config $BuildType
} finally {
    Pop-Location
}
