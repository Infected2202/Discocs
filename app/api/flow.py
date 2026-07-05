"""Flow session API: POST /api/v1/flow/start, POST /api/v1/flow/refill."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import context
from app.schemas.requests import FlowStartRequest, FlowRefillRequest, FlowEventRequest
from app.serializers.playback import playback_session_dict, queue_item_dict

router = APIRouter()
logger = logging.getLogger(__name__)

# Default window for cross-session recently-played exclusion (days). Tracks
# heard within this window are kept out of fresh Flow pools so restarts stop
# replaying the same already-heard tracks.
_DEFAULT_EXCLUDE_PLAYED_DAYS = 7

# If the profile was last built more than this many minutes ago, /flow/start
# rebuilds it synchronously so fresh play signals (play_count/likes synced from
# Navidrome) are reflected. 0 disables on-start rebuilds.
_DEFAULT_REBUILD_MAX_AGE_MINUTES = 30


def _profile_age_minutes(profile) -> float | None:
    """Minutes since the profile was last built, or None if never built."""
    stamp = getattr(profile, "last_built_at", None)
    if not stamp:
        return None
    from datetime import datetime, timezone

    try:
        built = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if built.tzinfo is None:
        built = built.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - built).total_seconds() / 60.0


def _maybe_rebuild_stale_profile(store, app_settings, profile, request_settings):
    """Rebuild the profile in place if it is older than the configured window.

    Returns the (possibly refreshed) profile. Only refreshes an already-built
    profile — a never-built one still goes through the not-ready path.
    """
    if profile is None or profile.status not in ("ready", "cold_start"):
        return profile
    max_age = int(request_settings.get("rebuild_max_age_minutes", _DEFAULT_REBUILD_MAX_AGE_MINUTES))
    if max_age <= 0:
        return profile
    age = _profile_age_minutes(profile)
    if age is not None and age < max_age:
        return profile

    from app.services.flow_regions import FlowSettings, rebuild_flow_profile

    try:
        rebuild_flow_profile(store, app_settings, FlowSettings(model_key=profile.model_key))
    except Exception:
        logger.exception("On-start flow profile rebuild failed model=%s", profile.model_key)
        return profile
    return store.get_flow_profile(profile.model_key) or profile


# ---------------------------------------------------------------------------
# GET /api/v1/flow/profile  (Slice 6)
# ---------------------------------------------------------------------------

@router.get("/api/v1/flow/profile")
def api_v1_flow_profile(
    model_key: str = "discogs_multi",
    include_debug: bool = False,
) -> dict[str, object]:
    """Return current Flow profile status and region summary."""
    store, _settings = context()
    profile = store.get_flow_profile(model_key)
    if profile is None:
        return {
            "available": False,
            "status": "not_built",
            "model_key": model_key,
            "region_count": 0,
            "regions": [],
        }
    regions = store.list_flow_regions(profile.id)
    region_summaries = [_region_summary(r) for r in regions]
    resp: dict[str, object] = {
        "available": profile.status in ("ready", "cold_start"),
        "status": profile.status,
        "model_key": model_key,
        "profile_id": profile.id,
        "region_count": len(regions),
        "last_built_at": profile.last_built_at,
        "regions": region_summaries if include_debug else [],
    }
    if include_debug and regions:
        total_weight = sum(r.weight for r in regions)
        resp["quality"] = {
            "total_seed_count": sum(r.seed_count for r in regions),
            "total_candidate_count": sum(r.candidate_count for r in regions),
            "avg_candidates_per_region": round(
                sum(r.candidate_count for r in regions) / len(regions), 1
            ),
            "region_weights": [
                round(r.weight / total_weight, 3) if total_weight > 0 else 0
                for r in regions
            ],
        }
    return resp


# ---------------------------------------------------------------------------
# Region selection helpers
# ---------------------------------------------------------------------------

def _choose_region(store, profile_id: str, hint_region_id: str | None = None):
    """Pick the active region: explicit hint → highest-weight region."""
    if hint_region_id:
        region = store.get_flow_region(hint_region_id)
        if region and region.profile_id == profile_id:
            return region
    regions = store.list_flow_regions(profile_id)
    if not regions:
        return None
    return max(regions, key=lambda r: r.weight)


def _load_session_context(store, session_id: str, region_id: str, model_key: str):
    """Build FlowSessionContext from existing playback session state."""
    from app.services.flow_candidates import FlowSessionContext, load_embeddings_batch

    session = store.get_playback_session(session_id)
    if session is None:
        return None

    ctx = FlowSessionContext(
        session_id=session_id,
        region_id=region_id,
        model_key=model_key,
    )

    # Restore state from session state_json
    state: dict[str, Any] = {}
    if session.state_json:
        try:
            state = json.loads(session.state_json)
        except Exception:
            pass

    ctx.session_skipped = {int(k): v for k, v in (state.get("session_skipped") or {}).items()}
    ctx.session_accepted = {int(k): v for k, v in (state.get("session_accepted") or {}).items()}
    ctx.session_artist_plays = {int(k): v for k, v in (state.get("session_artist_plays") or {}).items()}
    ctx.session_release_plays = {int(k): v for k, v in (state.get("session_release_plays") or {}).items()}
    ctx.exploration_level = float(state.get("exploration_level") or 0.10)
    ctx.exclude_played_days = int(state.get("exclude_played_days") or 0)
    ctx.current_track_id = session.current_track_id

    # Tracks to keep out of the candidate pool: anything still on the queue
    # (played, skipped, playing, or queued-but-not-yet-played). A track sitting
    # further down as "queued" must be excluded too, otherwise a later refill
    # call can pick it again and append a duplicate before it's ever played.
    queue = store.list_queue_items(session_id)
    ctx.played_track_ids = {
        item.track_id for item in queue
        if item.status != "removed"
    }
    if session.current_track_id:
        ctx.played_track_ids.add(session.current_track_id)

    # Load embeddings for recently accepted tracks so scorer can compute continuity_term
    # Sort by acceptance count descending, take up to 10 most accepted
    recent_accepted_ids = sorted(
        ctx.session_accepted.keys(),
        key=lambda tid: ctx.session_accepted[tid],
        reverse=True,
    )[:10]
    if recent_accepted_ids:
        vecs = load_embeddings_batch(store, recent_accepted_ids, model_key)
        ctx.recent_accepted_vectors = [vecs[tid] for tid in recent_accepted_ids if tid in vecs]

    # Load embeddings for skipped tracks → session negative centroid. Most-skipped
    # first, capped, so a long session does not unbound the query.
    recent_skipped_ids = sorted(
        ctx.session_skipped.keys(),
        key=lambda tid: ctx.session_skipped[tid],
        reverse=True,
    )[:15]
    if recent_skipped_ids:
        skip_vecs = load_embeddings_batch(store, recent_skipped_ids, model_key)
        ctx.recent_skipped_vectors = [
            skip_vecs[tid] for tid in recent_skipped_ids if tid in skip_vecs
        ]

    return ctx


def _queue_items_response(store, items, include_debug: bool = False) -> list[dict]:
    return [queue_item_dict(store, item, include_debug=include_debug) for item in items]


def _region_summary(region) -> dict[str, object]:
    return {
        "id": region.id,
        "region_index": region.region_index,
        "weight": region.weight,
        "seed_count": region.seed_count,
        "candidate_count": region.candidate_count,
        "medoid_track_id": region.medoid_track_id,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/flow/start
# ---------------------------------------------------------------------------

@router.post("/api/v1/flow/start")
def api_v1_flow_start(request: FlowStartRequest) -> dict[str, object]:
    """Start a new Flow session.

    1. Load the profile for the requested model_key.
    2. Choose active region (by weight or hint).
    3. Build candidate pool → score → fill visible_buffer.
    4. Create playback session with initial queue.
    5. Return session + queue + flow metadata.
    """
    from app.services.flow_candidates import (
        FlowSessionContext,
        adaptive_exploration_level,
        fill_flow_queue,
    )
    from app.services.flow_regions import FlowSettings

    store, app_settings = context()

    model_key = (request.settings or {}).get("model_key", "discogs_multi")
    if isinstance(model_key, str) is False:
        model_key = "discogs_multi"

    profile = store.get_flow_profile(str(model_key))
    profile = _maybe_rebuild_stale_profile(store, app_settings, profile, request.settings or {})
    if profile is None or profile.status not in ("ready", "cold_start"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Flow profile not ready (status={profile.status if profile else 'not_built'}). "
                "Run POST /api/v1/jobs/flow-profile first."
            ),
        )

    hint_region_id = (request.settings or {}).get("region_id")
    region = _choose_region(store, profile.id, hint_region_id)
    if region is None:
        raise HTTPException(status_code=409, detail="Flow profile has no regions.")

    visible_buffer = int((request.settings or {}).get("visible_buffer", 5))
    pool_size = int((request.settings or {}).get("candidate_pool_size", 1000))

    # Explicit caller value wins; otherwise adapt to the region's signal volume
    # (few seeds → explore more, many seeds → exploit).
    explicit_explore = (request.settings or {}).get("exploration_ratio")
    exploration_level = (
        float(explicit_explore) if explicit_explore is not None
        else adaptive_exploration_level(region.seed_count)
    )

    exclude_played_days = int(
        (request.settings or {}).get("exclude_played_days", _DEFAULT_EXCLUDE_PLAYED_DAYS)
    )

    ctx = FlowSessionContext(
        session_id="__placeholder__",
        region_id=region.id,
        model_key=str(model_key),
        exploration_level=exploration_level,
        exclude_played_days=exclude_played_days,
    )

    selected, score_summary = fill_flow_queue(
        store, app_settings, region, ctx,
        n=visible_buffer,
        pool_size=max(pool_size, visible_buffer * 10),
        max_per_artist=int((request.settings or {}).get("max_per_artist", 2)),
        max_per_release=int((request.settings or {}).get("max_per_release", 1)),
        long_term_weight=float((request.settings or {}).get("long_term_weight", 0.70)),
        session_weight=float((request.settings or {}).get("session_weight", 0.30)),
        skip_penalty_strength=float((request.settings or {}).get("skip_penalty_strength", 0.50)),
    )

    if not selected:
        raise HTTPException(
            status_code=409,
            detail="No candidates available for Flow. Ensure embeddings are built.",
        )

    track_ids = [c.track_id for c in selected]

    flow_state = {
        "profile_id": profile.id,
        "active_region_id": region.id,
        "model_key": str(model_key),
        "session_skipped": {},
        "session_accepted": {},
        "session_artist_plays": {},
        "session_release_plays": {},
        "exploration_level": ctx.exploration_level,
        "exclude_played_days": ctx.exclude_played_days,
    }

    session, queue = store.create_playback_session(
        source_type="flow",
        source_label=f"Flow · Region {region.region_index}",
        mode="flow",
        autoplay_enabled=True,
        track_ids=track_ids,
        state=flow_state,
    )

    # Save generation run for diagnostics
    run = store.save_flow_generation_run(
        session_id=session.id,
        profile_id=profile.id,
        region_id=region.id,
        settings_json=json.dumps(request.settings or {}),
        candidate_count=score_summary.get("pool_size"),
        selected_count=len(selected),
        score_summary_json=json.dumps(score_summary),
    )

    response: dict[str, object] = {
        "session": playback_session_dict(store, session),
        "queue": {
            "items": _queue_items_response(store, queue, include_debug=request.include_debug),
            "visible_buffer": visible_buffer,
        },
        "flow": {
            "profile_id": profile.id,
            "active_region_id": region.id,
            "generation_run_id": run.id,
            "active_region": _region_summary(region),
        },
    }
    if request.include_debug:
        response["debug"] = {
            "score_summary": score_summary,
            "score_breakdowns": [
                {"track_id": c.track_id, **c.score_breakdown}
                for c in selected
            ],
        }
    return response


# ---------------------------------------------------------------------------
# POST /api/v1/flow/refill
# ---------------------------------------------------------------------------

@router.post("/api/v1/flow/refill")
def api_v1_flow_refill(request: FlowRefillRequest) -> dict[str, object]:
    """Refill / rerank the Flow queue.

    Called when the visible buffer is low or after meaningful playback events.
    Reads session state_json for accumulated skip/accept signals, then appends
    new items to the queue.
    """
    from app.services.flow_candidates import fill_flow_queue

    store, app_settings = context()

    session = store.get_playback_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Playback session not found.")
    if session.source_type != "flow":
        raise HTTPException(status_code=400, detail="Session is not a Flow session.")
    if session.status == "ended":
        raise HTTPException(status_code=409, detail="Session has ended.")

    state: dict[str, Any] = {}
    if session.state_json:
        try:
            state = json.loads(session.state_json)
        except Exception:
            pass

    model_key = str(state.get("model_key") or "discogs_multi")
    profile_id = str(state.get("profile_id") or "")
    region_id = str(state.get("active_region_id") or "")

    # Allow caller to switch region
    if request.region_id:
        region_id = request.region_id

    region = store.get_flow_region(region_id) if region_id else None
    if region is None:
        # Fallback: pick highest-weight region
        profile = store.get_flow_profile(model_key)
        if profile is None:
            raise HTTPException(status_code=409, detail="Flow profile not found.")
        region = _choose_region(store, profile.id)
        if region is None:
            raise HTTPException(status_code=409, detail="No regions in flow profile.")

    ctx = _load_session_context(store, request.session_id, region.id, model_key)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Session not found.")

    visible_buffer = request.visible_buffer or 5

    # Count how many tracks are currently queued (not yet played)
    queue = store.list_queue_items(request.session_id)
    pending = [i for i in queue if i.status == "queued"]
    need = max(0, visible_buffer - len(pending))

    if need == 0:
        return {
            "added_items": [],
            "active_region": _region_summary(region),
            "generation_run_id": None,
        }

    selected, score_summary = fill_flow_queue(
        store, app_settings, region, ctx,
        n=need,
        pool_size=max(need * 20, 50),
    )

    # Re-check right before writing: narrows the window where a concurrent
    # refill call (same session) already queued one of these track IDs after
    # ctx was built above.
    already_queued = {i.track_id for i in store.list_queue_items(request.session_id) if i.status != "removed"}
    selected = [c for c in selected if c.track_id not in already_queued]

    added: list[dict] = []
    if selected:
        new_items = [
            {
                "track_id": c.track_id,
                "origin": "flow",
                "source_type": "flow",
                "score": c.final_score,
                "debug_json": json.dumps(c.score_breakdown) if request.include_debug else None,
            }
            for c in selected
        ]
        queue_after = store.append_queue_items(request.session_id, new_items)
        added_ids = {c.track_id for c in selected}
        added_queue_items = [i for i in queue_after if i.track_id in added_ids]
        added = _queue_items_response(store, added_queue_items, include_debug=request.include_debug)

    run = store.save_flow_generation_run(
        session_id=request.session_id,
        profile_id=profile_id or None,
        region_id=region.id,
        candidate_count=score_summary.get("pool_size"),
        selected_count=len(selected),
        score_summary_json=json.dumps(score_summary),
    )

    # Update region_id in session state if it changed
    if request.region_id and request.region_id != state.get("active_region_id"):
        state["active_region_id"] = region.id
        store.update_playback_session(request.session_id, state=state)

    return {
        "added_items": added,
        "active_region": _region_summary(region),
        "generation_run_id": run.id,
    }


# ---------------------------------------------------------------------------
# POST /api/v1/flow/event  (Slice 5: feedback loop)
# ---------------------------------------------------------------------------

@router.post("/api/v1/flow/event")
def api_v1_flow_event(request: FlowEventRequest) -> dict[str, object]:
    """Apply a playback event to Flow session state.

    Updates short-term skip/accept signals and may trigger a region switch.
    Does NOT refill the queue — call /refill separately when buffer is low.
    """
    from app.services.flow_feedback import apply_flow_event

    store, _settings = context()

    session = store.get_playback_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Playback session not found.")
    if session.source_type != "flow":
        raise HTTPException(status_code=400, detail="Session is not a Flow session.")

    result = apply_flow_event(
        store,
        request.session_id,
        request.event_type,
        request.track_id,
        artist_id=request.artist_id,
        release_id=request.release_id,
    )
    return {"ok": True, "changes": result}
