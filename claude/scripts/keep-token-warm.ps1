<#
.SYNOPSIS
    Keep the Claude OAuth access token in ~/.claude/.credentials.json fresh.

.DESCRIPTION
    The post-merge usage-snapshot hook (claude/scripts/usage-snapshot.py) reads the
    short-lived OAuth access token from ~/.claude/.credentials.json. The desktop/Cowork
    client refreshes its own token elsewhere and never writes that file back, so the
    on-disk token goes stale and the snapshot stops working until a manual `claude` run
    rewrites it (dev-env #359; visibility added in #357).

    This script is the payload of a local Windows scheduled task. It invokes the Claude
    CLI headlessly, which — when the token is at/near expiry — refreshes it and writes the
    new token back to .credentials.json (this is the same refresh the user triggers by
    running `claude` manually). It is best-effort: any failure is logged and the script
    still exits 0 so the scheduled task is never left in an error state.

    OBSERVABILITY / VERIFICATION: whether headless `claude -p` actually refreshes-and-writes
    the file near expiry is unverified (interactive `claude` is confirmed; headless is not).
    Every run logs the credentials-file mtime and the token's minutes-to-expiry BEFORE and
    AFTER the CLI call so a few days of logs either confirm or disprove the mechanism. The
    token VALUE is never read or logged — only its numeric expiry timestamp and the file mtime.

    No elevation, no new console window (ADR-041): launched hidden/non-interactive by the
    scheduled task; logs to Documents\LOGS instead of keeping a window open.

.NOTES
    Registered by register-keep-token-warm.ps1. Related: dev-env #359, ADR-043, PR #357 (#355).
#>
[CmdletBinding()]
param(
    # Model for the keep-warm call — cheapest that still performs a real authenticated turn.
    [string]$Model = 'haiku',
    # Hard cap on the CLI call so a hung process never lingers.
    [int]$TimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$credPath = Join-Path $env:USERPROFILE '.claude\.credentials.json'

# --- logging -------------------------------------------------------------
$logDir = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'LOGS'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir ("keep-token-warm_{0}.txt" -f (Get-Date -Format 'yyyy-MM-dd'))

function Write-Log([string]$Message) {
    $line = "{0} | {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

# Read only the token's numeric expiry + the file mtime. Never touch the token value.
function Get-TokenState {
    $state = [ordered]@{ mtime = $null; expiryMin = $null }
    try {
        if (Test-Path $credPath) {
            $state.mtime = (Get-Item $credPath).LastWriteTime
            $creds = Get-Content $credPath -Raw -Encoding utf8 | ConvertFrom-Json
            $expMs = [double]$creds.claudeAiOauth.expiresAt
            if ($expMs -gt 0) {
                $nowMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
                $state.expiryMin = [math]::Round((($expMs - $nowMs) / 60000.0), 1)
            }
        }
    } catch {
        # Leave fields null; the log line will show "n/a".
    }
    return $state
}

function Format-State($s) {
    $m = if ($s.mtime) { $s.mtime.ToString('yyyy-MM-dd HH:mm') } else { 'n/a' }
    $e = if ($null -ne $s.expiryMin) { "{0}min" -f $s.expiryMin } else { 'n/a' }
    return "mtime=$m expiry=$e"
}

# --- resolve claude.exe (dynamic, newest version — mirrors ~/bin/claude) ---
function Resolve-ClaudeExe {
    $base = Join-Path $env:LOCALAPPDATA 'Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code'
    if (Test-Path $base) {
        # Sort by parsed version of the parent dir (e.g. "2.1.170"), NOT lexically — a
        # path string sorts "2.1.170" before "2.1.99", which would pick the older binary.
        # Mirrors `sort -V` in ~/bin/claude. Non-version dir names fall back to 0.0.0.
        $exe = Get-ChildItem -Path $base -Filter 'claude.exe' -Recurse -ErrorAction SilentlyContinue |
               Sort-Object { try { [version]$_.Directory.Name } catch { [version]'0.0.0' } } |
               Select-Object -Last 1
        if ($exe) { return $exe.FullName }
    }
    # Fallback to PATH resolution if the package layout ever changes.
    $cmd = Get-Command claude -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

# --- main ----------------------------------------------------------------
$before = Get-TokenState
$claudeExe = Resolve-ClaudeExe

if (-not $claudeExe) {
    Write-Log ("ERROR claude.exe not found | before: {0}" -f (Format-State $before))
    exit 0
}

$exitCode = $null
$sw = [System.Diagnostics.Stopwatch]::StartNew()
# Run the CLI as a directly-killable child so a hung claude.exe is never orphaned past this
# run. ProcessStartInfo (not Start-Process -PassThru, whose .ExitCode is unreliable) gives a
# dependable exit code and a Kill handle. stdout/stderr are drained async into the void to
# avoid a full-pipe deadlock; stdin is closed for EOF so `-p` (prompt is an argument) never
# blocks. We rely on the before/after token state — not the output — for the REFRESHED signal.
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $claudeExe
    $psi.Arguments = "-p ok --model $Model"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Close()
    [void]$proc.StandardOutput.ReadToEndAsync()
    [void]$proc.StandardError.ReadToEndAsync()
    if ($proc.WaitForExit($TimeoutSeconds * 1000)) {
        $exitCode = $proc.ExitCode
    } else {
        try { $proc.Kill() } catch { }
        $exitCode = 'TIMEOUT'
    }
} catch {
    $exitCode = "EXC:$($_.Exception.GetType().Name)"
}
$sw.Stop()

$after = Get-TokenState

# Did the token actually refresh? Either the file was rewritten or expiry moved forward.
$refreshed = $false
if ($before.mtime -and $after.mtime -and ($after.mtime -gt $before.mtime)) { $refreshed = $true }
if (($null -ne $before.expiryMin) -and ($null -ne $after.expiryMin) -and ($after.expiryMin -gt ($before.expiryMin + 1))) { $refreshed = $true }
$refreshTag = if ($refreshed) { 'REFRESHED' } else { 'no-change' }

Write-Log ("claude_exit={0} dur={1}s | before[{2}] after[{3}] | {4}" -f `
    $exitCode, [math]::Round($sw.Elapsed.TotalSeconds, 1), (Format-State $before), (Format-State $after), $refreshTag)

exit 0
