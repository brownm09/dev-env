<#
.SYNOPSIS
    Turn the Docker Desktop watchdog on or off.
    Purpose: registers or removes the ClaudeDockerWatchdog and ClaudeDockerStart scheduled tasks.

.DESCRIPTION
    dev-env #1278, ADR-158. Registers two scheduled tasks that run docker-watchdog.py:

      ClaudeDockerWatchdog  --tick, at sign-in and every 5 minutes. Checks the hypervisor,
                            repairs a stuck or crashed Docker Desktop, and stops a session-started
                            engine once sessions are done.
      ClaudeDockerStart     --start, on demand only. pre-tool-use-docker-start.py runs it, so
                            Docker starts outside the Claude app's process tree.

    Both are non-elevated and windowless (ADR-041): Limited run level, Interactive logon, pyw.exe
    with no console, and marked Hidden. Registering again is safe and picks up a new -ScriptPath.

.PARAMETER ScriptPath
    The watchdog to run. Defaults to the junctioned ~/.claude/scripts path, which tracks dev-env
    main; override only to test an unmerged worktree.

.PARAMETER Unregister
    Undo: back up both task definitions to Documents\LOGS\<TaskName>Backup.xml (write-if-absent,
    ADR-079), then remove them. Docker then stays up once started, and the session hook reports
    that it can't start Docker.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-docker-watchdog.ps1
    powershell -ExecutionPolicy Bypass -File register-docker-watchdog.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $env:USERPROFILE '.claude\scripts\docker-watchdog.py'),
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$WatchdogTask = 'ClaudeDockerWatchdog'
$StartTask = 'ClaudeDockerStart'
$LogsDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS'

if ($Unregister) {
    foreach ($name in @($WatchdogTask, $StartTask)) {
        if (-not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
            Write-Host "No scheduled task '$name' to remove."
            continue
        }
        # Back up before mutating (ADR-079); write-if-absent so the first capture survives.
        $backup = Join-Path $LogsDir "${name}Backup.xml"
        if (-not ((Test-Path $backup) -and (Get-Item $backup).Length -gt 0)) {
            if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
            try {
                Export-ScheduledTask -TaskName $name -ErrorAction Stop |
                    Out-File -FilePath $backup -Encoding unicode -ErrorAction Stop  # the export declares UTF-16
            } catch {
                throw "Failed to back up '$name' to $backup -- refusing to remove it without a restorable backup (ADR-079). $_"
            }
        }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            throw "Unregister-ScheduledTask completed but '$name' is still present at read-back."
        }
        Write-Host "Removed scheduled task '$name' (definition backed up to $backup)."
    }
    return
}

$launcher = Get-Command pyw.exe -ErrorAction SilentlyContinue
if (-not $launcher) { throw "pyw.exe (the Python launcher) is not on PATH; install Python with the py launcher first." }
$pyw = $launcher.Source
if (-not (Test-Path $ScriptPath)) { throw "Watchdog not found at $ScriptPath. Pull dev-env main first." }

$user = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -Hidden

$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$daily = New-ScheduledTaskTrigger -Daily -At '00:00'
$daily.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition

Register-ScheduledTask -TaskName $WatchdogTask `
    -Action (New-ScheduledTaskAction -Execute $pyw -Argument ('-3 "{0}" --tick' -f $ScriptPath)) `
    -Trigger @($atLogon, $daily) -Principal $principal -Settings $settings -Force `
    -Description "Keeps Docker Desktop startable and stops it when sessions are done. dev-env #1278 / ADR-158." | Out-Null

Register-ScheduledTask -TaskName $StartTask `
    -Action (New-ScheduledTaskAction -Execute $pyw -Argument ('-3 "{0}" --start' -f $ScriptPath)) `
    -Principal $principal -Settings $settings -Force `
    -Description "Starts Docker Desktop outside the Claude app's process tree. dev-env #1278 / ADR-158." | Out-Null

foreach ($name in @($WatchdogTask, $StartTask)) {
    if (-not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)) {
        throw "Register-ScheduledTask completed but '$name' is missing at read-back."
    }
}
Start-ScheduledTask -TaskName $WatchdogTask
Write-Host "Registered '$WatchdogTask' (at sign-in and every 5 minutes) and '$StartTask' (on demand); non-elevated, hidden."
Write-Host "Watchdog: $ScriptPath"
Write-Host "Status:   py -3 `"$ScriptPath`" --status"
Write-Host "Undo:     powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
