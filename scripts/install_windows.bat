@echo off
REM Double-click this to run install_windows.ps1 without dealing with
REM PowerShell's execution policy yourself. New in the Windows fork of
REM Shriinivas/inkmcp, 2026-07. AGPL-3.0, same as upstream.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
echo.
pause
