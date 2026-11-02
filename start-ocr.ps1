$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot "ocr_service\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "OCR virtual environment not found: $python"
}

& $python (Join-Path $projectRoot "scripts\run_ocr.py")
