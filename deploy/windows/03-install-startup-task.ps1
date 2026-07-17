param(
  [string]$TaskName = "NiuOne Dashboard",
  [int]$Port = 8787
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RunBat = Join-Path $ProjectRoot "run.bat"

if (-not (Test-Path $RunBat)) {
  throw "Windows launcher was not found: $RunBat"
}

$action = New-ScheduledTaskAction `
  -Execute "cmd.exe" `
  -Argument "/c `"$RunBat`" --port $Port --no-browser" `
  -WorkingDirectory $ProjectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Description "Start NiuOne dashboard at Windows logon." `
  -Force | Out-Null

Write-Host "Registered startup task: $TaskName" -ForegroundColor Green
Write-Host "You can view it in Task Scheduler or remove it with 04-uninstall-startup-task.ps1."
