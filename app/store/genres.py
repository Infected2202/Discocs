"""Store Genres domain: жанры релиза (кэш в ``release_genres``), лейбла и артиста.

Сама методика — в ``app/genres.py``; здесь только данные и кэш.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

from app.genres import GENRE_MODEL, entity_genres
from app.genres import release_genres as compute_release_genres
from app.models import utc_now

_CHUNK = 500

# ``+p.model_name``: без унарного плюса SQLite берёт индекс по модели и перебирает все
# ~1.5 млн предсказаний вместо поиска по треку (проверено на живой базе: 0.22 с → 0.02 с).
_SIGNATURES_SQL = """
    SELECT rt.release_id, COUNT(p.track_id) AS analyzed, MAX(p.created_at) AS last_at
    FROM release_tracks rt
    JOIN tracks t ON t.id = rt.track_id AND t.missing_at IS NULL
    LEFT JOIN track_predictions p
      ON p.track_id = rt.track_id AND +p.model_name = ? AND p.rank = 1
    WHERE rt.release_id IN ({ids})
    GROUP BY rt.release_id
"""

_SCORES_SQL = """
    SELECT rt.release_id, p.track_id, p.label, p.score
    FROM release_tracks rt
    JOIN tracks t ON t.id = rt.track_id AND t.missing_at IS NULL
    JOIN track_predictions p ON p.track_id = rt.track_id AND +p.model_name = ?
    WHERE rt.release_id IN ({ids})
"""


def _chunks(ids: list[int]) -> Iterable[list[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start:start + _CHUNK]


def _placeholders(ids: list[int]) -> str:
    return ", ".join("?" for _ in ids)


class GenresStoreMixin:
    def release_genres(self, release_ids: Iterable[int]) -> dict[int, list[tuple[str, float]]]:
        """Жанры релизов; устаревшие (сменились треки или предсказания) пересчитываются."""
        ids = list(dict.fromkeys(int(rid) for rid in release_ids))
        result: dict[int, list[tuple[str, float]]] = {}
        with self.connect() as conn:  # type: ignore[attr-defined]
            for chunk in _chunks(ids):
                result.update(self._fresh_release_genres(conn, chunk))
        return result

    def refresh_release_genres(self) -> int:
        """Пересчитать устаревшие жанры всех релизов (после синхронизации и анализа)."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            ids = [int(row[0]) for row in conn.execute("SELECT id FROM releases")]
            stale = 0
            for chunk in _chunks(ids):
                stale += len(self._fresh_release_genres(conn, chunk, only_stale=True))
        return stale

    def label_genres(self, label_id: int) -> list[tuple[str, int]]:
        with self.connect() as conn:  # type: ignore[attr-defined]
            ids = [int(row[0]) for row in conn.execute(
                """
                SELECT DISTINCT rl.release_id FROM release_labels rl
                WHERE rl.label_id = ? AND EXISTS (
                    SELECT 1 FROM release_tracks rt JOIN tracks t ON t.id = rt.track_id
                    WHERE rt.release_id = rl.release_id AND t.missing_at IS NULL)
                """,
                (label_id,),
            )]
        genres = self.release_genres(ids)
        return entity_genres([style for style, _score in genres.get(rid, [])] for rid in ids)

    def artist_genres(self, artist_id: int) -> list[tuple[str, int]]:
        """По релизам, где артист основной (не приглашённый и не сборники)."""
        with self.connect() as conn:  # type: ignore[attr-defined]
            ids = [int(row[0]) for row in conn.execute(
                """
                SELECT DISTINCT ra.release_id FROM release_artists ra
                WHERE ra.artist_id = ? AND EXISTS (
                    SELECT 1 FROM release_tracks rt JOIN tracks t ON t.id = rt.track_id
                    WHERE rt.release_id = ra.release_id AND t.missing_at IS NULL)
                """,
                (artist_id,),
            )]
        genres = self.release_genres(ids)
        return entity_genres([style for style, _score in genres.get(rid, [])] for rid in ids)

    def top_label_genres(self, label_ids: Iterable[int], limit: int = 2) -> dict[int, list[str]]:
        """Главные стили лейблов для карточек — только из кэша, без пересчёта (полки дашборда)."""
        ids = list(dict.fromkeys(int(lid) for lid in label_ids))
        per_label: dict[int, list[list[str]]] = defaultdict(list)
        with self.connect() as conn:  # type: ignore[attr-defined]
            for chunk in _chunks(ids):
                for row in conn.execute(
                    f"""
                    SELECT rl.label_id, rg.genres_json FROM release_labels rl
                    JOIN release_genres rg ON rg.release_id = rl.release_id
                    WHERE rl.label_id IN ({_placeholders(chunk)})
                    """,
                    chunk,
                ):
                    per_label[int(row[0])].append([style for style, _score in json.loads(row[1])])
        return {lid: [style for style, _n in entity_genres(lists)][:limit] for lid, lists in per_label.items()}

    def _fresh_release_genres(
        self, conn, release_ids: list[int], *, only_stale: bool = False,
    ) -> dict[int, list[tuple[str, float]]]:
        if not release_ids:
            return {}
        marks = _placeholders(release_ids)
        signatures = {rid: "0:" for rid in release_ids}
        for row in conn.execute(_SIGNATURES_SQL.format(ids=marks), (GENRE_MODEL, *release_ids)):
            signatures[int(row[0])] = f"{int(row[1])}:{row[2] or ''}"
        cached: dict[int, tuple[str, list[tuple[str, float]]]] = {}
        for row in conn.execute(
            f"SELECT release_id, signature, genres_json FROM release_genres WHERE release_id IN ({marks})",
            release_ids,
        ):
            cached[int(row[0])] = (str(row[1]), [(str(s), float(v)) for s, v in json.loads(row[2])])
        stale = [rid for rid in release_ids if cached.get(rid, ("",))[0] != signatures[rid]]
        computed = self._compute_release_genres(conn, stale)
        now = utc_now()
        conn.executemany(
            """
            INSERT INTO release_genres (release_id, genres_json, signature, computed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(release_id) DO UPDATE SET
                genres_json = excluded.genres_json,
                signature = excluded.signature,
                computed_at = excluded.computed_at
            """,
            [(rid, json.dumps(computed[rid]), signatures[rid], now) for rid in stale],
        )
        # Пачками: полный пересчёт идёт десятки секунд и не должен всё это время держать запись.
        conn.commit()
        if only_stale:
            return computed
        return {rid: computed[rid] if rid in computed else cached[rid][1] for rid in release_ids}

    def _compute_release_genres(self, conn, release_ids: list[int]) -> dict[int, list[tuple[str, float]]]:
        tracks: dict[int, dict[int, dict[str, float]]] = {rid: defaultdict(dict) for rid in release_ids}
        for chunk in _chunks(release_ids):
            for row in conn.execute(_SCORES_SQL.format(ids=_placeholders(chunk)), (GENRE_MODEL, *chunk)):
                tracks[int(row[0])][int(row[1])][str(row[2])] = float(row[3])
        return {rid: compute_release_genres(list(by_track.values())) for rid, by_track in tracks.items()}
