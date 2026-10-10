"""Store Settings domain: per-user key/value preferences (language, etc.).

Part of the app/store package. Do not import this module directly; use
app.store instead. Backed by the generic ``user_settings`` table so new
settings keys never require a schema migration — API-layer validation
(app/schemas/requests.py) is what constrains which keys/values are accepted.
"""
from __future__ import annotations

from app.avatars import AVATAR_SETTING_KEY
from app.models import utc_now

# Tracks the web player keeps downloaded ahead when that is switched on: the
# next one at least, at most 5 — each is a whole file in the phone's memory.
PREFETCH_TRACKS_MIN = 1
PREFETCH_TRACKS_MAX = 5
PREFETCH_TRACKS_DEFAULT = 1

DEFAULT_USER_SETTINGS: dict[str, object] = {
    "language": "en",
    "transcoding_enabled": False,
    "transcoding_bitrate_kbps": 192,
    # Off: the web player downloads nothing ahead — tracks just stream. On: it
    # keeps `prefetch_tracks` upcoming tracks downloaded whole.
    "prefetch_ahead_enabled": False,
    "prefetch_tracks": PREFETCH_TRACKS_DEFAULT,
}

_BOOL_SETTINGS = {"transcoding_enabled", "prefetch_ahead_enabled"}
_INT_SETTINGS = {"transcoding_bitrate_kbps", "prefetch_tracks"}


def _decode_setting(key: str, value: str) -> object:
    if key in _BOOL_SETTINGS:
        return value.strip().lower() == "true"
    if key in _INT_SETTINGS:
        try:
            number = int(value)
        except ValueError:
            return DEFAULT_USER_SETTINGS[key]
        if key == "prefetch_tracks":
            # A value saved under an older range still reads as a valid one.
            return min(max(number, PREFETCH_TRACKS_MIN), PREFETCH_TRACKS_MAX)
        return number
    return value


def _encode_setting(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class SettingsStoreMixin:
    def get_user_settings(self) -> dict[str, object]:
        with self.connect() as conn:
            # The avatar lives in this table too but is public profile data
            # with its own API (app/avatars.py), not a preference.
            rows = conn.execute(
                "SELECT key, value FROM user_settings "
                "WHERE user_id = discocs_user_id() AND key != ?",
                (AVATAR_SETTING_KEY,),
            ).fetchall()
        merged = dict(DEFAULT_USER_SETTINGS)
        merged.update(
            {
                str(row["key"]): _decode_setting(str(row["key"]), str(row["value"]))
                for row in rows
            }
        )
        return merged

    def set_user_settings(self, values: dict[str, object]) -> dict[str, object]:
        """Upsert one or more settings for the current user; return the merged result."""
        now = utc_now()
        with self.connect() as conn:
            for key, value in values.items():
                conn.execute(
                    """
                    INSERT INTO user_settings (user_id, key, value, updated_at)
                    VALUES (discocs_user_id(), ?, ?, ?)
                    ON CONFLICT(user_id, key)
                    DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, _encode_setting(value), now),
                )
        return self.get_user_settings()
