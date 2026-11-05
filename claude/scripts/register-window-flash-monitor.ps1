<#
.SYNOPSIS
    Turn the console-flash recorder on or off.

.DESCRIPTION
    dev-env #1225, ADR-155. Registers the "ClaudeWindowFlashMonitor" scheduled task, which runs
    window-flash-monitor.py at sign-in and keeps it running. The monitor only observes: it logs
    console flashes to ~/.claude/scratch/window-flash-monitor/ and keeps 14 days.

    The task is non-elevated and windowless (ADR-041): Limited run level, Interactive logon,
    pyw.exe with no console, and the task is marked Hidden. It has no time limit, and the task
    restarts the monitor up to three times if it stops with an error. Registering again is
    safe: a second start is ignored while the monitor runs.

.PARAMETER ScriptPath
    The monitor to run. Defaults to the junctioned ~/.claude/scripts path, which tracks dev-env
    main; override only to test an unmerged worktree.

.PARAMETER Unregister
    Undo: back up the task definition to Documents\LOGS\ClaudeWindowFlashMonitorBackup.xml
    (write-if-absent, ADR-079), then remove the task and stop the monitor. The logs stay.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-window-flash-monitor.ps1
    powershell -ExecutionPolicy Bypass -File register-window-flash-monitor.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $env:USERPROFILE '.claude\scripts\window-flash-monitor.py'),
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'ClaudeWindowFlashMonitor'
$LogsDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS'
$BackupPath = Join-Path $LogsDir 'ClaudeWindowFlashMonitorBackup.xml'

if ($Unregister) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "No scheduled task '$TaskName' to remove."
        return
    }
    # Back up before mutating (ADR-079); write-if-absent so the first capture survives.
    if (-not ((Test-Path $BackupPath) -and (Get-Item $BackupPath).Length -gt 0)) {
        if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
        try {
            Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |
                Out-File -FilePath $BackupPath -Encoding utf8 -ErrorAction Stop
        } catch {
            throw "Failed to back up '$TaskName' to $BackupPath -- refusing to remove it without a restorable backup (ADR-079). $_"
        }
    }
    Stop-ScheduledTask -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        throw "Unregister-ScheduledTask completed but '$TaskName' is still present at read-back."
    }
    Write-Host "Stopped and removed scheduled task '$TaskName' (definition backed up to $BackupPath)."
    return
}

$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { throw "pyw.exe (the Python launcher) is not on PATH; install Python with the py launcher first." }
if (-not (Test-Path $ScriptPath)) { throw "Monitor not found at $ScriptPath. Pull dev-env main first." }

$action = New-ScheduledTaskAction -Execute $pyw -Argument ('-3 "{0}"' -f $ScriptPath)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME)
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Records console flashes and the process behind each one. Observes only. dev-env #1225 / ADR-155." | Out-Null

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Register-ScheduledTask completed but '$TaskName' is missing at read-back."
}
Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered and started scheduled task '$TaskName' (at sign-in; non-elevated, hidden)."
Write-Host "Monitor: $ScriptPath"
Write-Host "Report:  py -3 `"$ScriptPath`" --report"
Write-Host "Undo:    powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
