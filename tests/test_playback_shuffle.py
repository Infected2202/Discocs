"""Shuffle actually reorders a playback queue.

`mode` and `shuffle_enabled` used to be columns nothing read: a "shuffle"
session played its source in order and the only visible effect was the lit
icon in the player. These cover the ordering itself.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.scanner import ScannedTrack
from app.store import Store


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "app.db")
    store.init()
    return store


def _tracks(store: Store, tmp_path: Path, count: int) -> list[int]:
    ids = []
    for index in range(count):
        track_id, _changed = store.upsert_track(
            ScannedTrack(
                path=(tmp_path / f"{index:02d}.flac").resolve(),
                artist="Artist",
                title=f"Track {index}",
                album="Album",
                duration=100.0,
                file_size=1,
                mtime=1,
            )
        )
        ids.append(track_id)
    return ids


def _fixed_shuffle(monkeypatch, order: list[int]) -> None:
    """Make random.shuffle deterministic: reorder in place by the given indices."""
    def fake_shuffle(items: list) -> None:
        snapshot = list(items)
        items[:] = [snapshot[i] for i in order[: len(snapshot)]]

    monkeypatch.setattr("app.store.playback.random.shuffle", fake_shuffle)


def test_shuffle_mode_reorders_the_queue_and_picks_a_random_opener(tmp_path, monkeypatch):
    store = _store(tmp_path)
    ids = _tracks(store, tmp_path, 4)
    _fixed_shuffle(monkeypatch, [2, 0, 3, 1])

    session, queue = store.create_playback_session(
        source_type="playlist",
        source_label="Mixed",
        mode="shuffle",
        shuffle_enabled=True,
        track_ids=ids,
    )

    assert [item.track_id for item in queue] == [ids[2], ids[0], ids[3], ids[1]]
    # The queue's opener is what starts playing, so a shuffled queue is also
    # what makes the first track random.
    assert store.get_playback_session(session.id).current_track_id == ids[2]


def test_linear_mode_leaves_the_source_order_alone(tmp_path, monkeypatch):
    store = _store(tmp_path)
    ids = _tracks(store, tmp_path, 4)
    _fixed_shuffle(monkeypatch, [3, 2, 1, 0])

    _session, queue = store.create_playback_session(
        source_type="playlist",
        source_label="Ordered",
        mode="linear",
        track_ids=ids,
    )

    assert [item.track_id for item in queue] == ids


def test_turning_shuffle_on_spares_the_playing_track_and_its_history(tmp_path, monkeypatch):
    store = _store(tmp_path)
    ids = _tracks(store, tmp_path, 5)
    session, queue = store.create_playback_session(
        source_type="playlist", source_label="Live", track_ids=ids
    )
    store.update_playback_session(session.id, current_queue_item_id=queue[1].id)
    # Reverse whatever tail it is handed.
    monkeypatch.setattr(
        "app.store.playback.random.shuffle",
        lambda items: items.reverse(),
    )

    reordered = store.set_queue_shuffle(session.id, enabled=True)

    # Items 0 and 1 are history and the track playing right now; reshuffling
    # them would rewrite what was just heard and restart the current track.
    assert [item.track_id for item in reordered][:2] == ids[:2]
    assert [item.track_id for item in reordered][2:] == [ids[4], ids[3], ids[2]]


def test_turning_shuffle_off_restores_the_order_the_queue_was_built_in(tmp_path, monkeypatch):
    store = _store(tmp_path)
    ids = _tracks(store, tmp_path, 4)
    _fixed_shuffle(monkeypatch, [3, 1, 0, 2])
    session, queue = store.create_playback_session(
        source_type="playlist", source_label="Mixed", mode="shuffle", track_ids=ids
    )
    assert [item.track_id for item in queue] != ids

    restored = store.set_queue_shuffle(session.id, enabled=False)

    assert [item.track_id for item in restored] == ids


def test_positions_stay_unique_and_gapless_after_a_reorder(tmp_path, monkeypatch):
    # (session_id, position) is a unique index, so a naive row-by-row rewrite
    # collides with a position the reorder has not moved yet.
    store = _store(tmp_path)
    ids = _tracks(store, tmp_path, 6)
    session, _queue = store.create_playback_session(
        source_type="playlist", source_label="Live", track_ids=ids
    )
    monkeypatch.setattr("app.store.playback.random.shuffle", lambda items: items.reverse())

    reordered = store.set_queue_shuffle(session.id, enabled=True)

    assert [item.position for item in reordered] == list(range(len(ids)))


def test_reordering_an_empty_queue_is_harmless(tmp_path):
    store = _store(tmp_path)
    session, _queue = store.create_playback_session(
        source_type="playlist", source_label="Empty", track_ids=[]
    )

    assert store.set_queue_shuffle(session.id, enabled=True) == []


def test_reordering_an_unknown_session_is_rejected(tmp_path):
    store = _store(tmp_path)

    with pytest.raises(ValueError):
        store.set_queue_shuffle("no-such-session", enabled=True)
