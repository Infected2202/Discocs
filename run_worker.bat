@echo off
setlocal

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv\Scripts\python.exe
  echo Create the worker environment first:
  echo   py -3.11 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e ".[dev,essentia]"
  exit /b 1
)

rem URL of the discocs server reachable from this worker machine.
if "%DISCOCS_WORKER_SERVER%"=="" set "DISCOCS_WORKER_SERVER=http://127.0.0.1:8711"

rem Stable worker name shown in the web UI under Jobs / Workers.
if "%DISCOCS_WORKER_ID%"=="" set "DISCOCS_WORKER_ID=gpu-worker-1"

rem Worker capabilities. By default it accepts the full analyze pipeline.
if "%DISCOCS_WORKER_EMBEDDING_MODEL%"=="" set "DISCOCS_WORKER_EMBEDDING_MODEL=discogs_multi"
if "%DISCOCS_WORKER_AUDIO_FEATURE_MODEL%"=="" set "DISCOCS_WORKER_AUDIO_FEATURE_MODEL=audio_features_v1"
if "%DISCOCS_WORKER_HEAD_MODEL%"=="" set "DISCOCS_WORKER_HEAD_MODEL=discogs-effnet-heads"

if "%DISCOCS_WORKER_CLAIM_BATCH_SIZE%"=="" set "DISCOCS_WORKER_CLAIM_BATCH_SIZE=32"
if "%DISCOCS_WORKER_MAX_INFLIGHT_TASKS%"=="" set "DISCOCS_WORKER_MAX_INFLIGHT_TASKS=128"
if "%DISCOCS_WORKER_DOWNLOAD_CONCURRENCY%"=="" set "DISCOCS_WORKER_DOWNLOAD_CONCURRENCY=8"
if "%DISCOCS_WORKER_SUBMIT_BATCH_SIZE%"=="" set "DISCOCS_WORKER_SUBMIT_BATCH_SIZE=32"
if "%DISCOCS_WORKER_LEASE_SECONDS%"=="" set "DISCOCS_WORKER_LEASE_SECONDS=900"
if "%DISCOCS_WORKER_POLL_SECONDS%"=="" set "DISCOCS_WORKER_POLL_SECONDS=5"

if "%DISCOCS_DATA_DIR%"=="" set "DISCOCS_DATA_DIR=data"
if "%DISCOCS_MODEL_DIR%"=="" set "DISCOCS_MODEL_DIR=models"
if "%DISCOCS_INDEX_DIR%"=="" set "DISCOCS_INDEX_DIR=data"
if "%DISCOCS_DB_PATH%"=="" set "DISCOCS_DB_PATH=data\worker-local.db"
if "%DISCOCS_AUDIO_LOADER%"=="" set "DISCOCS_AUDIO_LOADER=ffmpeg"
if "%TF_CPP_MIN_LOG_LEVEL%"=="" set "TF_CPP_MIN_LOG_LEVEL=3"

echo Starting discocs worker
echo Server: %DISCOCS_WORKER_SERVER%
echo Worker: %DISCOCS_WORKER_ID%
echo Models: %DISCOCS_WORKER_EMBEDDING_MODEL%, %DISCOCS_WORKER_AUDIO_FEATURE_MODEL%, %DISCOCS_WORKER_HEAD_MODEL%
echo Models dir: %DISCOCS_MODEL_DIR%

".venv\Scripts\python.exe" -m app.cli worker ^
  --server "%DISCOCS_WORKER_SERVER%" ^
  --worker-id "%DISCOCS_WORKER_ID%" ^
  --models "%DISCOCS_WORKER_EMBEDDING_MODEL%" ^
  --models "%DISCOCS_WORKER_AUDIO_FEATURE_MODEL%" ^
  --models "%DISCOCS_WORKER_HEAD_MODEL%" ^
  --claim-batch-size "%DISCOCS_WORKER_CLAIM_BATCH_SIZE%" ^
  --max-inflight-tasks "%DISCOCS_WORKER_MAX_INFLIGHT_TASKS%" ^
  --download-concurrency "%DISCOCS_WORKER_DOWNLOAD_CONCURRENCY%" ^
  --submit-batch-size "%DISCOCS_WORKER_SUBMIT_BATCH_SIZE%" ^
  --lease-seconds "%DISCOCS_WORKER_LEASE_SECONDS%" ^
  --poll-seconds "%DISCOCS_WORKER_POLL_SECONDS%"

endlocal
