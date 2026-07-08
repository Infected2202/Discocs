from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app.api.flow as flow
from app.schemas.requests import FlowRefillRequest, FlowStartRequest


@dataclass
class _Profile:
    id: str = "profile-1"
    model_key: str = "discogs_multi"
    status: str = "ready"
    last_built_at: str | None = "2026-07-08T12:00:00+00:00"


@dataclass
class _Region:
    id: str = "region-1"
    profile_id: str = "profile-1"
    region_index: int = 2
    weight: float = 0.7
    seed_count: int = 4
    candidate_count: int = 15
    medoid_track_id: int | None = 11


@dataclass
class _Candidate:
    track_id: int
    final_score: float = 0.8
    score_breakdown: dict[str, float] = field(default_factory=lambda: {"taste": 0.8})


@dataclass
class _Session:
    id: str = "session-1"
    source_type: str = "flow"
    status: str = "active"
    current_track_id: int | None = None
    state_json: str | None = None


@dataclass
class _QueueItem:
    track_id: int
    status: str = "queued"


def _store_for_flow_start() -> MagicMock:
    profile = _Profile()
    region = _Region()
    session = _Session()
    queue = [MagicMock(track_id=101), MagicMock(track_id=202)]
    store = MagicMock()
    store.get_flow_profile.return_value = profile
    store.get_flow_region.return_value = region
    store.list_flow_regions.return_value = [region]
    store.create_playback_session.return_value = (session, queue)
    store.save_flow_generation_run.return_value = MagicMock(id="run-1")
    return store


def test_flow_model_key_uses_default_for_non_string():
    assert flow._flow_model_key({"model_key": 42}) == "discogs_multi"


def test_api_v1_flow_start_builds_session_from_settings():
    store = _store_for_flow_start()
    request = FlowStartRequest(
        settings={
            "model_key": "discogs_multi",
            "visible_buffer": 2,
            "candidate_pool_size": 25,
            "region_id": "region-1",
            "exploration_ratio": 0.33,
            "exclude_played_days": 9,
            "max_per_artist": 3,
            "max_per_release": 2,
            "long_term_weight": 0.6,
            "session_weight": 0.4,
            "skip_penalty_strength": 0.2,
        },
        include_debug=True,
    )
    selected = [_Candidate(101), _Candidate(202)]

    with patch("app.api.flow.context", return_value=(store, MagicMock())), patch(
        "app.api.flow.playback_session_dict",
        return_value={"id": "session-1"},
    ), patch(
        "app.api.flow.queue_item_dict",
        side_effect=lambda *_args, **_kwargs: {"track_id": _args[1].track_id},
    ), patch(
        "app.services.flow_candidates.fill_flow_queue",
        return_value=(selected, {"pool_size": 25}),
    ):
        response = flow.api_v1_flow_start(request)

    create_kwargs = store.create_playback_session.call_args.kwargs
    assert create_kwargs["track_ids"] == [101, 202]
    assert create_kwargs["state"]["profile_id"] == "profile-1"
    assert create_kwargs["state"]["active_region_id"] == "region-1"
    assert create_kwargs["state"]["exploration_level"] == 0.33
    assert create_kwargs["state"]["exclude_played_days"] == 9
    assert response["queue"]["visible_buffer"] == 2
    assert response["flow"]["generation_run_id"] == "run-1"
    assert response["debug"]["score_summary"] == {"pool_size": 25}
    assert response["debug"]["score_breakdowns"] == [
        {"track_id": 101, "taste": 0.8},
        {"track_id": 202, "taste": 0.8},
    ]


def test_api_v1_flow_start_raises_when_profile_is_not_ready():
    store = MagicMock()
    store.get_flow_profile.return_value = None

    with patch("app.api.flow.context", return_value=(store, MagicMock())):
        with pytest.raises(HTTPException) as exc:
            flow.api_v1_flow_start(FlowStartRequest())

    assert exc.value.status_code == 409
    assert "Flow profile not ready" in exc.value.detail


def test_api_v1_flow_refill_falls_back_to_profile_region():
    store = MagicMock()
    profile = _Profile()
    region = _Region()
    session = _Session(
        state_json=json.dumps({"model_key": "discogs_multi", "profile_id": "profile-1"})
    )
    store.get_playback_session.return_value = session
    store.get_flow_region.return_value = None
    store.get_flow_profile.return_value = profile
    store.list_flow_regions.return_value = [region]
    store.list_queue_items.return_value = [_QueueItem(track_id=1, status="played")]
    store.save_flow_generation_run.return_value = MagicMock(id="run-2")
    store.append_queue_items.return_value = [MagicMock(track_id=55)]

    with patch("app.api.flow.context", return_value=(store, MagicMock())), patch(
        "app.api.flow._load_session_context",
        return_value=MagicMock(),
    ), patch(
        "app.api.flow.queue_item_dict",
        side_effect=lambda *_args, **_kwargs: {"track_id": _args[1].track_id},
    ), patch(
        "app.services.flow_candidates.fill_flow_queue",
        return_value=([_Candidate(55)], {"pool_size": 10}),
    ):
        response = flow.api_v1_flow_refill(FlowRefillRequest(session_id="session-1", visible_buffer=3))

    assert response["active_region"]["id"] == "region-1"
    assert response["generation_run_id"] == "run-2"
    store.append_queue_items.assert_called_once()


def test_api_v1_flow_refill_updates_session_state_on_region_switch():
    store = MagicMock()
    region = _Region(id="region-2")
    state = {
        "model_key": "discogs_multi",
        "profile_id": "profile-1",
        "active_region_id": "region-1",
    }
    session = _Session(state_json=json.dumps(state))
    store.get_playback_session.return_value = session
    store.get_flow_region.return_value = region
    store.list_queue_items.side_effect = [
        [_QueueItem(track_id=1, status="played")],
        [_QueueItem(track_id=1, status="played")],
    ]
    store.save_flow_generation_run.return_value = MagicMock(id="run-3")
    store.append_queue_items.return_value = [MagicMock(track_id=77)]

    with patch("app.api.flow.context", return_value=(store, MagicMock())), patch(
        "app.api.flow._load_session_context",
        return_value=MagicMock(),
    ), patch(
        "app.api.flow.queue_item_dict",
        side_effect=lambda *_args, **_kwargs: {"track_id": _args[1].track_id},
    ), patch(
        "app.services.flow_candidates.fill_flow_queue",
        return_value=([_Candidate(77)], {"pool_size": 10}),
    ):
        flow.api_v1_flow_refill(
            FlowRefillRequest(session_id="session-1", visible_buffer=3, region_id="region-2")
        )

    store.update_playback_session.assert_called_once()
    assert store.update_playback_session.call_args.kwargs["state"]["active_region_id"] == "region-2"


def test_flow_session_or_error_rejects_non_flow_and_ended_sessions():
    with pytest.raises(HTTPException) as wrong_source:
        flow._flow_session_or_error(MagicMock(get_playback_session=MagicMock(return_value=_Session(source_type="manual"))), "session-1")
    assert wrong_source.value.status_code == 400

    with pytest.raises(HTTPException) as ended:
        flow._flow_session_or_error(MagicMock(get_playback_session=MagicMock(return_value=_Session(status="ended"))), "session-1")
    assert ended.value.status_code == 409
