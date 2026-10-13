$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at $python."
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name PersonalSearchEngine `
    --distpath (Join-Path $PSScriptRoot "dist") `
    (Join-Path $PSScriptRoot "launcher.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the launcher."
}

Write-Host "Launcher created at $(Join-Path $PSScriptRoot 'dist\PersonalSearchEngine.exe')"
