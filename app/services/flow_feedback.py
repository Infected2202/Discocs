"""Flow feedback loop and region-switching logic (Phase 9, Slice 5).

Called after playback events on a Flow session to update the session's
state_json with short-term signals. Does NOT re-score or refill — that
happens lazily in the next /api/v1/flow/refill call.

Signal mapping:
  completed / play_threshold_reached → accepted (weak positive)
  liked / replayed                   → accepted (strong positive)
  skipped (early)                    → skip penalty
  disliked                           → strong negative (handled by track pref; session ban)
  queue_click                        → ignored (navigation only)
"""
from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.store import Store

logger = logging.getLogger(__name__)

# Event → category
_STRONG_POSITIVE = {"liked", "replayed"}
_WEAK_POSITIVE = {"completed", "play_threshold_reached"}
_SKIP_EVENTS = {"skipped"}
_NEGATIVE_EVENTS = {"disliked"}
_IGNORED_EVENTS = {"queue_click", "removed_from_queue", "saved_to_playlist",
                   "autoplay_toggled", "preference_changed", "track_started", "progress"}

# Tuning constants
_EXPLORATION_MAX = 0.60
_EXPLORATION_STEP_UP = 0.05     # per accepted run milestone
_EXPLORATION_STEP_DOWN = 0.03   # per skip
_EXPLORATION_ACCEPTED_WINDOW = 5  # consecutive accepted before exploration nudge
_SKIP_SWITCH_THRESHOLD = 2       # early skips in last _SKIP_SWITCH_WINDOW to trigger region switch
_SKIP_SWITCH_WINDOW = 5
_REGION_WEIGHT_DECAY = 0.85      # decay active-region weight on switch


def _load_state(session) -> dict[str, Any]:
    if session.state_json:
        try:
            return json.loads(session.state_json)
        except Exception:
            pass
    return {}


def _save_state(store: Store, session_id: str, state: dict[str, Any]) -> None:
    store.update_playback_session(session_id, state=state)


def _detect_region_switch(state: dict[str, Any]) -> bool:
    """Return True if recent skips in the window exceed threshold."""
    recent: list[str] = state.get("recent_event_types", [])
    window = recent[-_SKIP_SWITCH_WINDOW:]
    early_skips = sum(1 for e in window if e == "skipped")
    return early_skips >= _SKIP_SWITCH_THRESHOLD


def _choose_next_region(store: Store, profile_id: str, current_region_id: str) -> str | None:
    """Pick the highest-weight region that is not the current one."""
    regions = store.list_flow_regions(profile_id)
    others = [r for r in regions if r.id != current_region_id]
    if not others:
        return None
    best = max(others, key=lambda r: r.weight)
    return best.id


def apply_flow_event(
    store: Store,
    session_id: str,
    event_type: str,
    track_id: int | None,
    artist_id: int | None = None,
    release_id: int | None = None,
) -> dict[str, Any]:
    """Update Flow session state after a playback event.

    Returns a dict describing what changed (for debug / API response).
    """
    session = store.get_playback_session(session_id)
    if session is None or session.source_type != "flow":
        return {"skipped": True, "reason": "not_a_flow_session"}

    state = _load_state(session)
    profile_id: str = str(state.get("profile_id") or "")
    active_region_id: str = str(state.get("active_region_id") or "")
    exploration: float = float(state.get("exploration_level") or 0.10)

    session_skipped: dict[str, int] = state.get("session_skipped") or {}
    session_accepted: dict[str, int] = state.get("session_accepted") or {}
    session_artist_plays: dict[str, int] = state.get("session_artist_plays") or {}
    session_release_plays: dict[str, int] = state.get("session_release_plays") or {}
    recent_events: list[str] = state.get("recent_event_types") or []
    consecutive_accepted: int = int(state.get("consecutive_accepted") or 0)

    changed: dict[str, Any] = {"event_type": event_type}

    if event_type in _IGNORED_EVENTS:
        return {"skipped": True, "reason": "ignored_event"}

    tid_key = str(track_id) if track_id is not None else None
    aid_key = str(artist_id) if artist_id is not None else None
    rid_key = str(release_id) if release_id is not None else None

    # Update recent event window
    recent_events.append(event_type)
    if len(recent_events) > 20:
        recent_events = recent_events[-20:]

    if event_type in _STRONG_POSITIVE:
        if tid_key:
            session_accepted[tid_key] = session_accepted.get(tid_key, 0) + 2
        consecutive_accepted += 1
        # Long accepted run → boost exploration
        if consecutive_accepted >= _EXPLORATION_ACCEPTED_WINDOW:
            exploration = min(_EXPLORATION_MAX, exploration + _EXPLORATION_STEP_UP)
            consecutive_accepted = 0
            changed["exploration_boosted"] = True
        changed["accepted"] = True

    elif event_type in _WEAK_POSITIVE:
        if tid_key:
            session_accepted[tid_key] = session_accepted.get(tid_key, 0) + 1
        consecutive_accepted += 1
        changed["accepted"] = True

    elif event_type in _SKIP_EVENTS:
        if tid_key:
            session_skipped[tid_key] = session_skipped.get(tid_key, 0) + 1
        consecutive_accepted = 0
        exploration = max(0.0, exploration - _EXPLORATION_STEP_DOWN)

        # Region switch check
        if _detect_region_switch({"recent_event_types": recent_events}):
            next_region_id = _choose_next_region(store, profile_id, active_region_id)
            if next_region_id:
                # Decay weight of the current region
                if active_region_id:
                    region = store.get_flow_region(active_region_id)
                    if region:
                        store.update_flow_region_weight(
                            active_region_id,
                            region.weight * _REGION_WEIGHT_DECAY,
                        )
                active_region_id = next_region_id
                recent_events = []   # reset window after switch
                consecutive_accepted = 0
                changed["region_switched"] = next_region_id

    elif event_type in _NEGATIVE_EVENTS:
        if tid_key:
            # Session ban: add many skip counts so scorer avoids this track
            session_skipped[tid_key] = session_skipped.get(tid_key, 0) + 5
        consecutive_accepted = 0
        changed["banned_in_session"] = True

    # Update artist/release play counts for session diversity
    if event_type not in _IGNORED_EVENTS and event_type not in _NEGATIVE_EVENTS:
        if aid_key:
            session_artist_plays[aid_key] = session_artist_plays.get(aid_key, 0) + 1
        if rid_key:
            session_release_plays[rid_key] = session_release_plays.get(rid_key, 0) + 1

    # Write updated state back
    state.update({
        "active_region_id": active_region_id,
        "exploration_level": round(exploration, 4),
        "session_skipped": session_skipped,
        "session_accepted": session_accepted,
        "session_artist_plays": session_artist_plays,
        "session_release_plays": session_release_plays,
        "recent_event_types": recent_events,
        "consecutive_accepted": consecutive_accepted,
    })
    _save_state(store, session_id, state)

    changed["active_region_id"] = active_region_id
    changed["exploration_level"] = exploration
    if "region_switched" not in changed:
        changed["region_switched"] = False
    return changed
