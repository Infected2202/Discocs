"""Dashboard service: shelf generation and mix refresh.

Extracted from app/main.py (some functions were accidentally removed in stage 4
and are restored here from git history).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread

from app.mixes import (
    dashboard_mix_generation_plan,
    ensure_dashboard_mixes,
)
from app.serializers.mixes import generated_mix_summary_dict
from app.serializers.search import (
    _discover_track_shelf_item,
    _release_shelf_item,
    _track_shelf_item,
)
from app.state import MIX_GENERATION_LOCK
from app.store import Store, row_to_track

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard shelf data sources
# ---------------------------------------------------------------------------

def _dashboard_generated_mixes(
    store: Store,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> tuple[list[dict[str, object]], int]:
    mixes = store.list_generated_mixes(statuses=["active", "saved"], limit=limit, offset=offset)
    items: list[dict[str, object]] = []
    for mix in mixes:
        summary = generated_mix_summary_dict(store, mix)
        item: dict[str, object] = {
            "id": f"generated_mix:{mix.id}",
            "entity_type": "generated_mix",
            "entity_id": mix.id,
            "title": summary["title"],
            "subtitle": summary["subtitle"],
            "artwork": summary["artwork"],
            "action": summary["action"],
            "play_action": summary["play_action"],
            "badges": [str(summary["track_count"]) + " tracks", str(mix.status)],
            "reason": "Generated from your taste regions",
        }
        if include_debug:
            item["debug"] = {
                "anchor": summary["anchor"],
                "score_summary": summary["score_summary"],
                "settings": summary["settings"],
            }
        items.append(item)
    return items, store.count_generated_mixes(statuses=["active", "saved"])


def _dashboard_recently_added(
    store: Store,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> tuple[list[dict[str, object]], int]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, COALESCE(r.added_at, MAX(COALESCE(t.added_at, t.created_at, rt.created_at, r.created_at))) AS added_at
            FROM releases r
            JOIN release_tracks rt ON rt.release_id = r.id
            JOIN tracks t ON t.id = rt.track_id
            WHERE t.missing_at IS NULL
            GROUP BY r.id
            ORDER BY added_at DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM (
                SELECT r.id
                FROM releases r
                JOIN release_tracks rt ON rt.release_id = r.id
                JOIN tracks t ON t.id = rt.track_id
                WHERE t.missing_at IS NULL
                GROUP BY r.id
            )
            """
        ).fetchone()
    items: list[dict[str, object]] = []
    for row in rows:
        release = store.get_release(int(row["id"]))
        if release is None:
            continue
        item = _release_shelf_item(release, "New in collection")
        if include_debug:
            item["debug"] = {"added_at": row["added_at"]}
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def _dashboard_listen_again(
    store: Store,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> tuple[list[dict[str, object]], int]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.liked, p.play_count, p.completion_count, p.replay_count, p.score,
                   p.last_played_at, p.last_completed_at, p.last_skipped_at
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND (
                    p.last_skipped_at IS NULL
                    OR p.liked = 1
                    OR COALESCE(p.last_completed_at, p.last_played_at, p.updated_at) >= p.last_skipped_at
                  )
            ORDER BY p.liked DESC,
                     COALESCE(p.last_completed_at, p.last_played_at, p.updated_at) DESC,
                     p.score DESC,
                     t.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND (
                    p.last_skipped_at IS NULL
                    OR p.liked = 1
                    OR COALESCE(p.last_completed_at, p.last_played_at, p.updated_at) >= p.last_skipped_at
                  )
            """
        ).fetchone()
    tracks = [row_to_track(row) for row in rows]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    items: list[dict[str, object]] = []
    for row, track in zip(rows, tracks, strict=True):
        if int(row["liked"] or 0):
            reason = "You liked this"
        elif int(row["replay_count"] or 0):
            reason = f"Replayed {int(row['replay_count'])} times"
        elif int(row["completion_count"] or 0):
            reason = f"Completed {int(row['completion_count'])} times"
        elif int(row["play_count"] or 0):
            reason = f"Played {int(row['play_count'])} times"
        else:
            reason = "Played before"
        item = _track_shelf_item(store, track, artists_by_track.get(track.id, []), reason)
        if include_debug:
            item["debug"] = {
                "score": row["score"],
                "last_played_at": row["last_played_at"],
                "last_completed_at": row["last_completed_at"],
                "last_skipped_at": row["last_skipped_at"],
            }
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def _dashboard_long_time_no_listen(
    store: Store,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> tuple[list[dict[str, object]], int]:
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.liked, p.completion_count, p.replay_count, p.score,
                   p.last_played_at, p.last_completed_at, p.last_skipped_at
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND p.last_played_at IS NOT NULL
              AND p.last_played_at < ?
              AND (p.last_skipped_at IS NULL OR p.last_skipped_at <= p.last_played_at OR p.liked = 1)
            ORDER BY p.liked DESC, p.last_played_at ASC, p.score DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            (cutoff, limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM user_track_preferences p
            JOIN tracks t ON t.id = p.track_id
            WHERE t.missing_at IS NULL
              AND p.disliked = 0
              AND (p.liked = 1 OR p.completion_count > 0 OR p.replay_count > 0 OR p.score > 0)
              AND p.last_played_at IS NOT NULL
              AND p.last_played_at < ?
              AND (p.last_skipped_at IS NULL OR p.last_skipped_at <= p.last_played_at OR p.liked = 1)
            """,
            (cutoff,),
        ).fetchone()
    tracks = [row_to_track(row) for row in rows]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    items: list[dict[str, object]] = []
    for row, track in zip(rows, tracks, strict=True):
        item = _track_shelf_item(store, track, artists_by_track.get(track.id, []), "Long time since last listen")
        if include_debug:
            item["debug"] = {"cutoff": cutoff, "last_played_at": row["last_played_at"], "score": row["score"]}
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def _dashboard_discover_random(
    store: Store,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> tuple[list[dict[str, object]], int]:
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT t.*
            FROM tracks t
            LEFT JOIN user_track_preferences p ON p.track_id = t.id
            WHERE t.missing_at IS NULL
              AND (p.track_id IS NULL OR (p.disliked = 0 AND p.play_count = 0 AND p.last_played_at IS NULL))
            ORDER BY RANDOM()
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM tracks t
            LEFT JOIN user_track_preferences p ON p.track_id = t.id
            WHERE t.missing_at IS NULL
              AND (p.track_id IS NULL OR (p.disliked = 0 AND p.play_count = 0 AND p.last_played_at IS NULL))
            """
        ).fetchone()
    tracks = [row_to_track(row) for row in rows]
    artists_by_track = store.artists_for_tracks([track.id for track in tracks])
    items: list[dict[str, object]] = []
    for track in tracks:
        item = _discover_track_shelf_item(store, track, artists_by_track.get(track.id, []), "Never played before")
        if include_debug:
            item["debug"] = {"random": True}
        items.append(item)
    return items, int(total_row["total"] if total_row else 0)


def dashboard_shelf_response(
    store: Store,
    key: str,
    *,
    limit: int,
    offset: int,
    include_debug: bool = False,
) -> dict[str, object] | None:
    titles = {
        "recently_added": ("Recently Added", "New in your collection"),
        "listen_again": ("Listen Again", "Tracks with positive listening history"),
        "long_time_no_listen": ("Long Time No Listen", "Good tracks that fell out of rotation"),
        "mixes_for_you": ("Mixes For You", "Generated finite mixes from taste regions"),
        "discover_random": ("Discover Random", "Tracks you haven't played yet"),
    }
    if key not in titles:
        return None
    if key == "recently_added":
        items, total = _dashboard_recently_added(store, limit, offset, include_debug)
    elif key == "listen_again":
        items, total = _dashboard_listen_again(store, limit, offset, include_debug)
    elif key == "long_time_no_listen":
        items, total = _dashboard_long_time_no_listen(store, limit, offset, include_debug)
    elif key == "discover_random":
        items, total = _dashboard_discover_random(store, limit, offset, include_debug)
    else:
        items, total = _dashboard_generated_mixes(store, limit, offset, include_debug)
    title, subtitle = titles[key]
    next_offset = offset + limit if offset + limit < total else None
    return {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "available": True,
    }


# ---------------------------------------------------------------------------
# Mix refresh
# ---------------------------------------------------------------------------

def _generated_mix_settings(settings: object) -> dict[str, object]:
    """Local helper — avoids importing generated_mix_settings from main to prevent circular import."""
    from app.config import load_runtime_settings  # noqa: PLC0415
    runtime = load_runtime_settings(settings.data_dir)  # type: ignore[attr-defined]
    from app.mixes import generated_mix_default_settings  # noqa: PLC0415
    saved = runtime.get("generated_mixes", runtime.get("mixes", {}))
    saved = saved if isinstance(saved, dict) else {}
    return {**generated_mix_default_settings(), **saved}


def ensure_dashboard_mixes_fast(store: Store, settings: object) -> dict[str, object]:
    mix_settings = _generated_mix_settings(settings)
    model = str(mix_settings.get("mix_model") or "discogs_multi")
    embedding_count = store.count_embeddings(model)
    plan = dashboard_mix_generation_plan(store, mix_settings)
    if not plan.should_generate:
        diagnostics = dict(plan.diagnostics)
        diagnostics.update({"background": False, "embedding_count": embedding_count})
        return diagnostics
    if embedding_count <= 5000:
        return ensure_dashboard_mixes(store, settings, mix_settings).diagnostics
    started = _start_dashboard_mix_generation(settings.db_path, settings)  # type: ignore[attr-defined]
    diagnostics = dict(plan.diagnostics)
    diagnostics.update({
        "reason": "scheduled" if started else "already_running",
        "generated_count": 0,
        "background": True,
        "embedding_count": embedding_count,
    })
    return diagnostics


def _start_dashboard_mix_generation(db_path: Path, settings: object) -> bool:
    if not MIX_GENERATION_LOCK.acquire(blocking=False):
        return False

    def run() -> None:
        try:
            logger.info("Background generated mix refresh started db_path=%s", db_path)
            background_store = Store(db_path)
            background_store.init()
            result = ensure_dashboard_mixes(background_store, settings, _generated_mix_settings(settings))
            logger.info(
                "Background generated mix refresh finished generated=%s reason=%s",
                result.diagnostics.get("generated_count"),
                result.diagnostics.get("reason"),
            )
        except Exception:
            logger.exception("Background generated mix refresh failed")
        finally:
            MIX_GENERATION_LOCK.release()

    Thread(target=run, name="generated-mix-refresh", daemon=True).start()
    return True
