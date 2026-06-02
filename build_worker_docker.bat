@echo off
setlocal

set "ROOT_DIR=%~dp0"
set "ROOT_DIR=%ROOT_DIR:~0,-1%"

if "%DISCOCS_WORKER_IMAGE%"=="" set "DISCOCS_WORKER_IMAGE=discocs-worker:dev"
if "%DISCOCS_WORKER_DOCKERFILE%"=="" set "DISCOCS_WORKER_DOCKERFILE=%ROOT_DIR%\Dockerfile.worker"

docker version >nul 2>nul
if errorlevel 1 (
  echo Docker is not available. Start Docker Desktop and try again.
  exit /b 1
)

echo Building %DISCOCS_WORKER_IMAGE%
echo Context: %ROOT_DIR%
echo Dockerfile: %DISCOCS_WORKER_DOCKERFILE%

docker build ^
  -f "%DISCOCS_WORKER_DOCKERFILE%" ^
  -t "%DISCOCS_WORKER_IMAGE%" ^
  "%ROOT_DIR%"

if errorlevel 1 (
  echo.
  echo Docker build failed.
  echo If the build context is a Samba/UNC path and Docker cannot read it,
  echo copy or mirror the repo to a local Windows folder, or run this script from
  echo a mapped drive that Docker Desktop can access.
  exit /b 1
)

echo.
echo Built %DISCOCS_WORKER_IMAGE%
endlocal
