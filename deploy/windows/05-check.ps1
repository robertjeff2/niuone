param(
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$EnvFile = Join-Path $ProjectRoot ".local-data\dashboard.env"
$RunBat = Join-Path $ProjectRoot "run.bat"
$Python = Join-Path $ProjectRoot ".local-data\.venv\Scripts\python.exe"
$Portfolio = Join-Path $ProjectRoot ".local-data\runtime\cron\output\niuniu_practice_portfolio.json"

Write-Host "Project: $ProjectRoot"
Write-Host "Env:     $EnvFile"
Write-Host "Run:     $RunBat"
Write-Host "Python:  $Python"

if (-not (Test-Path $EnvFile)) { throw "dashboard.env does not exist. Run 01-setup.ps1 first." }
if (-not (Test-Path $RunBat)) { throw "run.bat does not exist. Please make sure the project was copied completely." }
if (-not (Test-Path $Python)) { Write-Host "Windows virtual environment has not been created yet. run.bat will create it on first start." -ForegroundColor Yellow }

if (Test-Path $Python) {
  & $Python --version
  & $Python -m pip --version
}

$portUsed = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if ($portUsed) {
  Write-Host "Port $Port is already in use. NiuOne may already be running." -ForegroundColor Yellow
} else {
  Write-Host "Port $Port is free."
}

if (Test-Path $Portfolio) {
  Write-Host "Portfolio state file found: $Portfolio"
} else {
  Write-Host "Portfolio state file was not found yet. It will be created after first startup." -ForegroundColor Yellow
}

Write-Host "Check complete." -ForegroundColor Green
