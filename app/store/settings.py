"""Store Settings domain: per-user key/value preferences (language, etc.).

Part of the app/store package. Do not import this module directly; use
app.store instead. Backed by the generic ``user_settings`` table so new
settings keys never require a schema migration — API-layer validation
(app/schemas/requests.py) is what constrains which keys/values are accepted.
"""
from __future__ import annotations

from app.models import utc_now

DEFAULT_USER_SETTINGS: dict[str, str] = {
    "language": "en",
}


class SettingsStoreMixin:
    def get_user_settings(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT key, value FROM user_settings WHERE user_id = discocs_user_id()"
            ).fetchall()
        merged = dict(DEFAULT_USER_SETTINGS)
        merged.update({str(row["key"]): str(row["value"]) for row in rows})
        return merged

    def set_user_settings(self, values: dict[str, str]) -> dict[str, str]:
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
                    (key, value, now),
                )
        return self.get_user_settings()
