param([int]$TimeoutSeconds = 60)
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
. (Join-Path $projectRoot "scripts\python_environment.ps1")
$python = Get-PlatformPython -ProjectRoot $projectRoot
Push-Location $projectRoot
try {
    Write-Host "Using Python: $python"
    & $python (Join-Path $projectRoot "scripts\manage_services.py") start --timeout $TimeoutSeconds
    $serviceExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $serviceExitCode
