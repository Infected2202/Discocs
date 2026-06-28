@echo off

setlocal EnableExtensions

cd /d "%~dp0"



echo [stop] Stopping Discocs Bot instances...



if exist "data\bot.pid" (

    set /p BOTPID=<data\bot.pid

    if defined BOTPID (

        echo   PID %BOTPID% ^(from data\bot.pid^)

        taskkill /PID %BOTPID% /F >nul 2>&1

    )

    del /f /q "data\bot.pid" >nul 2>&1

)



powershell -NoProfile -Command ^

  "$root = (Resolve-Path '%CD%').Path; " ^

  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | " ^

  "Where-Object { " ^

  "  $cl = $_.CommandLine; " ^

  "  if (-not $cl -or $cl -notlike '*-m bot.main*') { return $false }; " ^

  "  $cl -like \"*$root*\" -or ($_.ExecutablePath -and $_.ExecutablePath -like \"*$root*\") " ^

  "} | " ^

  "ForEach-Object { Write-Host ('  PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; " ^

  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | " ^

  "Where-Object { $_.CommandLine -like '*-m bot.main*' } | " ^

  "ForEach-Object { Write-Host ('  PID ' + $_.ProcessId + ' (bot.main)'); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"



echo [stop] Done.

endlocal

