param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at $python. Create .venv and install requirements.txt first."
}

if ([string]::IsNullOrWhiteSpace($env:DOCS_PATH)) {
    $configuredPath = & $python -c "from connectors.base import Settings; s=Settings.from_yaml('config.yaml'); c=s.get_source('docs'); print(c.paths[0] if c and c.enabled and c.paths else 'data')"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the document folder from config.yaml."
    }
    $env:DOCS_PATH = $configuredPath.Trim()
}

if (-not (Test-Path -LiteralPath $env:DOCS_PATH -PathType Container)) {
    throw "Document folder '$env:DOCS_PATH' does not exist. Set DOCS_PATH or update sources.docs.paths in config.yaml."
}

docker compose up --build -d
if ($LASTEXITCODE -ne 0) {
    throw "Could not start the application. Check that Docker Desktop is running."
}

Write-Host "Services are starting. Open http://localhost:8501 to use the application."
if (-not $NoBrowser) {
    Start-Process "http://localhost:8501"
}
