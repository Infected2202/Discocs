"""Profile: the user's personal recommendations — generated mixes and "Albums For You" —
are visible to every signed-in user (read-only, whitelisted fields)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import app
from app.models import utc_now
from app.scanner import ScannedTrack
from app.serializers.search import dashboard_shelf_item
from app.services.profile import (
    ProfileMixNotFoundError,
    ProfileNotFoundError,
    ProfileViewerRequiredError,
    profile_albums_for_you_payload,
    profile_mix_detail,
    profile_mixes_payload,
)
from app.store import INITIALIZED_DB_PATHS, Store

MODEL = "test-model"


def _stores(tmp_path: Path) -> tuple[Store, Store, Store]:
    root = Store(tmp_path / "app.db")
    root.init()
    alice = root.for_user(root.upsert_user("Alice", now=utc_now()))
    bob = root.for_user(root.upsert_user("bob", now=utc_now()))
    return root, alice, bob


def _track(store: Store, tmp_path: Path, name: str) -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=(tmp_path / f"{name}.flac").resolve(),
            artist="Solo",
            title=name,
            album=f"{name} album",
            duration=200.0,
            file_size=1,
            mtime=1,
        )
    )
    return track_id


def _mix(store: Store, mix_id: str, track_ids: list[int], *, status: str = "active", cover_path: str | None = None):
    return store.save_generated_mix(
        mix_id=mix_id,
        title=f"Title {mix_id}",
        mix_type="debug",
        status=status,
        anchor={"representative_artist": "Anchor Artist", "seed_track_ids": track_ids},
        settings={"tracks_per_mix": len(track_ids), "novelty_weight": 0.7},
        score_summary={"selected_count": len(track_ids)},
        items=[
            {
                "track_id": track_id,
                "score": 0.91,
                "score_breakdown": {"region_similarity": 0.91},
                "reason": {"anchor_track_id": track_ids[0]},
            }
            for track_id in track_ids
        ],
        cover_path=cover_path,
    )


def _albums(store: Store, release_ids: list[int], *, reason: str | None = "You liked 5 tracks") -> None:
    items = [
        dashboard_shelf_item(
            "release", release_id, f"Album {release_id}", "Artist", f"/releases/{release_id}", reason=reason
        )
        for release_id in release_ids
    ]
    store.set_albums_for_you_cache(MODEL, json.dumps(items))


# ---------------------------------------------------------------------------
# Mixes shelf
# ---------------------------------------------------------------------------

def test_mixes_of_another_user_link_through_their_profile(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    track = _track(alice, tmp_path, "t1")
    _mix(alice, "mix-a", [track])

    seen_by_bob = profile_mixes_payload(bob, "ALICE", limit=16)
    seen_by_alice = profile_mixes_payload(alice, "alice", limit=16)

    foreign, own = seen_by_bob["items"][0], seen_by_alice["items"][0]
    assert foreign["entity_type"] == "generated_mix" and foreign["entity_id"] == "mix-a"
    # The viewer's own /mixes/{id} endpoints only see the viewer's mixes, so a
    # foreign card must not point at them.
    assert foreign["action"]["target"] == "/u/Alice/mixes/mix-a"
    assert foreign["play_action"]["endpoint"] == "/api/v1/users/Alice/mixes/mix-a/play"
    assert own["action"]["target"] == "/mixes/mix-a"
    assert own["play_action"]["endpoint"] == "/api/v1/mixes/mix-a/play"


def test_mixes_cover_urls_follow_the_same_rule(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    track = _track(alice, tmp_path, "t1")
    _mix(alice, "mix-cover", [track], cover_path=str(tmp_path / "cover.jpg"))

    foreign = profile_mixes_payload(bob, "alice", limit=16)["items"][0]
    own = profile_mixes_payload(alice, "alice", limit=16)["items"][0]

    assert foreign["artwork"]["url"] == "/api/v1/users/Alice/mixes/mix-cover/cover"
    assert own["artwork"]["url"] == "/api/v1/mixes/mix-cover/cover"


def test_mixes_are_the_targets_own_paged_and_stale_ones_hidden(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    alice_track = _track(alice, tmp_path, "a1")
    bob_track = _track(bob, tmp_path, "b1")
    _mix(alice, "mix-1", [alice_track])
    _mix(alice, "mix-2", [alice_track], status="saved")
    _mix(alice, "mix-old", [alice_track], status="stale")
    _mix(bob, "mix-bob", [bob_track])

    page = profile_mixes_payload(bob, "alice", limit=1)
    rest = profile_mixes_payload(bob, "alice", limit=1, offset=1)

    assert page["total"] == 2 and page["next_offset"] == 1 and len(page["items"]) == 1
    assert rest["next_offset"] is None
    seen = {item["entity_id"] for item in page["items"] + rest["items"]}
    assert seen == {"mix-1", "mix-2"}


def test_mixes_shelf_is_empty_for_a_user_without_mixes(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    _mix(bob, "mix-bob", [_track(bob, tmp_path, "b1")])

    payload = profile_mixes_payload(bob, "alice", limit=16)

    assert payload["items"] == [] and payload["total"] == 0


def test_mix_detail_is_whitelisted_and_has_the_tracks_in_order(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    first, second = _track(alice, tmp_path, "d1"), _track(alice, tmp_path, "d2")
    _mix(alice, "mix-d", [first, second])

    detail = profile_mix_detail(bob, "alice", "mix-d")

    assert set(detail) == {"id", "title", "status", "subtitle", "track_count", "artwork", "created_at", "items"}
    assert detail["track_count"] == 2
    assert [item["track_id"] for item in detail["items"]] == [first, second]
    assert [item["track"]["id"] for item in detail["items"]] == [first, second]
    for item in detail["items"]:
        assert set(item) == {"position", "track_id", "track"}


def test_mix_of_another_user_stale_or_missing_is_not_found(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    _mix(alice, "mix-a", [_track(alice, tmp_path, "a1")])
    _mix(alice, "mix-old", [_track(alice, tmp_path, "a2")], status="stale")
    _mix(bob, "mix-bob", [_track(bob, tmp_path, "b1")])

    with pytest.raises(ProfileMixNotFoundError):
        profile_mix_detail(bob, "alice", "mix-old")
    with pytest.raises(ProfileMixNotFoundError):
        profile_mix_detail(bob, "alice", "nope")
    # Bob's own mix id under Alice's name: still not hers.
    with pytest.raises(ProfileMixNotFoundError):
        profile_mix_detail(bob, "alice", "mix-bob")
    # ...and a missing mix is also a "not found" of the profile family (HTTP 404).
    assert issubclass(ProfileMixNotFoundError, ProfileNotFoundError)


# ---------------------------------------------------------------------------
# Albums For You shelf
# ---------------------------------------------------------------------------

def test_albums_are_the_targets_cache_in_stable_order_with_paging(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    _albums(alice, [11, 12, 13])
    _albums(bob, [99])

    first = profile_albums_for_you_payload(bob, "alice", model_name=MODEL, limit=2)
    second = profile_albums_for_you_payload(bob, "alice", model_name=MODEL, limit=2, offset=2)

    # The cache is score-ordered; unlike the dashboard it is not shuffled, so pages are consistent.
    assert [item["entity_id"] for item in first["items"]] == [11, 12]
    assert [item["entity_id"] for item in second["items"]] == [13]
    assert (first["total"], first["next_offset"], second["next_offset"]) == (3, 2, None)


def test_album_reasons_speak_to_the_owner_only(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    _albums(alice, [11])

    own = profile_albums_for_you_payload(alice, "alice", model_name=MODEL, limit=16)["items"][0]
    other = profile_albums_for_you_payload(bob, "alice", model_name=MODEL, limit=16)["items"][0]

    assert own["reason"] == "You liked 5 tracks"
    assert other["reason"] is None
    assert other["action"]["target"] == "/releases/11"


def test_albums_without_a_cache_or_for_another_model_are_empty(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    _albums(alice, [11])

    assert profile_albums_for_you_payload(bob, "alice", model_name="other-model", limit=16)["items"] == []
    payload = profile_albums_for_you_payload(alice, "bob", model_name=MODEL, limit=16)
    assert payload["items"] == [] and payload["total"] == 0 and payload["next_offset"] is None


def test_recommendation_reads_refuse_service_principal_and_unknown_users(tmp_path: Path):
    _root, alice, _bob = _stores(tmp_path)
    service = Store(tmp_path / "app.db", user_id=None)

    with pytest.raises(ProfileViewerRequiredError):
        profile_mixes_payload(service, "alice", limit=16)
    with pytest.raises(ProfileViewerRequiredError):
        profile_albums_for_you_payload(service, "alice", model_name=MODEL, limit=16)
    with pytest.raises(ProfileNotFoundError):
        profile_mixes_payload(alice, "carol", limit=16)
    with pytest.raises(ProfileNotFoundError):
        profile_albums_for_you_payload(alice, "carol", model_name=MODEL, limit=16)


# ---------------------------------------------------------------------------
# API (auth enabled, two real sessions)
# ---------------------------------------------------------------------------

def _init_auth_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "svc-secret")
    monkeypatch.setenv("DISCOCS_DEFAULT_MODEL", MODEL)
    monkeypatch.setattr(
        auth,
        "verify_navidrome_credentials",
        lambda _settings, username, password, **_kwargs: (
            username in {"alice", "bob"} and password == "correct"
        ),
    )
    monkeypatch.setattr(auth, "sync_navidrome_starred_for_user", lambda *_args, **_kwargs: None)
    store = Store(db_path)
    store.init()
    return store


def _login(username: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "correct"})
    assert response.status_code == 200
    return client


def _user_store(store: Store, username: str) -> Store:
    return store.for_user(int(store.get_user_by_username(username)["id"]))


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys |= _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            keys |= _all_keys(nested)
    return keys


def test_api_mixes_and_albums_are_visible_to_other_users(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    track = _track(store, tmp_path, "api-track")
    _mix(alice_store, "mix-api", [track])
    _albums(alice_store, [5, 6, 7])

    mixes = bob.get("/api/v1/users/ALICE/mixes").json()
    detail = bob.get("/api/v1/users/alice/mixes/mix-api").json()
    albums = bob.get("/api/v1/users/alice/albums-for-you", params={"limit": 2, "offset": 1}).json()

    assert [item["entity_id"] for item in mixes["items"]] == ["mix-api"]
    assert mixes["items"][0]["action"]["target"] == "/u/alice/mixes/mix-api"
    assert [item["track_id"] for item in detail["items"]] == [track]
    assert [item["entity_id"] for item in albums["items"]] == [6, 7]
    assert (albums["total"], albums["next_offset"]) == (3, None)
    assert bob.get("/api/v1/users/alice/mixes/nope").status_code == 404


def test_api_recommendations_never_expose_scores_or_generation_settings(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    track = _track(store, tmp_path, "secret-track")
    _mix(alice_store, "mix-secret", [track])
    _albums(alice_store, [5])

    payloads = [
        bob.get("/api/v1/users/alice/mixes").json(),
        bob.get("/api/v1/users/alice/mixes/mix-secret").json(),
        bob.get("/api/v1/users/alice/albums-for-you").json(),
    ]
    keys = set().union(*(_all_keys(payload) for payload in payloads))
    text = json.dumps(payloads)

    forbidden = {
        "score", "score_breakdown", "score_summary", "anchor", "settings", "saved_playlist_id",
        "reason_json", "seed_track_ids",
    }
    assert keys.isdisjoint(forbidden), keys & forbidden
    # Neither the owner-addressed reason nor anything from the generation settings/scores.
    assert "You liked" not in text
    assert "region_similarity" not in text and "novelty_weight" not in text


def test_api_playing_a_foreign_mix_starts_the_viewers_own_session(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store, bob_store = _user_store(store, "alice"), _user_store(store, "bob")
    first, second = _track(store, tmp_path, "p1"), _track(store, tmp_path, "p2")
    _mix(alice_store, "mix-play", [first, second])

    # The viewer's own endpoint does not see Alice's mix...
    assert bob.post("/api/v1/mixes/mix-play/play").status_code == 404
    # ...the profile one plays it in Bob's session.
    response = bob.post("/api/v1/users/alice/mixes/mix-play/play")

    assert response.status_code == 200
    session_id = response.json()["session"]["id"]
    session = bob_store.get_playback_session(session_id)
    assert session is not None and session.source_type == "generated_mix"
    assert alice_store.get_playback_session(session_id) is None
    # No pointer to the owner's mix (the viewer's store cannot read it) and no owner scores.
    assert "source_mix_id" not in json.loads(session.settings_json or "{}")
    queue = bob_store.list_queue_items(session_id)
    assert [item.track_id for item in queue] == [first, second]
    assert all(item.score is None for item in queue)
    assert "region_similarity" not in json.dumps(response.json())


def test_api_playing_an_own_mix_through_the_profile_keeps_the_ordinary_session(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    alice_store = _user_store(store, "alice")
    track = _track(store, tmp_path, "own1")
    _mix(alice_store, "mix-own", [track])

    session_id = alice.post("/api/v1/users/alice/mixes/mix-own/play").json()["session"]["id"]

    session = alice_store.get_playback_session(session_id)
    assert session is not None
    assert json.loads(session.settings_json or "{}")["source_mix_id"] == "mix-own"
    assert alice_store.list_queue_items(session_id)[0].score == pytest.approx(0.91)


def test_api_playing_an_empty_mix_is_a_conflict(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    _mix(_user_store(store, "alice"), "mix-empty", [])

    assert bob.post("/api/v1/users/alice/mixes/mix-empty/play").status_code == 409


def test_api_foreign_mix_cover(tmp_path: Path, monkeypatch):
    store = _init_auth_store(tmp_path, monkeypatch)
    _login("alice")
    bob = _login("bob")
    alice_store = _user_store(store, "alice")
    track = _track(store, tmp_path, "c1")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8jpeg")
    _mix(alice_store, "mix-with-cover", [track], cover_path=str(cover))
    _mix(alice_store, "mix-no-cover", [track])

    served = bob.get("/api/v1/users/alice/mixes/mix-with-cover/cover")

    assert served.status_code == 200 and served.content == b"\xff\xd8jpeg"
    assert served.headers["content-type"] == "image/jpeg"
    assert bob.get("/api/v1/users/alice/mixes/mix-no-cover/cover").status_code == 404
    assert bob.get("/api/v1/users/alice/mixes/nope/cover").status_code == 404


_RECOMMENDATION_PATHS = ("mixes", "albums-for-you", "mixes/x", "mixes/x/cover")


def test_api_recommendations_unknown_user_is_404_and_service_principal_is_403(tmp_path: Path, monkeypatch):
    _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")
    service = TestClient(app)
    headers = {"X-Discocs-Service-Token": "svc-secret"}

    for path in _RECOMMENDATION_PATHS:
        assert alice.get(f"/api/v1/users/carol/{path}").status_code == 404, path
        assert service.get(f"/api/v1/users/alice/{path}", headers=headers).status_code == 403, path
        assert TestClient(app).get(f"/api/v1/users/alice/{path}").status_code == 401, path
    assert alice.post("/api/v1/users/carol/mixes/x/play").status_code == 404
    assert service.post("/api/v1/users/alice/mixes/x/play", headers=headers).status_code == 403
    assert TestClient(app).post("/api/v1/users/alice/mixes/x/play").status_code == 401


def test_api_rejects_invalid_limits(tmp_path: Path, monkeypatch):
    _init_auth_store(tmp_path, monkeypatch)
    alice = _login("alice")

    for path in ("mixes", "albums-for-you"):
        assert alice.get(f"/api/v1/users/alice/{path}", params={"limit": 0}).status_code == 422
        assert alice.get(f"/api/v1/users/alice/{path}", params={"limit": 101}).status_code == 422
        assert alice.get(f"/api/v1/users/alice/{path}", params={"offset": -1}).status_code == 422
