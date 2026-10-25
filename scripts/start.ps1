# Start the backend (serves the built frontend from frontend/dist).
#
#   powershell -ExecutionPolicy Bypass -File scripts/start.ps1
#
# Pass -Dev to only run the API (frontend via `bun run dev`).

param(
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"

if (-not (Test-Path (Join-Path $backend ".venv"))) {
    throw "backend/.venv missing. Run: cd backend; uv venv --python 3.12 .venv; uv sync --extra dev"
}

if (-not $Dev) {
    $index = Join-Path $root "frontend\dist\index.html"
    if (-not (Test-Path $index)) {
        Write-Host "Building frontend with Bun..." -ForegroundColor Cyan
        Push-Location (Join-Path $root "frontend")
        bun run build
        Pop-Location
    }
}

Set-Location $backend
& .\.venv\Scripts\python.exe -m app
