<#
.SYNOPSIS
  Creates (or removes) the two Windows scheduled tasks that keep data collection running.

.DESCRIPTION
  1. "ReportManager Collection Worker": runs `manage.py collection_worker` every minute. It runs any queued or due
     collector, and records a heartbeat.
  2. "ReportManager Collection Alerts": runs `manage.py collection_alerts` every few minutes and emails problems. It is
     a separate task on purpose, so that a dead worker is itself reported.

  The tasks run as the given user, whether or not that user is logged on (S4U: no password is stored; the task cannot
  reach network resources with that user's Windows credentials, which is fine because the collectors authenticate with
  the keys and connection strings in their .env files). Run this from an elevated PowerShell. Not yet tested on your
  machine: use -WhatIf first.

.EXAMPLE
  .\Register-CollectorTasks.ps1 -WhatIf
  .\Register-CollectorTasks.ps1
  .\Register-CollectorTasks.ps1 -Remove
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
    [string]$PythonPath,
    [string]$RunAsUser = "$env:USERDOMAIN\$env:USERNAME",
    [int]$WorkerEveryMinutes = 1,
    [int]$AlertsEveryMinutes = 5,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'

$backend = Join-Path $ProjectPath 'backend'
if (-not $PythonPath) { $PythonPath = Join-Path $backend '.venv\Scripts\python.exe' }
$tasks = @(
    @{ Name = 'ReportManager Collection Worker'; Command = 'collection_worker'; Every = $WorkerEveryMinutes; LimitHours = 4;
       Description = 'Runs queued and scheduled data collectors (Report Manager).' },
    @{ Name = 'ReportManager Collection Alerts'; Command = 'collection_alerts'; Every = $AlertsEveryMinutes; LimitHours = 1;
       Description = 'Emails when a collector fails, is overdue, or the worker stops (Report Manager).' }
)

if ($Remove) {
    foreach ($t in $tasks) {
        if ($PSCmdlet.ShouldProcess($t.Name, 'Unregister scheduled task')) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction SilentlyContinue
            Write-Host "Removed: $($t.Name)"
        }
    }
    return
}

if (-not (Test-Path $PythonPath)) { throw "Python not found at $PythonPath. Pass -PythonPath, or create backend\.venv first." }
if (-not (Test-Path (Join-Path $backend 'manage.py'))) { throw "manage.py not found in $backend. Pass -ProjectPath." }

foreach ($t in $tasks) {
    $action    = New-ScheduledTaskAction -Execute $PythonPath -Argument "manage.py $($t.Command)" -WorkingDirectory $backend
    $trigger   = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                    -RepetitionInterval (New-TimeSpan -Minutes $t.Every) -RepetitionDuration (New-TimeSpan -Days 3650)
    $principal = New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType S4U -RunLevel Limited
    # IgnoreNew: if the previous run is still going when the next tick is due, skip the tick instead of piling up.
    $settings  = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries `
                    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours $t.LimitHours)
    if ($PSCmdlet.ShouldProcess($t.Name, "Register scheduled task (every $($t.Every) min, as $RunAsUser)")) {
        Register-ScheduledTask -TaskName $t.Name -Description $t.Description -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force | Out-Null
        Write-Host "Registered: $($t.Name) (every $($t.Every) minute(s))"
    }
}
Write-Host "`nNext: see backend\deploy\README.md to check it is working and to switch the schedules on."
