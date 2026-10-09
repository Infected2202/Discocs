"""Инструменты рабочей машины (docs/tools.md): админка ставит задачи, воркер на ПК (tools/agent) их берёт.

Воркер ходит сюда сам — discocs к ПК не подключается. Ждёт задачу долгим опросом: запрос висит, пока
задачи нет, — в простое воркер не тратит ни процессор, ни сеть.
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import api_error, context
from app.schemas.requests import (
    DescribeNextRequest,
    DescribeResultRequest,
    ToolJobControlRequest,
    ToolJobCreateRequest,
    ToolJobProgressRequest,
    ToolPollRequest,
)

router = APIRouter(prefix="/api/v1")

# Воркер переспрашивает сразу после ответа, долгий опрос — до минуты: дольше молчит — не на связи.
WORKER_ONLINE_SECONDS = 90
_POLL_STEP_SECONDS = 2.0
_WORKER_ID = re.compile(r"^[\w.-]{1,64}$")
_JOB_NOT_FOUND = "Tool job not found"


def _online(seen_at: str) -> bool:
    try:
        seen = datetime.fromisoformat(seen_at)
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return (datetime.now(UTC) - seen).total_seconds() <= WORKER_ONLINE_SECONDS


# --- воркер ------------------------------------------------------------------


@router.post("/tools/workers/{worker_id}/poll", response_model=None)
async def api_v1_tool_worker_poll(worker_id: str, request: ToolPollRequest) -> dict[str, object] | JSONResponse:
    """Воркер сообщает состояние и ждёт задачу до ``wait`` секунд; job — None, если задачи так и не было."""
    if not _WORKER_ID.match(worker_id):
        return api_error(400, "invalid_worker", "Worker id: letters, digits, '.', '-', '_'")
    store, _settings = context()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + request.wait
    while True:
        await run_in_threadpool(store.tool_worker_seen, worker_id, request.state)
        job = await run_in_threadpool(store.claim_tool_job, worker_id, request.tools)
        if job is not None or loop.time() >= deadline:
            return {"job": job}
        await asyncio.sleep(min(_POLL_STEP_SECONDS, max(0.0, deadline - loop.time())))


@router.post("/tools/jobs/{job_id}/progress", response_model=None)
def api_v1_tool_job_progress(job_id: int, request: ToolJobProgressRequest) -> dict[str, object] | JSONResponse:
    """Прогресс от воркера; в ответ — чего хочет админка: run, pause или cancel."""
    store, _settings = context()
    control = store.update_tool_job(job_id, status=request.status, progress=request.progress, message=request.message)
    if control is None:
        return api_error(404, "not_found", _JOB_NOT_FOUND)
    return {"control": control}


@router.post("/tools/jobs/{job_id}/describe/next", response_model=None)
def api_v1_describe_next(job_id: int, request: DescribeNextRequest) -> dict[str, object] | JSONResponse:
    """Следующие лейблы задачи describe с тем, что о них знает библиотека."""
    store, _settings = context()
    if store.tool_job(job_id) is None:
        return api_error(404, "not_found", _JOB_NOT_FOUND)
    return {"labels": store.describe_next(job_id, limit=request.limit, exclude=request.exclude)}


@router.post("/tools/describe/results", response_model=None)
def api_v1_describe_result(request: DescribeResultRequest) -> dict[str, object] | JSONResponse:
    """Итог агента: текст сразу становится описанием (кроме редакционного), «не нашлось» ничего не стирает."""
    store, _settings = context()
    outcome = store.save_agent_description(
        request.label_id,
        job_id=request.job_id,
        status=request.status,
        description=request.description,
        sources=[source.model_dump(exclude_none=True) for source in request.sources],
        model=request.model,
        note=request.note,
    )
    if outcome is None:
        return api_error(404, "not_found", "Label not found")
    return {"outcome": outcome}


# --- админка -----------------------------------------------------------------


@router.get("/tools")
def api_v1_tools() -> dict[str, object]:
    """Воркеры (на связи ли, что делают) и последние задачи."""
    store, _settings = context()
    workers = [{**worker, "online": _online(str(worker["seen_at"]))} for worker in store.tool_workers()]
    return {"workers": workers, "jobs": store.tool_jobs(limit=20)}


@router.post("/tools/jobs", response_model=None)
def api_v1_tool_job_create(request: ToolJobCreateRequest) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    if request.tool == "describe":
        if request.action != "run":
            return api_error(400, "invalid_action", "describe supports only 'run'")
        if store.active_tool_job("describe") is not None:
            return api_error(409, "already_running", "A describe job is already queued or running")
        label_ids = store.describe_label_ids(
            request.scope, min_releases=request.min_releases, label_ids=request.label_ids
        )
        if not label_ids:
            return api_error(400, "nothing_to_do", "No labels match this scope")
        params: dict[str, object] = {"scope": request.scope, "min_releases": request.min_releases,
                                     "label_ids": label_ids}
        job_id = store.create_tool_job("describe", "run", params)
    else:
        if request.action not in ("start", "stop"):
            return api_error(400, "invalid_action", "music-fill supports 'start' and 'stop'")
        job_id = store.create_tool_job(request.tool, request.action, {})
    return {"job": store.tool_job(job_id)}


@router.post("/tools/jobs/{job_id}/control", response_model=None)
def api_v1_tool_job_control(job_id: int, request: ToolJobControlRequest) -> dict[str, object] | JSONResponse:
    store, _settings = context()
    current = store.tool_job(job_id)
    if current is None:
        return api_error(404, "not_found", _JOB_NOT_FOUND)
    seen = {str(worker["id"]): str(worker["seen_at"]) for worker in store.tool_workers()}
    worker_id = current["worker_id"]
    worker_online = worker_id is None or (str(worker_id) in seen and _online(seen[str(worker_id)]))
    job = store.set_tool_job_control(job_id, request.control, worker_online=worker_online)
    if job is None:
        return api_error(404, "not_found", _JOB_NOT_FOUND)
    params = dict(job["params"]) if isinstance(job["params"], dict) else {}
    params.pop("label_ids", None)
    return {"job": {**job, "params": params}}


@router.get("/tools/describe")
def api_v1_describe_overview(limit: int = 30, offset: int = 0) -> dict[str, object]:
    """Покрытие описаниями, текущая задача и последние тексты агента (выборочный просмотр)."""
    store, _settings = context()
    job = store.active_tool_job("describe")
    if job is not None:
        params = dict(job["params"]) if isinstance(job["params"], dict) else {}
        params.pop("label_ids", None)
        job = {**job, "params": params, "counts": store.describe_job_counts(int(job["id"]))}
    return {
        "stats": store.describe_stats(),
        "job": job,
        "recent": store.agent_descriptions(limit=max(1, min(limit, 200)), offset=max(0, offset)),
    }


@router.post("/tools/describe/{label_id}/revert", response_model=None)
def api_v1_describe_revert(label_id: int) -> dict[str, object] | JSONResponse:
    """Убрать текст агента — вернуть описание, бывшее до него (или никакого)."""
    store, _settings = context()
    if not store.revert_agent_description(label_id):
        return api_error(409, "not_agent", "The label description was not written by the agent")
    return {"reverted": True}
