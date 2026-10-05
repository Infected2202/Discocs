"""Синхронизация лейблов из админки: доступы к Beatport/Discogs, запуск задачи, состояние."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from app.api.deps import api_error, context
from app.integration_secrets import IntegrationSecretError
from app.schemas.requests import BeatportLoginRequest, DiscogsAppRequest, LabelSyncRequest
from app.services.jobs import create_job, update_job
from app.services.label_sync.clients import ServiceAuthError, new_http_client
from app.services.label_sync.credentials import (
    BEATPORT_SECRET,
    DISCOGS_SECRET,
    connect_beatport,
    connect_discogs,
    credentials_status,
)
from app.services.label_sync.job import LABEL_SYNC_JOB_KIND, label_sync_job
from app.state import JOBS, JOBS_LOCK

router = APIRouter(prefix="/api/v1")


def _running_label_sync() -> str | None:
    with JOBS_LOCK:
        for job in JOBS.values():
            if getattr(job, "kind", None) == LABEL_SYNC_JOB_KIND and getattr(job, "status", None) in {"queued", "running"}:
                return str(job.id)
    return None


@router.get("/label-sync")
def api_v1_label_sync_status() -> dict[str, object]:
    store, settings = context()
    # Лейблы, заполненные старым скриптом, считаются обработанными и до первого запуска.
    store.seed_label_sync_state()
    return {
        **credentials_status(store, settings),
        "labels": store.label_sync_counts(),
        "not_found": store.label_sync_not_found(),
        "running_job_id": _running_label_sync(),
    }


@router.post("/label-sync/beatport", response_model=None)
def api_v1_label_sync_beatport_login(request: BeatportLoginRequest) -> dict[str, object] | JSONResponse:
    """Вход в Beatport: сервер получает токены, пароль не сохраняется."""
    store, settings = context()
    with new_http_client() as http:
        try:
            connect_beatport(store, settings, http, request.username, request.password)
        except ServiceAuthError as exc:
            return api_error(400, "beatport_login_failed", str(exc))
        except IntegrationSecretError as exc:
            return api_error(409, "no_server_key", str(exc))
    return credentials_status(store, settings)


@router.delete("/label-sync/beatport")
def api_v1_label_sync_beatport_logout() -> dict[str, object]:
    store, settings = context()
    store.delete_integration_secret(BEATPORT_SECRET)
    return credentials_status(store, settings)


@router.put("/label-sync/discogs", response_model=None)
def api_v1_label_sync_discogs_app(request: DiscogsAppRequest) -> dict[str, object] | JSONResponse:
    """Ключ и секрет приложения Discogs; перед сохранением проверяются одним поиском."""
    store, settings = context()
    with new_http_client() as http:
        try:
            connect_discogs(store, settings, http, request.key, request.secret)
        except ServiceAuthError as exc:
            return api_error(400, "discogs_rejected", str(exc))
        except IntegrationSecretError as exc:
            return api_error(409, "no_server_key", str(exc))
    return credentials_status(store, settings)


@router.delete("/label-sync/discogs")
def api_v1_label_sync_discogs_forget() -> dict[str, object]:
    store, settings = context()
    store.delete_integration_secret(DISCOGS_SECRET)
    return credentials_status(store, settings)


@router.post("/jobs/label-sync", response_model=None)
def api_v1_start_label_sync(
    request: LabelSyncRequest, background_tasks: BackgroundTasks,
) -> dict[str, object] | JSONResponse:
    """Запустить сразу: в общую очередь задач не встаёт (``NON_BLOCKING_JOB_KINDS``)."""
    running = _running_label_sync()
    if running is not None:
        return api_error(409, "already_running", f"Label sync is already running: {running}")
    store, settings = context()
    status = credentials_status(store, settings)
    if not (status["beatport"] or {}).get("connected") or not (status["discogs"] or {}).get("connected"):
        return api_error(409, "not_configured", "Connect Beatport and set the Discogs key and secret first")
    job_id = create_job(LABEL_SYNC_JOB_KIND, "Waiting to sync labels")
    update_job(job_id, status="running")
    background_tasks.add_task(label_sync_job, job_id, request.retry_not_found, request.label_id, request.recheck_found)
    return {"status": "accepted", "job_id": job_id}
