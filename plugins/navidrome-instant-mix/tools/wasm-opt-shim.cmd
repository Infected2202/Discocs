@echo off
setlocal enabledelayedexpansion

if "%~1"=="--version" (
    echo wasm-opt version 123
    exit /b 0
)
if "%~1"=="-h" (
    echo wasm-opt-shim
    exit /b 0
)
if "%~1"=="--help" (
    echo wasm-opt-shim
    exit /b 0
)

set "input="
set "output="

:args
if "%~1"=="" goto done
if "%~1"=="-o" goto set_output
if "%~1"=="--output" goto set_output
set "arg=%~1"
if /I "!arg:~0,3!"=="-o=" (
    set "output=!arg:~3!"
    shift
    goto args
)
if /I "!arg:~0,9!"=="--output=" (
    set "output=!arg:~9!"
    shift
    goto args
)
if exist "%~1" (
    set "input=%~1"
)
shift
goto args

:set_output
shift
set "output=%~1"
shift
goto args

:done
if not defined input (
    echo wasm-opt-shim: input wasm was not found 1>&2
    exit /b 1
)
if not defined output (
    set "output=%input%"
)
if /I not "%input%"=="%output%" (
    copy /Y "%input%" "%output%" >nul
    exit /b !errorlevel!
)
exit /b 0
