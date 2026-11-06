@echo off
rem Windows wrapper for the Claude Code CLI.
rem Resolves the current version directory dynamically so this script
rem does not need to be updated after app upgrades. It only finds and runs the
rem binary, and endlocal restores the caller's environment before the binary
rem starts, so authentication is the binary's own business.
rem
rem Looks in the current install location, then the older MSIX one. Within a
rem location it picks the newest version directory by numeric sort (2.1.170 beats
rem 2.1.99). Each holds claude.exe directly or one level down in a hash directory,
rem and any hash in the newest version will do.
rem
rem Must stay CRLF (see .gitattributes): cmd.exe can miss labels in an LF-only file.
rem No delayed expansion: it would eat any "!" in the arguments passed through %*.
setlocal
set "CURRENT_BASE=%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\claude-code"
set "OLDER_BASE=%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude-code"
set "CLAUDE_BIN="
call :find_newest "%CURRENT_BASE%"
if not defined CLAUDE_BIN call :find_newest "%OLDER_BASE%"
if defined CLAUDE_BIN goto :run
echo claude: could not find Claude Code binary under %CURRENT_BASE% or %OLDER_BASE% 1>&2
exit /b 1

:run
endlocal & "%CLAUDE_BIN%" %*
exit /b %errorlevel%

:find_newest
set "BEST_KEY="
for /d %%d in ("%~1\*") do call :consider "%%~fd" "%%~nxd"
exit /b 0

:consider
rem %1 is the version directory and %2 its name. A name holding anything but
rem digits and dots is not a version: this loop body runs only for such a name.
for /f "delims=0123456789." %%x in ("%~2") do exit /b 0
rem Zero-pad each part so a plain string comparison sorts numerically.
for /f "tokens=1-4 delims=." %%a in ("%~2") do (
    set "PART1=0000000000%%a"
    set "PART2=0000000000%%b"
    set "PART3=0000000000%%c"
    set "PART4=0000000000%%d"
)
set "KEY=%PART1:~-10%.%PART2:~-10%.%PART3:~-10%.%PART4:~-10%"
if defined BEST_KEY if "%KEY%" leq "%BEST_KEY%" exit /b 0
set "EXE="
if exist "%~1\claude.exe" set "EXE=%~1\claude.exe"
if not defined EXE for /d %%h in ("%~1\*") do if not defined EXE if exist "%%~fh\claude.exe" set "EXE=%%~fh\claude.exe"
if not defined EXE exit /b 0
set "CLAUDE_BIN=%EXE%"
set "BEST_KEY=%KEY%"
exit /b 0
