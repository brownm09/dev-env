<#
.SYNOPSIS
    Turn the restart guard on or off.
    Purpose: registers or removes the ClaudeRestartGuard scheduled task, which holds off restarts while Claude sessions work.

.DESCRIPTION
    dev-env #1398, ADR-165. Registers the "ClaudeRestartGuard" scheduled task, which runs
    restart-guard.py at sign-in and keeps it running. While any Claude session is busy, the guard
    asks for confirmation before a restart you start, and moves Windows Update's active hours so
    an automatic restart waits (capped at maxDeferHours).

    Creates ~/.claude/restart-guard.json if absent, in report-only mode ("block": false,
    "deferUpdates": false): the guard logs what it would do to
    Documents\LOGS\claude-restart-guard_<date>.jsonl and changes nothing. Turn each on in that file
    once the log looks right; the guard rereads it every 30 seconds. An existing file is never
    overwritten.

    The task is non-elevated and windowless (ADR-041): Limited run level, Interactive logon, pyw.exe
    with no console, marked Hidden, no time limit, started at sign-in and tried again every 5 minutes,
    which is ignored while the guard runs. The guard's own window is off-screen with no taskbar
    button. Registering again is safe: it stops the running guard and starts the one named by
    -ScriptPath.

.PARAMETER ScriptPath
    The guard to run. Defaults to the junctioned ~/.claude/scripts path, which tracks dev-env main;
    override only to test an unmerged worktree.

.PARAMETER Unregister
    Undo: put the captured active hours back (restart-guard.py --restore), back up the task
    definition to Documents\LOGS\ClaudeRestartGuardBackup.xml (write-if-absent, ADR-079), remove the
    task and stop the guard, and set "enabled": false in ~/.claude/restart-guard.json. Re-run without
    switches to turn it back on; the config keeps your settings.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-restart-guard.ps1
    powershell -ExecutionPolicy Bypass -File register-restart-guard.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $env:USERPROFILE '.claude\scripts\restart-guard.py'),
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'ClaudeRestartGuard'
$ConfigPath = Join-Path $env:USERPROFILE '.claude\restart-guard.json'
$LogsDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS'
$BackupPath = Join-Path $LogsDir 'ClaudeRestartGuardBackup.xml'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Set-ConfigEnabled([bool]$Enabled) {
    if (-not (Test-Path $ConfigPath)) { return }
    $cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
    $cfg | Add-Member -NotePropertyName enabled -NotePropertyValue $Enabled -Force
    [IO.File]::WriteAllText($ConfigPath, ($cfg | ConvertTo-Json -Depth 5), $Utf8NoBom)
    $readBack = (Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json).enabled
    if ($readBack -ne $Enabled) { throw "Wrote enabled=$Enabled to $ConfigPath but read back '$readBack'." }
    Write-Host "Set enabled=$Enabled in $ConfigPath."
}

$launcher = Get-Command pyw.exe -ErrorAction SilentlyContinue
if (-not $launcher) { throw "pyw.exe (the Python launcher) is not on PATH; install Python with the py launcher first." }
$pyw = $launcher.Source

if ($Unregister) {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py -and (Test-Path $ScriptPath)) {
        & $py.Source -3 $ScriptPath --restore
        if ($LASTEXITCODE -ne 0) { throw "restart-guard.py --restore failed (exit $LASTEXITCODE); leaving the task in place." }
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        # Back up before mutating (ADR-079); write-if-absent so the first capture survives.
        if (-not ((Test-Path $BackupPath) -and (Get-Item $BackupPath).Length -gt 0)) {
            if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
            try {
                Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |
                    Out-File -FilePath $BackupPath -Encoding unicode -ErrorAction Stop  # the export declares UTF-16
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
    } else {
        Write-Host "No scheduled task '$TaskName' to remove."
    }
    Set-ConfigEnabled $false
    return
}

if (-not (Test-Path $ScriptPath)) { throw "Guard not found at $ScriptPath. Pull dev-env main first." }

if (Test-Path $ConfigPath) {
    Write-Host "Keeping existing $ConfigPath (settings unchanged)."
    Set-ConfigEnabled $true
} else {
    $cfg = [ordered]@{ enabled = $true; block = $false; deferUpdates = $false; maxDeferHours = 24 }
    [IO.File]::WriteAllText($ConfigPath, ($cfg | ConvertTo-Json -Depth 5), $Utf8NoBom)
    Write-Host "Created $ConfigPath in report-only mode (block and deferUpdates false)."
}

# A running guard would otherwise keep its old code: IgnoreNew drops the start below.
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName
    # The stop returns before the task has stopped, and a start in that window is dropped too.
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
}

$action = New-ScheduledTaskAction -Execute $pyw -Argument ('-3 "{0}"' -f $ScriptPath)
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME)
$daily = New-ScheduledTaskTrigger -Daily -At '00:00'
$daily.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $daily) `
    -Principal $principal -Settings $settings -Force `
    -Description "Asks before a restart and defers Windows Update's restart while Claude sessions are working. dev-env #1398 / ADR-165." | Out-Null

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Register-ScheduledTask completed but '$TaskName' is missing at read-back."
}
Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered and started scheduled task '$TaskName' (at sign-in and every 5 minutes if it has stopped; non-elevated, hidden)."
Write-Host "Guard:  $ScriptPath"
Write-Host "Status: py -3 `"$ScriptPath`" --status"
Write-Host "Log:    $LogsDir\claude-restart-guard_<date>.jsonl"
Write-Host "Undo:   powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
