from app.serializers.search import dashboard_shelf_item, search_group, search_top_result


def test_search_group_calculates_next_offset_until_last_page() -> None:
    first_page = search_group("tracks", "Tracks", [{"id": 1}], total=3, limit=1, offset=0)
    last_page = search_group("tracks", "Tracks", [{"id": 3}], total=3, limit=1, offset=2)

    assert first_page["next_offset"] == 1
    assert first_page["total"] == 3
    assert last_page["next_offset"] is None


def test_search_top_result_prefers_exact_artist_over_partial_track_match() -> None:
    artist = {"id": 1, "name": "Miles Davis"}
    track = {
        "id": 2,
        "title": "Miles Runs the Voodoo Down",
        "artists": [{"id": 1, "name": "Miles Davis Quintet"}],
        "release": {"id": 3, "title": "Bitches Brew"},
    }

    result = search_top_result("Miles Davis", [artist], [], [track])

    assert result == {"entity_type": "artist", "entity": artist}


def test_search_top_result_uses_release_artist_and_track_release_fields() -> None:
    release = {
        "id": 10,
        "title": "Kind of Blue",
        "artists": [{"id": 1, "name": "Miles Davis"}],
    }
    track = {
        "id": 20,
        "title": "So What",
        "artists": [{"id": 1, "name": "Miles Davis"}],
        "release": {"id": 10, "title": "Kind of Blue"},
    }

    assert search_top_result("Kind", [], [release], [track]) == {
        "entity_type": "release",
        "entity": release,
    }
    assert search_top_result("So", [], [release], [track]) == {
        "entity_type": "track",
        "entity": track,
    }


def test_dashboard_shelf_item_defaults_artwork_and_play_action() -> None:
    item = dashboard_shelf_item(
        "release",
        42,
        "Head Hunters",
        "Herbie Hancock",
        "/releases/42",
        reason="Because you liked fusion",
    )

    assert item["id"] == "release:42"
    assert item["artwork"] == {"url": None, "source": "none", "placeholder": True}
    assert item["play_action"] == {"type": "play", "source_type": "release", "source_id": 42}
    assert item["badges"] == []
    assert item["reason"] == "Because you liked fusion"


def test_dashboard_shelf_item_allows_custom_play_source_and_debug() -> None:
    item = dashboard_shelf_item(
        "track",
        7,
        "Chameleon",
        "Herbie Hancock",
        "?view=recommendations&seed=7",
        artwork_url="/cover",
        play_source_type="mix",
        play_source_id=99,
        badges=["Track"],
        subtitle_links=[{"label": "Herbie Hancock", "href": "/artists/1"}],
        debug={"score": 0.9},
    )

    assert item["artwork"] == {"url": "/cover", "source": "local", "placeholder": False}
    assert item["play_action"] == {"type": "play", "source_type": "mix", "source_id": 99}
    assert item["badges"] == ["Track"]
    assert item["subtitle_links"] == [{"label": "Herbie Hancock", "href": "/artists/1"}]
    assert item["debug"] == {"score": 0.9}
