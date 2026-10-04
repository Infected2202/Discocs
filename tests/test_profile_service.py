"""Profile access layer: username resolution, target-scoped reads, visibility rules."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.avatars import AVATAR_KEYS
from app.models import utc_now
from app.services.profile import (
    ProfileNotFoundError,
    ProfileViewerRequiredError,
    can_view_private_playlists,
    profile_header,
    profile_playlists,
    resolve_profile_target,
)
from app.store import Store


def _stores(tmp_path: Path) -> tuple[Store, Store, Store]:
    root = Store(tmp_path / "app.db")
    root.init()
    alice = root.for_user(root.upsert_user("Alice", now=utc_now()))
    bob = root.for_user(root.upsert_user("bob", now=utc_now()))
    return root, alice, bob


def test_username_resolves_case_insensitively_to_a_target_scoped_store(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)

    target = resolve_profile_target(bob, "aLiCe")

    assert target.user_id == alice.user_id
    assert target.username == "Alice"
    assert target.store.user_id == alice.user_id
    assert bob.user_id != alice.user_id


def test_unknown_username_is_not_found(tmp_path: Path):
    _root, _alice, bob = _stores(tmp_path)

    with pytest.raises(ProfileNotFoundError):
        resolve_profile_target(bob, "carol")
    with pytest.raises(ProfileNotFoundError):
        resolve_profile_target(bob, "   ")


def test_service_principal_cannot_view_profiles(tmp_path: Path):
    _stores(tmp_path)
    service = Store(tmp_path / "app.db", user_id=None)

    with pytest.raises(ProfileViewerRequiredError):
        resolve_profile_target(service, "Alice")
    with pytest.raises(ProfileViewerRequiredError):
        profile_header(service, "Alice")


def test_header_has_only_whitelisted_fields(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)

    own = profile_header(alice, "alice")
    other = profile_header(bob, "ALICE")

    assert set(own) == {"username", "avatar", "created_at", "viewer_is_owner"}
    assert own["viewer_is_owner"] is True
    assert other["viewer_is_owner"] is False
    assert own["username"] == other["username"] == "Alice"
    assert own["avatar"] in AVATAR_KEYS
    # The avatar is assigned once, so both viewers see the same one.
    assert other["avatar"] == own["avatar"]


def test_private_playlists_only_on_own_profile(tmp_path: Path):
    _root, alice, bob = _stores(tmp_path)
    alice.create_playlist(title="Alice private")
    alice.create_playlist(title="Alice public", source={"visibility": "public"})
    bob.create_playlist(title="Bob public", source={"visibility": "public"})

    seen_by_owner = {playlist.title for playlist in profile_playlists(alice, "Alice")}
    seen_by_bob = {playlist.title for playlist in profile_playlists(bob, "alice")}

    assert seen_by_owner == {"Alice private", "Alice public"}
    assert seen_by_bob == {"Alice public"}
    assert {playlist.title for playlist in profile_playlists(alice, "bob")} == {"Bob public"}


def test_can_view_private_playlists_rule():
    assert can_view_private_playlists(1, 1) is True
    assert can_view_private_playlists(2, 1) is False
    assert can_view_private_playlists(None, 1) is False
