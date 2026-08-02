$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}

if (-not $env:SAM2_DEVICE) {
    $env:SAM2_DEVICE = "cpu"
}

& $python -m uvicorn sam2_service.main:app --host 0.0.0.0 --port 9025 --workers 1
