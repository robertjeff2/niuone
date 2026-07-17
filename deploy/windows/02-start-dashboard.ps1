param(
  [string]$HostName = "127.0.0.1",
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RunBat = Join-Path $ProjectRoot "run.bat"

if (-not (Test-Path $RunBat)) {
  throw "Windows launcher was not found: $RunBat"
}

Set-Location $ProjectRoot
Write-Host "NiuOne dashboard starting..." -ForegroundColor Cyan
Write-Host "URL: http://$HostName`:$Port"
$env:DASHBOARD_HOST = $HostName
& $RunBat --port $Port
