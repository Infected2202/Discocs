"""Regression tests: Flow refill must not re-queue a track that is already
sitting in the queue (played, skipped, or still queued-but-unplayed).

Root cause found in production: `_load_session_context` only excluded
`played`/`skipped` tracks from the candidate pool, so a track still sitting
further down the queue as `queued` (added by an earlier refill, not yet
played) could be picked again by the next refill and appended a second time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import app.api.flow as flow
from app.schemas.requests import FlowRefillRequest


@dataclass
class _Session:
    id: str = "s1"
    source_type: str = "flow"
    status: str = "active"
    current_track_id: int | None = None
    state_json: str | None = None


@dataclass
class _QueueItem:
    track_id: int
    status: str


@dataclass
class _Region:
    id: str = "r1"
    profile_id: str = "p1"
    region_index: int = 0
    weight: float = 1.0
    seed_count: int = 3
    candidate_count: int = 10
    medoid_track_id: int | None = None


@dataclass
class _ScoredCandidate:
    track_id: int
    final_score: float = 0.9
    score_breakdown: dict = field(default_factory=dict)


def _make_store(queue_items: list[_QueueItem]) -> MagicMock:
    store = MagicMock()
    store.get_playback_session.return_value = _Session(
        state_json=json.dumps(
            {"model_key": "discogs_multi", "profile_id": "p1", "active_region_id": "r1"}
        )
    )
    store.get_flow_region.return_value = _Region()
    store.list_queue_items.return_value = queue_items
    store.get_track.return_value = None  # skip track serialization, irrelevant here
    store.save_flow_generation_run.return_value = MagicMock(id="run1")
    store.append_queue_items.side_effect = lambda session_id, items: [
        MagicMock(track_id=item["track_id"]) for item in items
    ]
    return store


# ---------------------------------------------------------------------------
# _load_session_context — exclusion set must include queued-but-unplayed
# ---------------------------------------------------------------------------

def test_load_session_context_excludes_queued_not_just_played():
    items = [
        _QueueItem(track_id=1, status="played"),
        _QueueItem(track_id=2, status="skipped"),
        _QueueItem(track_id=3, status="queued"),
        _QueueItem(track_id=4, status="removed"),
    ]
    store = MagicMock()
    store.get_playback_session.return_value = _Session(state_json=json.dumps({}))
    store.list_queue_items.return_value = items

    ctx = flow._load_session_context(store, "s1", "r1", "discogs_multi")

    # queued (3) must be excluded alongside played/skipped (1, 2); removed (4)
    # is no longer on the queue at all, so it stays out of the exclusion set.
    assert ctx.played_track_ids == {1, 2, 3}


# ---------------------------------------------------------------------------
# api_v1_flow_refill — final re-check right before writing to the queue
# ---------------------------------------------------------------------------

def test_refill_skips_candidate_already_queued():
    existing = [_QueueItem(track_id=42, status="queued")]
    store = _make_store(existing)
    candidate = _ScoredCandidate(track_id=42)

    with patch("app.api.flow.context", return_value=(store, MagicMock())), patch(
        "app.services.flow_candidates.fill_flow_queue",
        return_value=([candidate], {"pool_size": 1}),
    ):
        request = FlowRefillRequest(session_id="s1", visible_buffer=5)
        result = flow.api_v1_flow_refill(request)

    assert result["added_items"] == []
    store.append_queue_items.assert_not_called()


def test_refill_adds_candidate_not_yet_queued():
    existing = [_QueueItem(track_id=42, status="queued")]
    store = _make_store(existing)
    candidate = _ScoredCandidate(track_id=99)

    with patch("app.api.flow.context", return_value=(store, MagicMock())), patch(
        "app.services.flow_candidates.fill_flow_queue",
        return_value=([candidate], {"pool_size": 1}),
    ):
        request = FlowRefillRequest(session_id="s1", visible_buffer=5)
        result = flow.api_v1_flow_refill(request)

    assert len(result["added_items"]) == 1
    store.append_queue_items.assert_called_once()
