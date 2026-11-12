@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap.ps1" %*
set LVE_RESULT=%ERRORLEVEL%
if not "%LVE_RESULT%"=="0" echo Setup incomplete. See the displayed error and run this entry again.
pause
exit /b %LVE_RESULT%
