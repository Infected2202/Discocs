"""Задача «синхронизировать лейблы»: найти новые лейблы и перепроверить те, у кого появились релизы.

Что уже искали, лежит в ``label_sync_state``: найденный лейбл больше не ищется, ненайденный —
только когда изменился набор его штрихкодов и ISRC (докачали релизы). Задача не входит в общую
очередь (``NON_BLOCKING_JOB_KINDS``): она пишет только данные лейблов и никому не мешает.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings
from app.models import LabelMetadata
from app.services.label_sync.clients import (
    BeatportClient,
    DiscogsClient,
    ServiceAuthError,
    WebClient,
    new_http_client,
)
from app.services.label_sync.credentials import BEATPORT_SECRET, DISCOGS_SECRET, load_secret, save_secret
from app.services.label_sync.http_cache import HttpCache
from app.services.label_sync.resolver import LabelResolver, LabelResult
from app.services.label_sync.tags import read_barcode
from app.services.labels import LabelImageError, decode_label_image_bytes, save_label_metadata
from app.store import Store
from app.store.label_sync import (
    LABEL_SYNC_ERROR,
    LABEL_SYNC_FOUND,
    LABEL_SYNC_NOT_FOUND,
    LabelSyncCandidate,
    LabelTrackSource,
)

logger = logging.getLogger(__name__)

LABEL_SYNC_JOB_KIND = "label-sync"
WORKERS = 3  # пока один лейбл ждёт Beatport или Википедию, Discogs не простаивает
CACHE_FILE = "label_sync_cache.db"


class LabelSyncUnavailable(RuntimeError):
    """Не хватает доступов — задачу не запускаем."""


@dataclass(frozen=True)
class LabelKeys:
    barcodes: tuple[str, ...]
    isrcs: tuple[str, ...]

    @property
    def hash(self) -> str:
        payload = json.dumps([sorted(self.barcodes), sorted(self.isrcs)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class LabelSyncSummary:
    checked: int = 0
    found: int = 0
    not_found: int = 0
    errors: int = 0
    images: int = 0
    descriptions: int = 0

    def message(self) -> str:
        return (f"Checked {self.checked} labels: found {self.found} (images {self.images}, "
                f"descriptions {self.descriptions}), not found {self.not_found}, errors {self.errors}")


# ---------- какие лейблы и с какими ключами ----------

def label_keys(
    store: Store,
    label_ids: list[int],
    read: Callable[[Path], str | None] = read_barcode,
) -> dict[int, LabelKeys]:
    """Штрихкоды (из тегов) и ISRC (из Navidrome) лейблов; сначала — из маленьких релизов.

    EP и синглы находятся на Beatport/Discogs лучше больших сборников, а у сборника часто
    другой UPC дистрибьютора.
    """
    sources = store.label_track_sources(label_ids)
    barcodes = _barcodes_for(store, sources, read)
    by_label: dict[int, dict[str, int]] = defaultdict(dict)
    isrc_by_label: dict[int, dict[int, tuple[int, str]]] = defaultdict(dict)
    for src in sources:
        code = barcodes.get(src.track_id)
        if code:
            size = by_label[src.label_id].get(code, src.release_track_count)
            by_label[src.label_id][code] = min(size, src.release_track_count)
        if src.isrc and src.release_id not in isrc_by_label[src.label_id]:
            # одного ISRC на релиз достаточно: треки релиза голосуют за один и тот же лейбл
            isrc_by_label[src.label_id][src.release_id] = (src.release_track_count, src.isrc[0])
    out: dict[int, LabelKeys] = {}
    for label_id in label_ids:
        codes = by_label.get(label_id, {})
        isrcs = sorted(isrc_by_label.get(label_id, {}).values())
        out[label_id] = LabelKeys(
            barcodes=tuple(sorted(codes, key=lambda c: (codes[c], c))),
            isrcs=tuple(dict.fromkeys(code for _size, code in isrcs)),
        )
    return out


def _barcodes_for(
    store: Store, sources: list[LabelTrackSource], read: Callable[[Path], str | None],
) -> dict[int, str]:
    """Штрихкод трека: из кэша, если файл не менялся, иначе — из тегов."""
    paths = {src.track_id: src.path for src in sources if src.path}
    cached = store.cached_barcodes(paths)
    result: dict[int, str] = {}
    fresh: list[tuple[int, str | None, int | None, int | None]] = []
    for track_id, path in paths.items():
        try:
            stat = os.stat(path)
        except OSError:
            continue
        size, mtime = int(stat.st_size), int(stat.st_mtime)
        hit = cached.get(track_id)
        if hit is not None and hit[1] == size and hit[2] == mtime:
            code = hit[0]
        else:
            code = read(Path(path))
            fresh.append((track_id, code, size, mtime))
        if code:
            result[track_id] = code
    if fresh:
        store.save_barcodes(fresh)
    return result


def labels_to_sync(
    candidates: list[LabelSyncCandidate],
    keys: Callable[[list[int]], dict[int, LabelKeys]],
    *,
    retry_not_found: bool,
    only_label_id: int | None,
    remember_baseline: Callable[[int, str], None],
) -> list[tuple[LabelSyncCandidate, LabelKeys]]:
    """Кого искать: новых, с ошибкой, ненайденных с новыми ключами (или все ненайденные по кнопке)."""
    if only_label_id is not None:
        chosen = [c for c in candidates if c.label_id == only_label_id]
        known = keys([c.label_id for c in chosen])
        return [(c, known[c.label_id]) for c in chosen]
    pending = [c for c in candidates if c.status != LABEL_SYNC_FOUND]
    known = keys([c.label_id for c in pending])
    todo = []
    for candidate in pending:
        current = known[candidate.label_id]
        if candidate.status == LABEL_SYNC_NOT_FOUND and not retry_not_found:
            if candidate.keys_hash is None:
                # Отмечен старым скриптом: только запомнить ключи, искать — когда появятся новые.
                remember_baseline(candidate.label_id, current.hash)
                continue
            if candidate.keys_hash == current.hash:
                continue
        todo.append((candidate, current))
    return todo


# ---------- один лейбл ----------

def sync_one_label(
    store: Store,
    data_dir: Path,
    resolver: LabelResolver,
    candidate: LabelSyncCandidate,
    keys: LabelKeys,
    summary: LabelSyncSummary,
    lock: threading.Lock,
) -> None:
    def titles() -> list[str]:
        return [row.release.title for row in store.label_releases(candidate.label_id)]

    result = resolver.resolve(candidate.name, keys.barcodes, keys.isrcs, titles)
    if not result.found:
        store.set_label_sync_state(candidate.label_id, LABEL_SYNC_NOT_FOUND, keys_hash=keys.hash)
        with lock:
            summary.not_found += 1
        return
    image = _image(resolver, result)
    save_label_metadata(store, data_dir, LabelMetadata(
        name=candidate.name,
        image_source=result.image_source if image else None,
        description=result.description,
        description_source=result.description_source,
        links=result.links,
        external_ids=result.external_ids,
    ), image)
    store.set_label_sync_state(
        candidate.label_id, LABEL_SYNC_FOUND, keys_hash=keys.hash,
        beatport_id=result.external_ids.get("beatport"), discogs_id=result.external_ids.get("discogs"),
    )
    with lock:
        summary.found += 1
        summary.images += image is not None
        summary.descriptions += bool(result.description)


def _image(resolver: LabelResolver, result: LabelResult):
    if not result.image_url:
        return None
    payload = resolver.web.download(result.image_url)
    if payload is None:
        return None
    try:
        return decode_label_image_bytes(payload)
    except LabelImageError:
        logger.warning("Label image is not a picture url=%s", result.image_url)
        return None


# ---------- задача ----------

def build_resolver(store: Store, settings: Settings, http: httpx.Client, cache: HttpCache) -> LabelResolver:
    beatport = load_secret(store, settings, BEATPORT_SECRET)
    discogs = load_secret(store, settings, DISCOGS_SECRET)
    if not beatport:
        raise LabelSyncUnavailable("Sign in to Beatport first")
    if not discogs or not discogs.get("token"):
        raise LabelSyncUnavailable("Set the Discogs token first")
    username = beatport.get("username")

    def keep_token(token: dict[str, object]) -> None:
        save_secret(store, settings, BEATPORT_SECRET, {**token, "username": username})

    return LabelResolver(
        BeatportClient(beatport, keep_token, cache, http),
        DiscogsClient(str(discogs["token"]), cache, http),
        WebClient(cache, http),
    )


def run_label_sync(
    store: Store,
    settings: Settings,
    resolver: LabelResolver,
    *,
    retry_not_found: bool = False,
    only_label_id: int | None = None,
    progress: Callable[[int, int, str | None], None] = lambda done, total, current: None,
    cancelled: Callable[[], bool] = lambda: False,
    workers: int = WORKERS,
    read: Callable[[Path], str | None] = read_barcode,
) -> LabelSyncSummary:
    store.seed_label_sync_state()
    todo = labels_to_sync(
        store.label_sync_candidates(),
        lambda ids: label_keys(store, ids, read),
        retry_not_found=retry_not_found,
        only_label_id=only_label_id,
        remember_baseline=store.set_label_sync_keys_hash,
    )
    summary = LabelSyncSummary()
    lock = threading.Lock()
    progress(0, len(todo), None)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_guarded, store, settings.data_dir, resolver, candidate, keys, summary, lock, cancelled): candidate
            for candidate, keys in todo
        }
        for future in as_completed(futures):
            candidate = futures[future]
            try:
                future.result()
            except ServiceAuthError:
                # Вход истёк — остальные лейблы всё равно не найдутся: прерываем задачу целиком.
                for pending in futures:
                    pending.cancel()
                raise
            with lock:
                summary.checked += 1
                done = summary.checked
            progress(done, len(todo), candidate.name)
            if cancelled():
                for pending in futures:
                    pending.cancel()
                break
    return summary


def _guarded(store, data_dir, resolver, candidate, keys, summary, lock, cancelled) -> None:
    if cancelled():
        return
    try:
        sync_one_label(store, data_dir, resolver, candidate, keys, summary, lock)
    except ServiceAuthError:
        raise
    except Exception as exc:  # noqa: BLE001 — один лейбл не должен ронять всю синхронизацию
        logger.exception("Label sync failed label_id=%s name=%s", candidate.label_id, candidate.name)
        store.set_label_sync_state(candidate.label_id, LABEL_SYNC_ERROR, keys_hash=keys.hash, error=str(exc)[:500])
        with lock:
            summary.errors += 1


def label_sync_job(job_id: str, retry_not_found: bool, only_label_id: int | None) -> None:
    """Фоновая задача из ``POST /api/v1/jobs/label-sync``."""
    from app.api.deps import context  # noqa: PLC0415 — как у остальных фоновых задач
    from app.services.jobs import finish_job, update_job  # noqa: PLC0415
    from app.state import JOBS, JOBS_LOCK  # noqa: PLC0415

    def cancelled() -> bool:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            return job is None or getattr(job, "status", None) == "cancelled"

    def progress(done: int, total: int, current: str | None) -> None:
        if not cancelled():
            update_job(job_id, status="running", done=done, total=total, current=current,
                       message=f"Syncing labels {done}/{total}")

    http = new_http_client()
    cache = None
    try:
        store, settings = context()
        cache = HttpCache(settings.data_dir / CACHE_FILE)
        resolver = build_resolver(store, settings, http, cache)
        summary = run_label_sync(store, settings, resolver, retry_not_found=retry_not_found,
                                 only_label_id=only_label_id, progress=progress, cancelled=cancelled)
        if not cancelled():
            finish_job(job_id, "completed", summary.message())
    except (LabelSyncUnavailable, ServiceAuthError) as exc:
        finish_job(job_id, "failed", str(exc))
    except Exception as exc:  # noqa: BLE001 — статус задачи должен закрыться в любом случае
        logger.exception("Label sync job failed job_id=%s", job_id)
        finish_job(job_id, "failed", str(exc))
    finally:
        http.close()
        if cache is not None:
            cache.close()
