@echo off
rem Windows wrapper for the Claude Code CLI.
rem Resolves the current version directory dynamically so this script
rem does not need to be updated after app upgrades.
setlocal EnableDelayedExpansion
set CLAUDE_BASE=%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code
for /f "tokens=*" %%d in ('dir /b /ad /o-d "%CLAUDE_BASE%" 2^>nul') do (
    if exist "%CLAUDE_BASE%\%%d\claude.exe" (
        "%CLAUDE_BASE%\%%d\claude.exe" %*
        exit /b %errorlevel%
    )
)
echo claude: could not find Claude Code binary under %CLAUDE_BASE% 1>&2
exit /b 1
