from __future__ import annotations

import json
import logging
import re
from threading import Lock
from pathlib import Path
from time import perf_counter

import numpy as np

from app.config import Settings
from app.embedder import blend_embeddings
from app.index import HnswIndex
from app.store import SimilarTrack, Store, Track


logger = logging.getLogger(__name__)

MAX_HNSW_CANDIDATES = 500
_INDEX_CACHE_LOCK = Lock()
_INDEX_CACHE: dict[tuple[str, Path, int], tuple[int, int, HnswIndex]] = {}


class StaleIndexError(FileNotFoundError):
    pass


class Recommender:
    def __init__(self, store: Store, settings: Settings, model_name: str):
        self.store = store
        self.settings = settings
        self.model_name = model_name

    def similar(
        self,
        seed: Track,
        k: int = 30,
        max_per_artist: int = 2,
        exclude_same_album: bool = True,
        count_collaboration_artists: bool = True,
        candidate_multiplier: int = 5,
    ) -> list[SimilarTrack]:
        started = perf_counter()
        logger.info(
            "Finding similar tracks seed_id=%s model=%s k=%s max_per_artist=%s exclude_same_album=%s",
            seed.id,
            self.model_name,
            k,
            max_per_artist,
            exclude_same_album,
        )
        seed_vector = self.store.load_embedding(seed.id, self.model_name)
        if seed_vector is None:
            logger.warning("Missing seed embedding seed_id=%s model=%s", seed.id, self.model_name)
            raise LookupError(f"No embedding for track {seed.id} and model {self.model_name}")

        results = self.similar_vector(
            seed_vector,
            exclude_track_ids={seed.id},
            album_seeds=[seed],
            k=k,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
            count_collaboration_artists=count_collaboration_artists,
            candidate_multiplier=candidate_multiplier,
        )
        logger.info(
            "Finished similar query seed_id=%s model=%s results=%s seconds=%.3f",
            seed.id,
            self.model_name,
            len(results),
            perf_counter() - started,
        )
        return results

    def similar_mix(
        self,
        seed_tracks: list[Track],
        k: int = 30,
        max_per_artist: int = 2,
        exclude_same_album: bool = True,
        count_collaboration_artists: bool = True,
        candidate_multiplier: int = 5,
    ) -> tuple[list[SimilarTrack], list[int]]:
        started = perf_counter()
        seed_ids = [track.id for track in seed_tracks]
        logger.info(
            "Finding similar mix seed_ids=%s model=%s k=%s max_per_artist=%s exclude_same_album=%s",
            seed_ids,
            self.model_name,
            k,
            max_per_artist,
            exclude_same_album,
        )
        vectors: list[np.ndarray] = []
        seeds_with_embeddings: list[Track] = []
        skipped_seed_ids: list[int] = []
        for track in seed_tracks:
            vector = self.store.load_embedding(track.id, self.model_name)
            if vector is None:
                skipped_seed_ids.append(track.id)
                continue
            vectors.append(vector)
            seeds_with_embeddings.append(track)
        if not vectors:
            logger.warning(
                "Missing embeddings for all mix seeds seed_ids=%s model=%s skipped=%s",
                seed_ids,
                self.model_name,
                skipped_seed_ids,
            )
            raise LookupError(
                f"No embeddings for mix seeds and model {self.model_name}: {skipped_seed_ids}"
            )

        seed_vector = blend_embeddings(vectors)
        results = self.similar_vector(
            seed_vector,
            exclude_track_ids=set(seed_ids),
            album_seeds=seeds_with_embeddings,
            k=k,
            max_per_artist=max_per_artist,
            exclude_same_album=exclude_same_album,
            count_collaboration_artists=count_collaboration_artists,
            candidate_multiplier=candidate_multiplier,
        )
        logger.info(
            "Finished similar mix seed_ids=%s model=%s skipped=%s results=%s seconds=%.3f",
            seed_ids,
            self.model_name,
            skipped_seed_ids,
            len(results),
            perf_counter() - started,
        )
        return results, skipped_seed_ids

    def similar_vector(
        self,
        seed_vector: np.ndarray,
        exclude_track_ids: set[int] | list[int],
        album_seeds: list[Track],
        k: int = 30,
        max_per_artist: int = 2,
        exclude_same_album: bool = True,
        count_collaboration_artists: bool = True,
        candidate_multiplier: int = 5,
    ) -> list[SimilarTrack]:
        exclude_ids = set(exclude_track_ids)
        index_path = self.settings.index_path(self.model_name)
        if not index_path.exists():
            logger.warning("Index not found model=%s path=%s", self.model_name, index_path)
            raise FileNotFoundError(f"Index not found: {index_path}")

        load_started = perf_counter()
        index = _load_cached_index(index_path, dim=int(seed_vector.shape[0]), model_name=self.model_name)
        load_seconds = perf_counter() - load_started
        indexed_count = self.store.count_embeddings(self.model_name)
        index_count = index.count()
        if index_count != indexed_count:
            logger.warning(
                "Index is stale model=%s path=%s index_count=%s db_embeddings=%s",
                self.model_name,
                index_path,
                index_count,
                indexed_count,
            )
            raise StaleIndexError(
                f"Index is stale for {self.model_name}: index has {index_count} vectors "
                f"but database has {indexed_count} embeddings. Rebuild the index."
            )
        requested_raw_k = max(k * candidate_multiplier, k + len(exclude_ids))
        raw_k = min(requested_raw_k, indexed_count, max(k, MAX_HNSW_CANDIDATES))
        if raw_k < 1:
            logger.info(
                "Similar vector query has no indexed tracks model=%s indexed_count=%s",
                self.model_name,
                indexed_count,
            )
            return []
        logger.info(
            "Similar vector prepared model=%s k=%s raw_k=%s requested_raw_k=%s indexed_count=%s "
            "candidate_multiplier=%s index_load_seconds=%.3f",
            self.model_name,
            k,
            raw_k,
            requested_raw_k,
            indexed_count,
            candidate_multiplier,
            load_seconds,
        )
        query_started = perf_counter()
        labels, distances = index.query(seed_vector, raw_k)
        query_seconds = perf_counter() - query_started

        filter_started = perf_counter()
        track_by_id = self.store.get_tracks([int(label) for label in labels])
        track_load_seconds = perf_counter() - filter_started
        results: list[SimilarTrack] = []
        seen_track_keys: set[tuple[str, str]] = set()
        per_artist: dict[str, int] = {}
        skipped_excluded = 0
        skipped_missing = 0
        skipped_short = 0
        skipped_duplicate_track = 0
        skipped_duplicate_result = 0
        skipped_album = 0
        skipped_artist_cap = 0
        for label, distance in zip(labels, distances, strict=False):
            track_id = int(label)
            if track_id in exclude_ids:
                skipped_excluded += 1
                continue
            track = track_by_id.get(track_id)
            if track is None:
                skipped_missing += 1
                continue
            if _is_too_short(track):
                skipped_short += 1
                continue
            if _same_track_any(album_seeds, track):
                skipped_duplicate_track += 1
                continue
            if exclude_same_album and _same_album_any(album_seeds, track):
                skipped_album += 1
                continue
            track_key = _track_metadata_key(track)
            if track_key is not None and track_key in seen_track_keys:
                skipped_duplicate_result += 1
                continue
            artist_keys = _artist_credit_keys(
                track.artist,
                count_collaboration_artists=count_collaboration_artists,
            )
            if artist_keys:
                if any(per_artist.get(artist_key, 0) >= max_per_artist for artist_key in artist_keys):
                    skipped_artist_cap += 1
                    continue
                for artist_key in artist_keys:
                    per_artist[artist_key] = per_artist.get(artist_key, 0) + 1
            if track_key is not None:
                seen_track_keys.add(track_key)
            distance_float = float(distance)
            results.append(
                SimilarTrack(
                    track=track,
                    distance=distance_float,
                    similarity=1.0 - distance_float,
                )
            )
            if len(results) >= k:
                break
        logger.info(
            "Similar vector finished model=%s results=%s labels=%s hnsw_seconds=%.3f "
            "track_load_seconds=%.3f filter_seconds=%.3f skipped_excluded=%s skipped_missing=%s "
            "skipped_short=%s skipped_duplicate_track=%s skipped_duplicate_result=%s "
            "skipped_album=%s skipped_artist_cap=%s",
            self.model_name,
            len(results),
            len(labels),
            query_seconds,
            track_load_seconds,
            perf_counter() - filter_started,
            skipped_excluded,
            skipped_missing,
            skipped_short,
            skipped_duplicate_track,
            skipped_duplicate_result,
            skipped_album,
            skipped_artist_cap,
        )
        return results


def build_index(store: Store, settings: Settings, model_name: str) -> Path:
    started = perf_counter()
    ids, vectors = store.load_embeddings(model_name)
    if len(ids) == 0:
        logger.warning("Cannot build index without embeddings model=%s", model_name)
        raise ValueError(f"No embeddings found for model {model_name}")
    logger.info("Building recommender index model=%s vectors=%s dim=%s", model_name, len(ids), vectors.shape[1])
    index = HnswIndex.build(ids, vectors)
    path = settings.index_path(model_name)
    index.save(path)
    index_metadata_path(path).write_text(
        json.dumps(
            {
                "model_name": model_name,
                "count": int(len(ids)),
                "dim": int(vectors.shape[1]),
                "space": index.space,
                "path": str(path),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    logger.info(
        "Finished recommender index model=%s vectors=%s path=%s seconds=%.3f",
        model_name,
        len(ids),
        path,
        perf_counter() - started,
    )
    return path


def index_metadata_path(index_path: Path) -> Path:
    return index_path.with_suffix(f"{index_path.suffix}.json")


def _load_cached_index(index_path: Path, dim: int, model_name: str) -> HnswIndex:
    stat = index_path.stat()
    cache_key = (model_name, index_path.resolve(), dim)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        cached_mtime_ns, cached_size, cached_index = cached
        if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
            logger.info(
                "Using cached HNSW index model=%s path=%s dim=%s size=%s",
                model_name,
                index_path,
                dim,
                stat.st_size,
            )
            return cached_index

    with _INDEX_CACHE_LOCK:
        cached = _INDEX_CACHE.get(cache_key)
        if cached is not None:
            cached_mtime_ns, cached_size, cached_index = cached
            if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size:
                logger.info(
                    "Using cached HNSW index model=%s path=%s dim=%s size=%s",
                    model_name,
                    index_path,
                    dim,
                    stat.st_size,
                )
                return cached_index
        started = perf_counter()
        index = HnswIndex.load(index_path, dim=dim)
        logger.info(
            "Loaded HNSW index model=%s path=%s dim=%s size=%s seconds=%.3f",
            model_name,
            index_path,
            dim,
            stat.st_size,
            perf_counter() - started,
        )
        _INDEX_CACHE[cache_key] = (stat.st_mtime_ns, stat.st_size, index)
        return index


def _same_album(seed: Track, result: Track) -> bool:
    if not seed.album or not result.album:
        return False
    if seed.artist and result.artist and seed.artist.strip().lower() != result.artist.strip().lower():
        return False
    return seed.album.strip().lower() == result.album.strip().lower()


def _same_album_any(seeds: list[Track], result: Track) -> bool:
    return any(_same_album(seed, result) for seed in seeds)


def _same_track(seed: Track, result: Track) -> bool:
    return _track_metadata_key(seed) is not None and _track_metadata_key(seed) == _track_metadata_key(result)


def _same_track_any(seeds: list[Track], result: Track) -> bool:
    return any(_same_track(seed, result) for seed in seeds)


def _track_metadata_key(track: Track) -> tuple[str, str] | None:
    artist = _metadata_key(track.artist)
    title = _metadata_key(track.title)
    if artist is None or title is None:
        return None
    return artist, title


# No wrapping `\s*` around the alternation: an unbounded quantifier that
# frequently fails to find its delimiter forces re.split to rescan the
# remaining string from every position, which is what made the old
# `\s+...\s+` pattern polynomial on whitespace-heavy input (S5852).
# Delimiters that require surrounding whitespace use lookaround instead; the
# caller strips the leftover boundary spaces from each part.
_ARTIST_KEY_ALWAYS_DELIMITER_RE = r"[;&+]"
_ARTIST_KEY_SPACED_DELIMITER_RE = (
    r"(?<=\s)(?:\u2022|feat\.?|ft\.?|featuring|with|vs\.?|x)(?=\s)"
)
_ARTIST_KEY_SPLIT_RE = re.compile(
    "|".join((_ARTIST_KEY_SPACED_DELIMITER_RE, _ARTIST_KEY_ALWAYS_DELIMITER_RE)),
    re.IGNORECASE,
)


def _artist_credit_keys(
    artist: str | None,
    *,
    count_collaboration_artists: bool = True,
) -> tuple[str, ...]:
    key = _metadata_key(artist)
    if key is None:
        return ()
    if not count_collaboration_artists:
        return (key,)
    parts = (part.strip() for part in _ARTIST_KEY_SPLIT_RE.split(key))
    keys = tuple(dict.fromkeys(part for part in parts if part))
    return keys or (key,)


def _metadata_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None


def _is_too_short(track: Track) -> bool:
    return track.duration is not None and track.duration < 60.0
