from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth
from app.api.auth_middleware import is_public_share_request
from app.config import Settings, SharingSettings
from app.main import app
from app.models import utc_now
from app.store import INITIALIZED_DB_PATHS, Store
from app.store.shares import share_token_hash


def test_sharing_is_enabled_by_default_and_supports_explicit_opt_out(monkeypatch):
    token = "A" * 43
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/public/shares/{token}",
            "headers": [],
        }
    )

    monkeypatch.delenv("DISCOCS_SHARING_ENABLED", raising=False)
    assert SharingSettings().enabled is True
    assert Settings.from_env().sharing.enabled is True
    assert is_public_share_request(request) is True

    monkeypatch.setenv("DISCOCS_SHARING_ENABLED", "false")
    assert Settings.from_env().sharing.enabled is False
    assert is_public_share_request(request) is False


def _init_store(tmp_path: Path, monkeypatch) -> Store:
    db_path = tmp_path / "app.db"
    INITIALIZED_DB_PATHS.discard(db_path.resolve())
    monkeypatch.setenv("DISCOCS_DB_PATH", str(db_path))
    monkeypatch.setenv("DISCOCS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_INDEX_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOCS_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("DISCOCS_AUTH_ENABLED", "true")
    monkeypatch.setenv("DISCOCS_SHARING_ENABLED", "true")
    store = Store(db_path)
    store.init()
    return store


def _track(store: Store, path: Path, *, title: str, artist: str = "Artist") -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"public-audio")
    now = utc_now()
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tracks (
                path, artist, title, album, duration, file_size, mtime,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(path), artist, title, "Album", 123.0, path.stat().st_size, 1, now, now),
        )
        return int(cursor.lastrowid)


def _release(store: Store, track_ids: list[int], *, title: str = "Album") -> int:
    now = utc_now()
    with store.connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO releases (
                title, normalized_title, release_type, track_count,
                identity_key, identity_confidence, created_at, updated_at
            ) VALUES (?, ?, 'album', ?, ?, 'exact', ?, ?)
            """,
            (title, title.casefold(), len(track_ids), f"test:{title}", now, now),
        )
        release_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO release_tracks (
                release_id, track_id, position, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [(release_id, track_id, position, now, now) for position, track_id in enumerate(track_ids)],
        )
    return release_id


def _user_store(store: Store, username: str = "alice") -> Store:
    user_id = store.upsert_user(username, now=utc_now())
    scoped = Store(store.db_path, user_id=user_id)
    scoped.init()
    return scoped


def _future(days: int = 7) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _session_client(store: Store, username: str = "alice") -> TestClient:
    token = auth.create_session(store, Settings.from_env(), username, "nav-password")
    client = TestClient(app)
    client.cookies.set("discocs_session", token)
    return client


def test_share_store_hashes_token_and_freezes_release_membership(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    first = _track(store, tmp_path / "01.flac", title="First")
    second = _track(store, tmp_path / "02.flac", title="Second")
    release_id = _release(store, [first, second])
    scoped = _user_store(store)

    share, token = scoped.create_share(
        source_type="release",
        source_id=release_id,
        expires_at=_future(),
    )

    assert len(token) >= 40
    assert share.token_hash == share_token_hash(token)
    assert token != share.token_hash
    with store.connect() as conn:
        persisted = dict(conn.execute("SELECT * FROM shares WHERE id = ?", (share.id,)).fetchone())
    assert token not in " ".join(str(value) for value in persisted.values())
    assert [item.track_id for item in scoped.list_share_items(share.id)] == [first, second]

    third = _track(store, tmp_path / "03.flac", title="Third")
    now = utc_now()
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO release_tracks (release_id, track_id, position, created_at, updated_at)
            VALUES (?, ?, 2, ?, ?)
            """,
            (release_id, third, now, now),
        )
    assert [item.track_id for item in scoped.list_share_items(share.id)] == [first, second]


def test_share_management_is_owner_scoped_and_revoke_is_immediate(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "one.flac", title="One")
    alice = _user_store(store, "alice")
    bob = _user_store(store, "bob")
    share, token = alice.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )

    assert bob.get_user_share(share.id) is None
    assert bob.revoke_user_share(share.id) is False
    assert alice.resolve_active_share(share_token_hash(token)) is not None
    assert alice.revoke_user_share(share.id) is True
    assert alice.resolve_active_share(share_token_hash(token)) is None


def test_public_share_is_only_narrow_anonymous_auth_exception(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "public.flac", title="Public")
    scoped = _user_store(store)
    _share, token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )
    client = TestClient(app)

    public = client.get(f"/api/v1/public/shares/{token}")
    protected = client.get(f"/api/v1/tracks/{track_id}")
    unsafe = client.post(f"/api/v1/public/shares/{token}")
    neighbor = client.get(f"/api/v1/public/sharesx/{token}")
    preview_neighbor = client.get(f"/api/v1/public/shares/{token}/preview/extra")

    assert public.status_code == 200
    assert protected.status_code == 401
    assert unsafe.status_code == 401
    assert neighbor.status_code == 401
    assert preview_neighbor.status_code == 401
    assert public.headers["cache-control"] == "private, no-cache"
    assert public.headers["referrer-policy"] == "no-referrer"
    assert public.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


def test_public_metadata_exposes_no_internal_identifiers(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "secret-path.flac", title="Visible title")
    scoped = _user_store(store)
    _share, token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )

    payload = TestClient(app).get(f"/api/v1/public/shares/{token}").json()
    serialized = str(payload)
    assert payload["items"][0]["position"] == 0
    assert payload["items"][0]["title"] == "Visible title"
    assert "track_id" not in serialized
    assert "owner" not in serialized
    assert "secret-path.flac" not in serialized
    assert "navidrome" not in serialized


def test_public_preview_exposes_universal_metadata_without_audio(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(
        store,
        tmp_path / "preview.flac",
        title='Visible <track> & "friends"',
        artist="Preview Artist",
    )
    scoped = _user_store(store)
    share, token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )

    response = TestClient(app, base_url="http://backend:7752").get(
        f"/api/v1/public/shares/{token}/preview",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "music.example",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "public, max-age=300"
    assert "x-robots-tag" not in response.headers
    assert 'property="og:type" content="website"' in response.text
    assert 'property="og:title" content="Visible &lt;track&gt; &amp; &quot;friends&quot;"' in response.text
    assert 'property="og:description" content="Preview Artist · Album · 2:03"' in response.text
    assert f'property="og:url" content="https://music.example/share/{token}"' in response.text
    assert f'property="og:image" content="https://music.example/api/v1/public/shares/{token}/cover?preview=1"' in response.text
    assert 'name="twitter:card" content="summary_large_image"' in response.text
    assert "music:" not in response.text
    assert "og:audio" not in response.text
    assert "<audio" not in response.text
    assert 'name="robots"' not in response.text
    assert scoped.get_user_share(share.id).access_count == 0

    head = TestClient(app, base_url="http://backend:7752").head(
        f"/api/v1/public/shares/{token}/preview",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "music.example",
        },
    )
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-type"].startswith("text/html")
    assert head.headers["cache-control"] == "public, max-age=300"
    assert "x-robots-tag" not in head.headers


def test_public_release_preview_contains_album_metadata(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    first = _track(store, tmp_path / "first.flac", title="First", artist="Album Artist")
    second = _track(store, tmp_path / "second.flac", title="Second", artist="Album Artist")
    release_id = _release(store, [first, second], title="Album Title")
    scoped = _user_store(store)
    _share, token = scoped.create_share(
        source_type="release", source_id=release_id, expires_at=_future()
    )

    response = TestClient(app).get(f"/api/v1/public/shares/{token}/preview")

    assert response.status_code == 200
    assert 'property="og:type" content="website"' in response.text
    assert 'property="og:title" content="Album Title"' in response.text
    assert 'property="og:description" content="Album Artist · 2 tracks · 4:06"' in response.text
    assert "music:" not in response.text


def test_public_cover_and_audio_are_privately_cacheable(tmp_path, monkeypatch):
    from app.navidrome import CoverArt

    class FakeNavidromeClient:
        def __init__(self, settings):
            self.settings = settings

        def get_cover_art(self, cover_art_id, *, size):
            assert cover_art_id == "cached-cover"
            assert size == 1000
            return CoverArt(b"cover", "image/jpeg")

    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "cache.flac", title="Cached")
    scoped = _user_store(store)
    _share, token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )
    monkeypatch.setattr(
        "app.api.shares._cover_art_id",
        lambda store, share: "cached-cover",
    )
    monkeypatch.setattr("app.api.shares.NavidromeClient", FakeNavidromeClient)
    client = TestClient(app)

    cover = client.get(f"/api/v1/public/shares/{token}/cover")
    cover_head = client.head(f"/api/v1/public/shares/{token}/cover")
    preview_cover = client.get(f"/api/v1/public/shares/{token}/cover?preview=1")
    preview_cover_head = client.head(f"/api/v1/public/shares/{token}/cover?preview=1")
    audio = client.get(f"/api/v1/public/shares/{token}/items/0/audio")

    assert cover.status_code == 200
    assert cover.headers["cache-control"] == "private, max-age=3600"
    assert cover.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert cover_head.status_code == 200
    assert cover_head.content == b""
    assert cover_head.headers["content-type"] == "image/jpeg"
    assert cover_head.headers["cache-control"] == "private, max-age=3600"
    assert preview_cover.status_code == 200
    assert preview_cover.headers["cache-control"] == "public, max-age=3600"
    assert "x-robots-tag" not in preview_cover.headers
    assert preview_cover_head.status_code == 200
    assert preview_cover_head.content == b""
    assert preview_cover_head.headers["cache-control"] == "public, max-age=3600"
    assert "x-robots-tag" not in preview_cover_head.headers
    assert audio.status_code == 200
    assert audio.headers["cache-control"] == "private, max-age=3600"


def test_public_audio_resolves_only_member_position(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "member.flac", title="Member")
    scoped = _user_store(store)
    _share, token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )
    client = TestClient(app)

    audio = client.get(f"/api/v1/public/shares/{token}/items/0/audio")
    unknown = client.get(f"/api/v1/public/shares/{token}/items/1/audio")
    head = client.head(f"/api/v1/public/shares/{token}/items/0/audio")

    assert audio.status_code == 200
    assert audio.content == b"public-audio"
    assert audio.headers["content-disposition"] == "inline"
    assert unknown.status_code == 404
    assert head.status_code == 200
    assert head.content == b""
    # Completed responses release their concurrency slot; sequential listening
    # must never exhaust the per-client limit.
    assert all(
        client.get(f"/api/v1/public/shares/{token}/items/0/audio").status_code == 200
        for _ in range(5)
    )


def test_expired_revoked_and_unknown_share_have_same_public_response(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "one.flac", title="One")
    scoped = _user_store(store)
    expired, expired_token = scoped.create_share(
        source_type="track",
        source_id=track_id,
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    revoked, revoked_token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )
    scoped.revoke_user_share(revoked.id)
    client = TestClient(app)

    responses = [
        client.get(f"/api/v1/public/shares/{expired_token}"),
        client.get(f"/api/v1/public/shares/{revoked_token}"),
        client.get("/api/v1/public/shares/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
    ]
    assert expired.id != revoked.id
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert len({response.text for response in responses}) == 1


def test_public_navidrome_audio_uses_service_account_not_creator_secret(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    monkeypatch.setenv("DISCOCS_NAVIDROME_URL", "http://navidrome:4533")
    monkeypatch.setenv("DISCOCS_NAVIDROME_USER", "share-service")
    monkeypatch.setenv("DISCOCS_NAVIDROME_PASSWORD", "service-secret")
    track_id = _track(store, tmp_path / "mapped.flac", title="Mapped")
    store.upsert_external_track("navidrome", "song-1", track_id)
    scoped = _user_store(store)
    _share, token = scoped.create_share(
        source_type="track", source_id=track_id, expires_at=_future()
    )
    seen: dict[str, str] = {}

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "audio/mpeg", "Content-Length": "5"}

        def getcode(self):
            return 200

        def read(self, _size=-1):
            if seen.get("read"):
                return b""
            seen["read"] = "yes"
            return b"audio"

        def close(self):
            pass

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["timeout"] = str(timeout)
        return FakeResponse()

    monkeypatch.setattr("app.api.tracks.urlopen", fake_urlopen)
    response = TestClient(app).get(f"/api/v1/public/shares/{token}/items/0/audio")

    assert response.status_code == 200
    assert response.content == b"audio"
    query = parse_qs(urlparse(seen["url"]).query)
    assert query["u"] == ["share-service"]
    assert query["format"] == ["mp3"]
    assert query["maxBitRate"] == ["320"]
    assert query["estimateContentLength"] == ["true"]
    assert "alice" not in seen["url"]


def test_management_api_creates_lists_and_revokes_share(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "managed.flac", title="Managed")
    client = _session_client(store)

    assert client.get("/api/v1/shares/capabilities").json() == {
        "enabled": True,
        "can_create": True,
    }

    created = client.post(
        "/api/v1/shares",
        json={"source_type": "track", "source_id": track_id, "title": "For a friend"},
    )
    assert created.status_code == 201
    assert created.json()["url"].startswith("http://testserver/share/")
    share_id = created.json()["share"]["id"]

    listed = client.get("/api/v1/shares").json()["items"]
    assert [item["id"] for item in listed] == [share_id]
    assert "url" not in listed[0]

    revoked = client.delete(f"/api/v1/shares/{share_id}")
    assert revoked.status_code == 204
    assert client.get(f"/api/v1/shares/{share_id}").json()["status"] == "revoked"


def test_management_api_allows_any_authenticated_user(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "shared.flac", title="Shared")
    client = _session_client(store, "bob")

    assert client.get("/api/v1/shares/capabilities").json() == {
        "enabled": True,
        "can_create": True,
    }
    response = client.post(
        "/api/v1/shares",
        json={"source_type": "track", "source_id": track_id},
    )
    assert response.status_code == 201


def test_management_api_rejects_service_principal(tmp_path, monkeypatch):
    store = _init_store(tmp_path, monkeypatch)
    track_id = _track(store, tmp_path / "service.flac", title="Service")
    monkeypatch.setenv("DISCOCS_SERVICE_TOKEN", "service-secret")
    client = TestClient(app)
    headers = {"x-discocs-service-token": "service-secret"}

    assert client.get("/api/v1/shares/capabilities", headers=headers).json() == {
        "enabled": True,
        "can_create": False,
    }
    response = client.post(
        "/api/v1/shares",
        json={"source_type": "track", "source_id": track_id},
        headers=headers,
    )
    assert response.status_code == 401
