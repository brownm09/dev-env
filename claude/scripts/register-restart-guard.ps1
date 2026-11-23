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
    Undo: back up the task definition to %USERPROFILE%\Documents\LOGS\ClaudeRestartGuardBackup.xml
    (write-if-absent, ADR-079), stop the guard and wait for it to exit, end any deferral
    (restart-guard.py --restore), remove the task, and set "enabled": false in
    ~/.claude/restart-guard.json. If the guard won't stop, or an active-hours backup exists and the
    restore can't run or fails, it throws and leaves the task registered. Re-run without switches to
    turn it back on; the config keeps your settings. Needs py.exe only when there is a backup to
    restore, and never pyw.exe.

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
# Backups sit at a fixed path, so moving Documents can't hide them (the same path restart-guard.py uses).
$AnchorDir = Join-Path $env:USERPROFILE 'Documents\LOGS'
$BackupPath = Join-Path $AnchorDir 'ClaudeRestartGuardBackup.xml'
$ActiveHoursBackup = Join-Path $AnchorDir 'ClaudeRestartGuardActiveHoursBackup.json'
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

function Stop-Guard {
    # Stop-ScheduledTask returns before the process has exited, so wait for the task to leave Running.
    Stop-ScheduledTask -TaskName $TaskName
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-ScheduledTask -TaskName $TaskName).State -eq 'Running' -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    return (Get-ScheduledTask -TaskName $TaskName).State -ne 'Running'
}

if ($Unregister) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        # Back up before mutating (ADR-079); write-if-absent so the first capture survives.
        if (-not ((Test-Path $BackupPath) -and (Get-Item $BackupPath).Length -gt 0)) {
            if (-not (Test-Path $AnchorDir)) { New-Item -ItemType Directory -Path $AnchorDir -Force | Out-Null }
            try {
                Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |
                    Out-File -FilePath $BackupPath -Encoding unicode -ErrorAction Stop  # the export declares UTF-16
            } catch {
                throw "Failed to back up '$TaskName' to $BackupPath -- refusing to remove it without a restorable backup (ADR-079). $_"
            }
        }
        # Stop the guard before restoring: a running guard would move active hours again.
        if (-not (Stop-Guard)) { throw "'$TaskName' is still running 15 s after Stop-ScheduledTask; leaving it in place. Run -Unregister again." }
    }
    # End any deferral. The restore is required whenever the guard has left a backup behind.
    if (Test-Path $ActiveHoursBackup) {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if (-not $py) { throw "py.exe isn't on PATH, so active hours can't be restored from $ActiveHoursBackup; leaving the task in place." }
        if (-not (Test-Path $ScriptPath)) { throw "Guard not found at $ScriptPath, so active hours can't be restored; leaving the task in place." }
        & $py.Source -3 $ScriptPath --restore
        if ($LASTEXITCODE -ne 0) { throw "restart-guard.py --restore failed (exit $LASTEXITCODE); leaving the task in place (it restarts within 5 minutes)." }
    } else {
        Write-Host "No active-hours backup at $ActiveHoursBackup: the guard never changed active hours."
    }
    if ($task) {
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

$launcher = Get-Command pyw.exe -ErrorAction SilentlyContinue
if (-not $launcher) { throw "pyw.exe (the Python launcher) is not on PATH; install Python with the py launcher first." }
$pyw = $launcher.Source

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
    # A start while the old guard is still stopping is dropped too.
    if (-not (Stop-Guard)) { Write-Warning "The old guard is still running 15 s after Stop-ScheduledTask; it keeps running the old code until it exits." }
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
$deadline = (Get-Date).AddSeconds(10)
while ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
}
if ((Get-ScheduledTask -TaskName $TaskName).State -ne 'Running') {
    Write-Warning "Registered '$TaskName', but it isn't running 10 s after the start. Check Documents\LOGS\claude-restart-guard_<date>.jsonl for a start_error or already_running line."
}
Write-Host "Registered and started scheduled task '$TaskName' (at sign-in and every 5 minutes if it has stopped; non-elevated, hidden)."
Write-Host "Guard:  $ScriptPath"
Write-Host "Status: py -3 `"$ScriptPath`" --status"
Write-Host "Log:    $LogsDir\claude-restart-guard_<date>.jsonl"
Write-Host "Undo:   powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
