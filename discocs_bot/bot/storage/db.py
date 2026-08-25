import logging
from pathlib import Path

import aiosqlite

from bot.config import Settings
from bot.storage.user_prefs import DEFAULT_AUDIO_PROFILE, normalize_audio_profile

logger = logging.getLogger(__name__)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS telegram_audio_cache (
    navidrome_song_id TEXT NOT NULL,
    telegram_file_id TEXT NOT NULL,
    telegram_file_unique_id TEXT,
    bitrate TEXT NOT NULL,
    file_size INTEGER,
    duration INTEGER,
    title TEXT,
    artist TEXT,
    album TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    PRIMARY KEY (navidrome_song_id, bitrate)
);

CREATE TABLE IF NOT EXISTS users (
    telegram_user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    role TEXT NOT NULL DEFAULT 'friend',
    audio_profile TEXT NOT NULL DEFAULT '{DEFAULT_AUDIO_PROFILE}',
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_media (
    media_key TEXT PRIMARY KEY,
    url_key TEXT NOT NULL,
    source TEXT NOT NULL,
    webpage_url TEXT NOT NULL,
    telegram_file_id TEXT,
    title TEXT NOT NULL,
    artist TEXT,
    duration INTEGER,
    thumbnail_url TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS external_audio_cache (
    media_key TEXT NOT NULL,
    profile TEXT NOT NULL,
    part_index INTEGER NOT NULL,
    part_count INTEGER NOT NULL,
    telegram_file_id TEXT NOT NULL,
    file_size INTEGER,
    duration INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (media_key, part_index)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER,
    navidrome_song_id TEXT,
    event_type TEXT NOT NULL,
    context TEXT,
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, settings: Settings) -> None:
        self._path = settings.sqlite_path
        self._conn: aiosqlite.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate_schema()
        await self._conn.commit()
        logger.info("SQLite ready at %s", self._path)

    async def _migrate_schema(self) -> None:
        conn = self._require_conn()

        async with conn.execute("PRAGMA table_info(users)") as cursor:
            user_cols = {row[1] for row in await cursor.fetchall()}
        if user_cols and "audio_profile" not in user_cols:
            await conn.execute(
                f"ALTER TABLE users ADD COLUMN audio_profile TEXT NOT NULL DEFAULT '{DEFAULT_AUDIO_PROFILE}'"
            )

        async with conn.execute("PRAGMA table_info(external_media)") as cursor:
            media_cols = {row[1] for row in await cursor.fetchall()}
        if media_cols and "telegram_file_id" not in media_cols:
            await conn.execute("ALTER TABLE external_media ADD COLUMN telegram_file_id TEXT")

        async with conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'telegram_audio_cache'"
        ) as cursor:
            row = await cursor.fetchone()
        if not row or not row[0]:
            return
        create_sql = row[0]
        if "PRIMARY KEY (navidrome_song_id, bitrate)" in create_sql:
            return

        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_audio_cache_new (
                navidrome_song_id TEXT NOT NULL,
                telegram_file_id TEXT NOT NULL,
                telegram_file_unique_id TEXT,
                bitrate TEXT NOT NULL,
                file_size INTEGER,
                duration INTEGER,
                title TEXT,
                artist TEXT,
                album TEXT,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                PRIMARY KEY (navidrome_song_id, bitrate)
            );
            INSERT OR IGNORE INTO telegram_audio_cache_new
            SELECT * FROM telegram_audio_cache;
            DROP TABLE telegram_audio_cache;
            ALTER TABLE telegram_audio_cache_new RENAME TO telegram_audio_cache;
            """
        )
        logger.info("Migrated telegram_audio_cache to composite primary key")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def get_cached_file_id(self, song_id: str, *, bitrate: str | None = None) -> str | None:
        conn = self._require_conn()
        if bitrate:
            query = (
                "SELECT telegram_file_id FROM telegram_audio_cache "
                "WHERE navidrome_song_id = ? AND bitrate = ?"
            )
            params = (song_id, bitrate)
        else:
            query = "SELECT telegram_file_id FROM telegram_audio_cache WHERE navidrome_song_id = ?"
            params = (song_id,)
        async with conn.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return row["telegram_file_id"] if row else None

    async def save_cached_file_id(
        self,
        *,
        song_id: str,
        file_id: str,
        file_unique_id: str | None,
        bitrate: str,
        file_size: int | None,
        duration: int | None,
        title: str,
        artist: str,
        album: str,
        created_at: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO telegram_audio_cache (
                navidrome_song_id, telegram_file_id, telegram_file_unique_id,
                bitrate, file_size, duration, title, artist, album,
                created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(navidrome_song_id, bitrate) DO UPDATE SET
                telegram_file_id = excluded.telegram_file_id,
                telegram_file_unique_id = excluded.telegram_file_unique_id,
                file_size = excluded.file_size,
                duration = excluded.duration,
                title = excluded.title,
                artist = excluded.artist,
                album = excluded.album,
                last_used_at = excluded.last_used_at
            """,
            (
                song_id,
                file_id,
                file_unique_id,
                bitrate,
                file_size,
                duration,
                title,
                artist,
                album,
                created_at,
                created_at,
            ),
        )
        await conn.commit()

    async def touch_cache(self, song_id: str, profile: str, last_used_at: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE telegram_audio_cache SET last_used_at = ? "
            "WHERE navidrome_song_id = ? AND bitrate = ?",
            (last_used_at, song_id, profile),
        )
        await conn.commit()

    async def delete_cached_file_id(self, song_id: str, profile: str | None = None) -> None:
        conn = self._require_conn()
        if profile:
            await conn.execute(
                "DELETE FROM telegram_audio_cache WHERE navidrome_song_id = ? AND bitrate = ?",
                (song_id, profile),
            )
        else:
            await conn.execute(
                "DELETE FROM telegram_audio_cache WHERE navidrome_song_id = ?",
                (song_id,),
            )
        await conn.commit()

    async def save_external_media(
        self,
        *,
        media_key: str,
        url_key: str,
        source: str,
        webpage_url: str,
        title: str,
        artist: str | None,
        duration: int | None,
        thumbnail_url: str | None,
        now: str,
        telegram_file_id: str | None = None,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO external_media (
                media_key, url_key, source, webpage_url, telegram_file_id, title, artist,
                duration, thumbnail_url, created_at, last_used_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                url_key = excluded.url_key,
                source = excluded.source,
                webpage_url = excluded.webpage_url,
                telegram_file_id = excluded.telegram_file_id,
                title = excluded.title,
                artist = excluded.artist,
                duration = excluded.duration,
                thumbnail_url = excluded.thumbnail_url,
                last_used_at = excluded.last_used_at
            """,
            (
                media_key,
                url_key,
                source,
                webpage_url,
                telegram_file_id,
                title,
                artist,
                duration,
                thumbnail_url,
                now,
                now,
            ),
        )
        await conn.commit()

    async def get_external_media(self, media_key: str) -> aiosqlite.Row | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT * FROM external_media WHERE media_key = ?",
            (media_key,),
        ) as cursor:
            return await cursor.fetchone()

    async def touch_external_media(self, media_key: str, now: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "UPDATE external_media SET last_used_at = ? WHERE media_key = ?",
            (now, media_key),
        )
        await conn.commit()

    async def get_external_parts(self, media_key: str) -> list[aiosqlite.Row]:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT * FROM external_audio_cache WHERE media_key = ? ORDER BY part_index",
            (media_key,),
        ) as cursor:
            return list(await cursor.fetchall())

    async def save_external_parts(
        self,
        *,
        media_key: str,
        profile: str,
        parts: list[dict],
        now: str,
    ) -> None:
        """Replace the cached parts for a media key.

        Parts of one delivery only make sense together, so a re-delivery in a
        different profile drops the previous set instead of mixing with it.
        """
        conn = self._require_conn()
        await conn.execute("DELETE FROM external_audio_cache WHERE media_key = ?", (media_key,))
        await conn.executemany(
            """
            INSERT INTO external_audio_cache (
                media_key, profile, part_index, part_count,
                telegram_file_id, file_size, duration, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    media_key,
                    profile,
                    index,
                    len(parts),
                    part["file_id"],
                    part.get("file_size"),
                    part.get("duration"),
                    now,
                )
                for index, part in enumerate(parts)
            ],
        )
        await conn.commit()

    async def delete_external_parts(self, media_key: str) -> None:
        conn = self._require_conn()
        await conn.execute("DELETE FROM external_audio_cache WHERE media_key = ?", (media_key,))
        await conn.commit()

    async def get_user_audio_profile(self, user_id: int) -> str:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT audio_profile FROM users WHERE telegram_user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return DEFAULT_AUDIO_PROFILE
        return normalize_audio_profile(row["audio_profile"])

    async def set_user_audio_profile(self, user_id: int, profile: str, *, now: str) -> None:
        conn = self._require_conn()
        profile = normalize_audio_profile(profile)
        await conn.execute(
            """
            INSERT INTO users (telegram_user_id, audio_profile, role, created_at, last_seen_at)
            VALUES (?, ?, 'friend', ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                audio_profile = excluded.audio_profile,
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, profile, now, now),
        )
        await conn.commit()

    async def touch_user(
        self,
        user_id: int,
        *,
        username: str | None,
        first_name: str | None,
        now: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO users (
                telegram_user_id, username, first_name, role, audio_profile, created_at, last_seen_at
            ) VALUES (?, ?, ?, 'friend', ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, username, first_name, DEFAULT_AUDIO_PROFILE, now, now),
        )
        await conn.commit()

    async def log_event(
        self,
        *,
        user_id: int | None,
        song_id: str | None,
        event_type: str,
        context: str | None,
        created_at: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO events (telegram_user_id, navidrome_song_id, event_type, context, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, song_id, event_type, context, created_at),
        )
        await conn.commit()
