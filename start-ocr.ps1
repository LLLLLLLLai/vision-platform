$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = if ($env:OCR_PYTHON) { $env:OCR_PYTHON } else { Join-Path $projectRoot "ocr_service\.venv\Scripts\python.exe" }

if (-not (Test-Path -LiteralPath $python)) {
    throw "OCR Python not found: $python. Install ocr_service/requirements.txt in an isolated environment or set OCR_PYTHON."
}

& $python (Join-Path $projectRoot "scripts\run_ocr.py")
