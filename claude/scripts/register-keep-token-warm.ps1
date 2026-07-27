<#
.SYNOPSIS
    Register (or remove) the "ClaudeKeepTokenWarm" local scheduled task.

.DESCRIPTION
    Creates a Windows scheduled task that runs keep-token-warm.ps1 every 4 hours to keep
    the Claude OAuth token in ~/.claude/.credentials.json fresh (dev-env #359, ADR-043).

    Per-machine by design: the task runs the local claude.exe and writes the local
    credentials file, so each machine registers its own task. The task action points at the
    junctioned path C:\Users\brown\.claude\scripts\keep-token-warm.ps1 (which tracks dev-env
    `main`), so payload updates land automatically without re-registering.

    Non-elevated and windowless (ADR-041): RunLevel Limited, Interactive logon (no stored
    password), powershell launched -WindowStyle Hidden -NonInteractive, task marked Hidden.
    This script does NOT self-relaunch as admin; registering a task for the current user at
    Limited run level does not require elevation. If Register-ScheduledTask returns an
    access-denied error on your machine, open an elevated PowerShell yourself and re-run.

    Under the MSIX Claude desktop app this task's refresh is permanently futile (OAuth
    lives in the OS keychain, unreachable to a claude.exe subprocess) -- keep-token-warm.ps1
    now detects and skips that dead-end itself (dev-env #917, ADR-124), and machines that
    no longer need the task at all should -Unregister it (dev-env #917, ADR-043 addendum).

.PARAMETER IntervalHours
    Repetition interval in hours (default 4). The access token TTL is ~8h; a 4h cadence
    bounds how long a lapsed token can persist before a run refreshes it.

.PARAMETER Unregister
    Remove the task instead of creating it. First backs up the live task definition to
    Documents\LOGS\ClaudeKeepTokenWarmBackup.xml (write-if-absent, ADR-079) and verifies
    removal by read-back afterward. Restoring is simply re-running this script with no
    switches -- the task carries no state beyond what this script's own registration logic
    defines, so re-registering deterministically reconstructs the backed-up definition.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-keep-token-warm.ps1
    powershell -ExecutionPolicy Bypass -File register-keep-token-warm.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [int]$IntervalHours = 4,
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'ClaudeKeepTokenWarm'
# Durable junction path (tracks dev-env main), NOT the ephemeral worktree path.
$PayloadPath = Join-Path $env:USERPROFILE '.claude\scripts\keep-token-warm.ps1'
# Write-if-absent anchor (ADR-079): captures the task definition the first time it is
# ever unregistered, so the pristine original always survives no matter how many times
# -Unregister runs afterward.
$BackupPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS\ClaudeKeepTokenWarmBackup.xml'

if ($Unregister) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "No scheduled task '$TaskName' to remove."
        return
    }

    # Back up before mutating (ADR-079): capture the live definition first and refuse to
    # proceed if it can't be captured. Write-if-absent so a later -Unregister run (e.g.
    # after a re-registration) never overwrites the original pristine capture.
    if (-not (Test-Path $BackupPath)) {
        $backupDir = Split-Path $BackupPath -Parent
        if (-not (Test-Path $backupDir)) { New-Item -ItemType Directory -Path $backupDir -Force | Out-Null }
        try {
            Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |
                Out-File -FilePath $BackupPath -Encoding utf8 -ErrorAction Stop
        } catch {
            throw "Failed to back up '$TaskName' to $BackupPath before unregistering -- refusing to proceed without a restorable backup (ADR-079). $_"
        }
        if (-not (Test-Path $BackupPath) -or (Get-Item $BackupPath).Length -eq 0) {
            throw "Backup at $BackupPath is missing or empty after export -- refusing to proceed (ADR-079)."
        }
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        throw "Unregister-ScheduledTask completed but '$TaskName' is still present at read-back."
    }
    Write-Host "Removed scheduled task '$TaskName' (definition backed up to $BackupPath)."
    return
}

if (-not (Test-Path $PayloadPath)) {
    Write-Warning "Payload not found at $PayloadPath. Registering anyway; it will resolve once dev-env main (junctioned to ~/.claude/scripts) contains keep-token-warm.ps1."
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ("-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"{0}`"" -f $PayloadPath)

# Start a minute out, then repeat at the chosen interval. Omitting -RepetitionDuration
# makes the repetition indefinite; passing [TimeSpan]::MaxValue is rejected by Task
# Scheduler as out-of-range (P99999999D...), so it must be left unset.
$trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours)

$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description ("Keeps the Claude OAuth token in ~/.claude/.credentials.json fresh so the post-merge usage-snapshot hook works without a manual `claude` refresh. Runs keep-token-warm.ps1 every {0}h. dev-env #359 / ADR-043." -f $IntervalHours) | Out-Null

Write-Host "Registered scheduled task '$TaskName' (every ${IntervalHours}h, non-elevated, hidden)."
Write-Host "Payload: $PayloadPath"
Write-Host "Logs:    $(Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS')\keep-token-warm_<date>.txt"
