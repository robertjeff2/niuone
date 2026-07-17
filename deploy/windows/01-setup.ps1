param(
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$LocalData = Join-Path $ProjectRoot ".local-data"
$Runtime = Join-Path $LocalData "runtime"
$CronOutput = Join-Path $Runtime "cron\output"
$Venv = Join-Path $LocalData ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$EnvFile = Join-Path $LocalData "dashboard.env"

function Write-Step($Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Set-EnvLine {
  param(
    [string[]]$Lines,
    [string]$Name,
    [string]$Value
  )
  $escaped = [regex]::Escape($Name)
  $line = "$Name=$Value"
  $found = $false
  $next = foreach ($item in $Lines) {
    if ($item -match "^\s*$escaped=") {
      $found = $true
      $line
    } else {
      $item
    }
  }
  if (-not $found) {
    $next += $line
  }
  return $next
}

Write-Step "Prepare directories"
New-Item -ItemType Directory -Force -Path $LocalData, $Runtime, $CronOutput | Out-Null

Write-Step "Prepare dashboard.env"
if (-not (Test-Path $EnvFile)) {
  @(
    "# Managed by NiuOne Windows setup.",
    "DASHBOARD_ADMIN_PASSWORD=change-me",
    "DASHBOARD_ACTIVE_STRATEGY=zettaranc",
    "DASHBOARD_INITIAL_CASH=50000",
    "DASHBOARD_NOTIFICATION_ENABLED=0"
  ) | Set-Content -Encoding UTF8 $EnvFile
}

$backup = "$EnvFile.windows-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $EnvFile $backup -Force

$lines = Get-Content $EnvFile -Encoding UTF8
$pathUpdates = @{
  "DASHBOARD_HOME" = $Runtime
  "DASHBOARD_HOST" = "127.0.0.1"
  "DASHBOARD_PORT" = [string]$Port
  "PYTHON_BIN" = $VenvPython
  "DASHBOARD_CONFIG" = (Join-Path $Runtime "config.yaml")
  "DASHBOARD_PUSH_HISTORY_DB" = (Join-Path $Runtime "push_history.db")
  "DASHBOARD_PORTFOLIO_STATE" = (Join-Path $CronOutput "niuniu_practice_portfolio.json")
  "DASHBOARD_TRADER_SCRIPT" = (Join-Path $ProjectRoot "app\entrypoints\niuniu_practice_trader.py")
  "DASHBOARD_CN_STOCK_TOOLS" = (Join-Path $ProjectRoot "app\entrypoints\cn_stock_tools.py")
  "DASHBOARD_B1_SCANNER" = (Join-Path $ProjectRoot "app\entrypoints\multi_strategy_screen.py")
}

foreach ($key in $pathUpdates.Keys) {
  $lines = Set-EnvLine -Lines $lines -Name $key -Value $pathUpdates[$key]
}

if (-not ($lines -match "^\s*DASHBOARD_INITIAL_CASH=")) {
  $lines += "DASHBOARD_INITIAL_CASH=50000"
}

$lines | Set-Content -Encoding UTF8 $EnvFile
Write-Host "Backed up previous env: $backup"

Write-Step "Check Windows launcher"
$RunBat = Join-Path $ProjectRoot "run.bat"
if (-not (Test-Path $RunBat)) {
  throw "run.bat was not found. Please make sure the project was copied completely."
}
Write-Host "The virtual environment and dependencies will be created by run.bat: $Venv"

Write-Step "Done"
Write-Host "Project root: $ProjectRoot"
Write-Host "Launcher: .\run.bat"
Write-Host "Start command: .\deploy\windows\02-start-dashboard.ps1"
Write-Host "URL: http://127.0.0.1:$Port"
