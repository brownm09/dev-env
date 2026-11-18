<#
.SYNOPSIS
    Turn the production-deploy approval notifier on or off.
    Purpose: registers or removes the ClaudeDeployApprovalNotifier scheduled task and the deploy-approve URL scheme.

.DESCRIPTION
    dev-env #1380, ADR-163. Registers:

      ClaudeDeployApprovalNotifier  deploy-approval-notifier.py --tick, at sign-in and every
                                    2 minutes. Shows a Windows toast for each GitHub Actions run
                                    waiting on a production environment you can approve.
      deploy-approve: URL scheme    HKCU\Software\Classes\deploy-approve, whose handler is
                                    deploy-approval-notifier.py --approve "%1". The toast's
                                    Approve button opens it.

    Non-elevated and windowless (ADR-041): Limited run level, Interactive logon, pyw.exe with no
    console, marked Hidden. Registering again is safe and picks up a new -ScriptPath.

    Run this from a normal terminal or a scheduled task, not from a Claude desktop session: that
    app's HKCU writes stay private to it, so the URL scheme would never reach Windows.

.PARAMETER ScriptPath
    The notifier to run. Defaults to the junctioned ~/.claude/scripts path, which tracks dev-env
    main; override only to test an unmerged worktree.

.PARAMETER Unregister
    Undo: back up the task definition and the URL-scheme key to Documents\LOGS\ (write-if-absent,
    ADR-079), remove both, and re-import any deploy-approve key that existed before the first
    registration.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File register-deploy-approval-notifier.ps1
    powershell -ExecutionPolicy Bypass -File register-deploy-approval-notifier.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$ScriptPath = (Join-Path $env:USERPROFILE '.claude\scripts\deploy-approval-notifier.py'),
    [switch]$Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$TaskName = 'ClaudeDeployApprovalNotifier'
$RegPath = 'HKCU\Software\Classes\deploy-approve'
$PsRegPath = 'HKCU:\Software\Classes\deploy-approve'
$LogsDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS'
$PriorBackup = Join-Path $LogsDir 'DeployApproveProtocolPrior.reg'
$KeyBackup = Join-Path $LogsDir 'DeployApproveProtocolBackup.reg'
$TaskBackup = Join-Path $LogsDir "${TaskName}Backup.xml"

function Test-NonEmpty([string]$Path) { (Test-Path $Path) -and (Get-Item $Path).Length -gt 0 }

function Export-Key([string]$File) {
    # Back up before mutating (ADR-079); write-if-absent so the first capture survives.
    if (Test-NonEmpty $File) { return }
    if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
    & reg.exe export $RegPath $File /y | Out-Null
    if ($LASTEXITCODE -ne 0 -or -not (Test-NonEmpty $File)) {
        throw "Failed to back up $RegPath to $File -- refusing to change it without a restorable backup (ADR-079)."
    }
}

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if (-not (Test-NonEmpty $TaskBackup)) {
            if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null }
            try {
                Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop |
                    Out-File -FilePath $TaskBackup -Encoding unicode -ErrorAction Stop  # the export declares UTF-16
            } catch {
                throw "Failed to back up '$TaskName' to $TaskBackup -- refusing to remove it without a restorable backup (ADR-079). $_"
            }
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            throw "Unregister-ScheduledTask completed but '$TaskName' is still present at read-back."
        }
        Write-Host "Removed scheduled task '$TaskName' (definition backed up to $TaskBackup)."
    } else {
        Write-Host "No scheduled task '$TaskName' to remove."
    }

    if (Test-Path $PsRegPath) {
        Export-Key $KeyBackup
        Remove-Item -Path $PsRegPath -Recurse -Force
        if (Test-Path $PsRegPath) { throw "Removed $RegPath but it is still present at read-back." }
        Write-Host "Removed $RegPath (backed up to $KeyBackup)."
    } else {
        Write-Host "No $RegPath key to remove."
    }
    if (Test-NonEmpty $PriorBackup) {
        & reg.exe import $PriorBackup 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to restore the pre-existing $RegPath from $PriorBackup." }
        Write-Host "Restored the deploy-approve key that existed before registration, from $PriorBackup."
    }
    return
}

$launcher = Get-Command pyw.exe -ErrorAction SilentlyContinue
if (-not $launcher) { throw "pyw.exe (the Python launcher) is not on PATH; install Python with the py launcher first." }
$pyw = $launcher.Source
if (-not (Test-Path $ScriptPath)) { throw "Notifier not found at $ScriptPath. Pull dev-env main first." }
if (-not (Get-Command gh.exe -ErrorAction SilentlyContinue)) { throw "gh.exe is not on PATH; the notifier polls GitHub through it." }

# URL scheme. A key some other tool registered under this name is captured once so -Unregister can put it back.
if (Test-Path $PsRegPath) {
    $existing = (Get-ItemProperty -Path "$PsRegPath\shell\open\command" -ErrorAction SilentlyContinue).'(default)'
    if (-not ($existing -and $existing -like '*deploy-approval-notifier*')) { Export-Key $PriorBackup }
}
$command = '"{0}" -3 "{1}" --approve "%1"' -f $pyw, $ScriptPath
New-Item -Path "$PsRegPath\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $PsRegPath -Name '(default)' -Value 'URL:Deploy approval (deploy-approval-notifier)'
Set-ItemProperty -Path $PsRegPath -Name 'URL Protocol' -Value ''
Set-ItemProperty -Path "$PsRegPath\shell\open\command" -Name '(default)' -Value $command
$readBack = (Get-ItemProperty -Path "$PsRegPath\shell\open\command").'(default)'
if ($readBack -ne $command) { throw "Wrote the deploy-approve handler but read back '$readBack'." }

# Scheduled task.
$user = "{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -Hidden

$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
$daily = New-ScheduledTaskTrigger -Daily -At '00:00'
$daily.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 2) `
    -RepetitionDuration (New-TimeSpan -Hours 24)).Repetition

Register-ScheduledTask -TaskName $TaskName `
    -Action (New-ScheduledTaskAction -Execute $pyw -Argument ('-3 "{0}" --tick' -f $ScriptPath)) `
    -Trigger @($atLogon, $daily) -Principal $principal -Settings $settings -Force `
    -Description "Toasts GitHub Actions runs waiting on a production approval. dev-env #1380 / ADR-163." | Out-Null
if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    throw "Register-ScheduledTask completed but '$TaskName' is missing at read-back."
}
Start-ScheduledTask -TaskName $TaskName

Write-Host "Registered '$TaskName' (at sign-in and every 2 minutes; non-elevated, hidden) and the deploy-approve: URL scheme."
Write-Host "Handler:  $readBack"
Write-Host "Status:   py -3 `"$ScriptPath`" --status"
Write-Host "Try it:   py -3 `"$ScriptPath`" --test-toast"
Write-Host "Undo:     powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Unregister"
