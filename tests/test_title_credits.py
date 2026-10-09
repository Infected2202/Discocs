"""Фиты и ремиксеры из названия трека — артисты с ролями featured/remixer."""
from pathlib import Path

from app.scanner import ScannedTrack
from app.serializers.entities import track_summary_dict
from app.store import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def _add(store: Store, name: str, *, artist: str, title: str, album: str = "Album") -> int:
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path(f"/music/{artist}/{album}/{name}.flac"),
            artist=artist,
            title=title,
            album=album,
            duration=200.0,
            file_size=100,
            mtime=1,
        )
    )
    return track_id


def _roles(store: Store, track_id: int) -> list[tuple[str, str, str | None]]:
    artists = store.artists_for_tracks([track_id])[track_id]
    return [(artist.name, artist.role, artist.credit_text) for artist in artists]


def _artist_id(store: Store, name: str) -> int | None:
    return store.artist_ids_by_names([name]).get(name.casefold())


def test_featured_artist_from_title_becomes_a_credited_artist(tmp_path):
    store = _store(tmp_path)
    track_id = _add(store, "02", artist="Locked Club", title="Pi Pu Pa (ft. RLGN)", album="Sadism")

    assert _roles(store, track_id) == [
        ("Locked Club", "primary", "Locked Club"),
        ("RLGN", "featured", "RLGN"),
    ]
    rlgn_id = _artist_id(store, "RLGN")
    assert rlgn_id is not None
    # Не основной артист релиза — релиз у него в «Участии».
    discography = store.artist_discography(rlgn_id)
    assert [row.release.title for row in discography["featured_in"]] == ["Sadism"]
    assert discography["albums"] == discography["releases"] == []
    # Артист ищется, хотя ни одного своего релиза у него нет.
    found = store.search_entities("RLGN")["artists"]["items"]
    assert [row.artist.id for row in found] == [rlgn_id]


def test_title_credits_do_not_change_the_release_artist(tmp_path):
    store = _store(tmp_path)
    for index in range(3):
        _add(store, f"0{index}", artist="Locked Club", title=f"Song {index} (Guest {index} Remix)", album="EP")

    release_id = store.release_id_for_track(_add(store, "09", artist="Locked Club", title="Last", album="EP"))
    release = store.get_release(release_id)
    assert release is not None
    assert [artist.name for artist in release.artists] == ["Locked Club"]


def test_version_words_need_an_artist_the_library_already_has(tmp_path):
    store = _store(tmp_path)
    unknown = _add(store, "01", artist="Main", title="Tune (Break Version)")
    assert _roles(store, unknown) == [("Main", "primary", "Main")]
    assert _artist_id(store, "Break") is None

    _add(store, "02", artist="Coyu", title="Own Track", album="Coyu Album")
    known = _add(store, "03", artist="Main", title="Tune (Coyu Edit)")
    assert _roles(store, known) == [("Main", "primary", "Main"), ("Coyu", "remixer", "Coyu")]


def test_known_duo_stays_whole_unknown_one_splits(tmp_path):
    store = _store(tmp_path)
    split = _add(store, "01", artist="Main", title="Tune (Crosby, Stills Remix)")
    assert [name for name, role, _text in _roles(store, split) if role == "remixer"] == ["Crosby", "Stills"]

    # Тег артиста запятую не делит — «Crosby, Stills» целиком теперь известен.
    _add(store, "02", artist="Crosby, Stills", title="Own", album="Duo")
    whole = _add(store, "03", artist="Main", title="Tune 2 (Crosby, Stills Remix)")
    assert [name for name, role, _text in _roles(store, whole) if role == "remixer"] == ["Crosby, Stills"]


def test_title_credit_keeps_the_existing_artist_spelling(tmp_path):
    store = _store(tmp_path)
    _add(store, "01", artist="Solomun", title="Own", album="Solomun Album")
    track_id = _add(store, "02", artist="Main", title="Tune (SOLOMUN Remix)")

    assert _roles(store, track_id)[1] == ("Solomun", "remixer", "SOLOMUN")


def test_main_artist_named_in_title_is_not_credited_twice(tmp_path):
    store = _store(tmp_path)
    track_id = _add(store, "01", artist="Alpha feat. Beta", title="Tune (feat. Beta)")

    assert _roles(store, track_id) == [("Alpha", "primary", "Alpha feat. Beta"), ("Beta", "primary", "Alpha feat. Beta")]


def test_renamed_title_drops_old_credits(tmp_path):
    store = _store(tmp_path)
    track_id = _add(store, "01", artist="Main", title="Tune (ft. Guest)")
    assert len(_roles(store, track_id)) == 2

    _add(store, "01", artist="Main", title="Tune")
    assert _roles(store, track_id) == [("Main", "primary", "Main")]


def test_navidrome_remixer_tag_credits_the_remixer(tmp_path):
    store = _store(tmp_path)
    track_id, _changed = store.upsert_track(
        ScannedTrack(
            path=Path("navidrome://song-1"),
            artist="Main",
            title="Tune",
            album="Album",
            duration=123.0,
            file_size=100,
            mtime=1,
        )
    )
    store.upsert_external_track(
        "navidrome",
        "song-1",
        track_id,
        raw_json='{"albumId":"album-1","contributors":[{"role":"remixer","artist":{"name":"Tag Remixer"}}]}',
    )

    store.backfill_library_normalization()

    assert _roles(store, track_id) == [("Main", "primary", "Main"), ("Tag Remixer", "remixer", "Tag Remixer")]


def test_track_payload_keeps_artists_main_and_lists_credits_separately(tmp_path):
    store = _store(tmp_path)
    track_id = _add(store, "01", artist="Locked Club", title="Sadism (ft. Any Act) [Coyu Remix]")
    track = store.get_track(track_id)
    assert track is not None

    payload = track_summary_dict(store, track, store.artists_for_tracks([track_id])[track_id])

    assert [artist["name"] for artist in payload["artists"]] == ["Locked Club"]
    assert [(credit["name"], credit["role"], credit["text"]) for credit in payload["credits"]] == [
        ("Any Act", "featured", "Any Act"),
        ("Coyu", "remixer", "Coyu"),
    ]


def test_title_credits_follow_main_artists_in_position(tmp_path):
    store = _store(tmp_path)
    track_id = _add(store, "01", artist="Main", title="Tune (Guest Remix)")
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT role FROM track_artists WHERE track_id = ? ORDER BY position", (track_id,)
        ).fetchall()
    assert [row["role"] for row in rows] == ["primary", "remixer"]
    # Первый артист трека по position — основной (flow_candidates берёт его).
    assert store.artist_ids_for_track(track_id)[0] == _artist_id(store, "Main")


def test_listening_stats_count_only_main_artists(tmp_path):
    from app.models import utc_now
    from app.store import PlaybackEventCreate

    base = _store(tmp_path)
    track_id = _add(base, "01", artist="Main", title="Tune (Guest Remix)")
    user = base.for_user(base.upsert_user("alice", now=utc_now()))
    user.record_playback_event(PlaybackEventCreate(event_type="play_threshold_reached", track_id=track_id))

    assert [item.name for item in user.top_listened_artists()] == ["Main"]
    assert user.count_listened_artists() == 1
    assert user.listen_summary().artists == 1


def test_known_artist_followed_by_a_version_name_credits_the_artist(tmp_path):
    store = _store(tmp_path)
    _add(store, "01", artist="Hot Since 82", title="Own", album="HS82")
    _add(store, "02", artist="Carl Craig", title="Own", album="CC")

    remix = _add(store, "03", artist="Main", title="Tune (Hot Since 82 Future Remix)")
    mix = _add(store, "04", artist="Main", title="Tune 2 (Carl Craig Late Island Mix)")
    club = _add(store, "06", artist="Main", title="Tune 4 (Carl Craig Club Remix)")
    unknown = _add(store, "05", artist="Main", title="Tune 3 (Bombay Dub Orchestra Remix)")

    assert _roles(store, remix)[1] == ("Hot Since 82", "remixer", "Hot Since 82")
    assert _roles(store, mix)[1] == ("Carl Craig", "remixer", "Carl Craig")
    assert _roles(store, club)[1] == ("Carl Craig", "remixer", "Carl Craig")
    # Ни одно начало имени не известно — новый артист целиком.
    assert _roles(store, unknown)[1] == ("Bombay Dub Orchestra", "remixer", "Bombay Dub Orchestra")
