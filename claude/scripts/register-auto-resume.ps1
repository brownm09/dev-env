<#
.SYNOPSIS
    Turn on (or off) auto-resume for desktop sessions cut off by a reboot or app crash.

.DESCRIPTION
    dev-env #1182, ADR-152. Two pieces make interrupted Claude desktop sessions continue on
    their own:

      * The SessionStart hook session-start-auto-resume.py, wired in settings.shared.json.
        When a session's CLI starts and its transcript ended mid-turn, the hook wakes the
        model with a "check for errors and continue" message.
      * The "ClaudeAutoResumeLauncher" scheduled task this script registers. After the app
        starts it opens each interrupted session (claude://code/continue?session=...) so
        the app starts that session's CLI and the hook fires.

    Both read the opt-in file ~/.claude/auto-resume.json. This script creates it only if it
    is absent, with the -Include rules you pass, and never overwrites an existing file. The
    rules are "all", "pinned" (the sidebar pin), "cwd:<glob>" and
    "session:<local_... id>"; edit the file by hand to change them later.

    The task is non-elevated and windowless (ADR-041): Limited run level, Interactive logon,
    pyw.exe with no console, and the task is marked Hidden. It runs at sign-in plus 1 minute
    and every -IntervalMinutes around the clock. A run with nothing to do exits in about a
    second.

.PARAMETER Include
    Opt-in rules written to a new ~/.claude/auto-resume.json. Default: "all".

.PARAMETER IntervalMinutes
    How often the launcher checks for a new app instance (default 3).

.PARAMETER ScriptPath
    The launcher to run. Defaults to the junctioned ~/.claude/scripts path, which tracks
    dev-env main; override only to test an unmerged worktree.

.PARAMETER Unregister
    Undo: back up the task definition to Documents\LOGS\ClaudeAutoResumeLauncherBackup.xml
    (write-if-absent, ADR-079), remove the task, and set "enabled": false in
    ~/.claude/auto-resume.json, which also stops the hook. Re-run without switches to turn
    it back on; the config file keeps your rules.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-auto-resume.ps1
    powershell -ExecutionPolicy Bypass -File register-auto-resume.ps1 -Include pinned,'cwd:C:\Users\brown\Git\dev-env*'
    powershell -ExecutionPolicy Bypass -File register-auto-resume.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string[]]$Include = @('all'),
    [int]$IntervalMinutes = 3,
    [string]$ScriptPath = (Join-Path $env:USERPROFILE '.claude\scripts\auto-resume-launcher.py'),
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'ClaudeAutoResumeLauncher'
$ConfigPath = Join-Path $env:USERPROFILE '.claude\auto-resume.json'
$LogsDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS'
$BackupPath = Join-Path $LogsDir 'ClaudeAutoResumeLauncherBackup.xml'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Set-ConfigEnabled([bool]$Enabled) {
    if (-not (Test-Path $ConfigPath)) { return }
    $cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json
    if ($cfg.PSObject.Properties.Name -contains 'enabled') { $cfg.enabled = $Enabled }
    else { $cfg | Add-Member -NotePropertyName enabled -NotePropertyValue $Enabled }
    [IO.File]::WriteAllText($ConfigPath, ($cfg | ConvertTo-Json -Depth 5), $Utf8NoBom)
    $readBack = (Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json).enabled
    if ($readBack -ne $Enabled) { throw "Wrote enabled=$Enabled to $ConfigPath but read back '$readBack'." }
    Write-Host "Set enabled=$Enabled in $ConfigPath."
}

if ($Unregister) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
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
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            throw "Unregister-ScheduledTask completed but '$TaskName' is still present at read-back."
        }
        Write-Host "Removed scheduled task '$TaskName' (definition backed up to $BackupPath)."
    } else {
        Write-Host "No scheduled task '$TaskName' to remove."
    }
    Set-ConfigEnabled $false
    return
}

# Under `powershell -File`, `-Include a,b` arrives as the single string "a,b": split it.
$Include = @($Include | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim(" '`"") } |
    Where-Object { $_ })
$badRules = @($Include | Where-Object { $_ -notmatch '^(all|pinned|(cwd|session):.+)$' })
if ($badRules.Count -gt 0) {
    throw "Unknown -Include rule(s): $($badRules -join ', '). Use all, pinned, cwd:<glob> or session:<local_id>."
}

$pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { throw "pyw.exe (the Python launcher) is not on PATH; install Python with the py launcher first." }
if (-not (Test-Path $ScriptPath)) {
    Write-Warning "Launcher not found at $ScriptPath. Registering anyway; it resolves once dev-env main contains auto-resume-launcher.py."
}

if (Test-Path $ConfigPath) {
    Write-Host "Keeping existing $ConfigPath (rules unchanged)."
    Set-ConfigEnabled $true
} else {
    $cfg = [ordered]@{ enabled = $true; include = @($Include); exclude = @(); maxAgeHours = 12;
                       settleSeconds = 20; maxAttempts = 2 }
    [IO.File]::WriteAllText($ConfigPath, ($cfg | ConvertTo-Json -Depth 5), $Utf8NoBom)
    Write-Host "Created $ConfigPath with include = $($Include -join ', ')."
}

$action = New-ScheduledTaskAction -Execute $pyw -Argument ('-3 "{0}"' -f $ScriptPath)

# A daily trigger repeating every N minutes for 24 hours gives one continuous cycle that
# needs no sign-out to start and never doubles up. A logon trigger repeating forever does
# both: its cycle starts only at the next sign-in, and a second "from now" trigger beside
# it would run out of phase with it. The extra sign-in run (plus 1 minute, no repetition)
# catches a reboot without waiting for the next slot.
$every = New-TimeSpan -Minutes $IntervalMinutes
$daily = New-ScheduledTaskTrigger -Daily -At '00:00'
$daily.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval $every `
    -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME)
$atLogon.Delay = 'PT1M'

$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($daily, $atLogon) `
    -Principal $principal -Settings $settings -Force `
    -Description "Opens Claude desktop sessions that a reboot or app crash cut off mid-turn, so their SessionStart hook can continue them. dev-env #1182 / ADR-152." | Out-Null

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Register-ScheduledTask completed but '$TaskName' is missing at read-back."
}
Start-ScheduledTask -TaskName $TaskName
Write-Host "Registered scheduled task '$TaskName' (every ${IntervalMinutes} min, plus sign-in + 1 min; non-elevated, hidden)."
Write-Host "Launcher: $ScriptPath"
Write-Host "Logs:     $LogsDir\claude-auto-resume_<date>.jsonl"
Write-Host "Undo:     powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
