@echo off
rem  Update the tree from GitHub, on Windows.
rem
rem  Double-click this file, or run it from a Command Prompt on the
rem  CIRCUITPY drive.  It hands over to update.sh, which does the actual
rem  work, so there is only ever one copy of that logic to keep right.
rem
rem  It needs the bash that comes with Git for Windows.  If you cloned this
rem  repository on this machine you already have it.

setlocal

rem  Work from the drive this file is on, whatever directory the prompt
rem  started in.  It also means bash is handed a plain relative name
rem  instead of a Windows path, which it would read as having no directory
rem  part at all.
cd /d "%~dp0"

if not exist "update.sh" (
    echo Could not find update.sh next to this file.
    echo Both files should be in the root of the CIRCUITPY drive.
    goto :finish_fail
)

rem  Look for Git's bash in the usual places, then wherever git itself is.
set "BASH="
call :try "%ProgramFiles%\Git\bin\bash.exe"
call :try "%ProgramW6432%\Git\bin\bash.exe"
call :try "%ProgramFiles(x86)%\Git\bin\bash.exe"
call :try "%LocalAppData%\Programs\Git\bin\bash.exe"
call :try "%UserProfile%\scoop\apps\git\current\bin\bash.exe"
if not defined BASH call :from_path

if not defined BASH (
    echo.
    echo Could not find Git Bash on this computer.
    echo.
    echo update.sh needs it. Install Git for Windows, which includes it:
    echo     https://git-scm.com/download/win
    echo.
    echo If Git is already installed somewhere unusual, you can run the
    echo script yourself from a Git Bash window:
    echo     bash update.sh
    goto :finish_fail
)

"%BASH%" update.sh %*
set "RESULT=%ERRORLEVEL%"
goto :finish

:try
rem  Record the first candidate that exists.
if defined BASH goto :eof
if exist %1 set "BASH=%~1"
goto :eof

:from_path
rem  Fall back to whatever is on PATH: bash directly, else alongside git.
for /f "delims=" %%F in ('where bash 2^>nul') do if not defined BASH set "BASH=%%F"
if defined BASH goto :eof
for /f "delims=" %%F in ('where git 2^>nul') do if not defined BASH call :beside_git "%%F"
goto :eof

:beside_git
rem  %~dp1 is ...\Git\cmd\ ; bash lives at ...\Git\bin\bash.exe
if exist "%~dp1..\bin\bash.exe" set "BASH=%~dp1..\bin\bash.exe"
goto :eof

:finish_fail
set "RESULT=1"

:finish
rem  Pause only when double-clicked, so running it from a prompt is quiet.
echo %cmdcmdline% | find /i "%~nx0" >nul 2>&1
if not errorlevel 1 (
    echo.
    pause
)
exit /b %RESULT%
