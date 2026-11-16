function Get-PlatformPython {
    param([string]$ProjectRoot)
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $ProjectRoot "venv\Scripts\python.exe")
    )
    if ($env:CONDA_PREFIX) { $candidates += Join-Path $env:CONDA_PREFIX "python.exe" }
    if ($env:VISION_CONDA_PREFIX) { $candidates += Join-Path $env:VISION_CONDA_PREFIX "python.exe" }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    throw "No usable Python: create .venv/venv, activate Conda, or set VISION_CONDA_PREFIX."
}
