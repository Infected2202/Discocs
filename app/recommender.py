from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

import numpy as np

from app.config import Settings
from app.embedder import blend_embeddings
from app.index import HnswIndex
from app.store import SimilarTrack, Store, Track


logger = logging.getLogger(__name__)


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
        candidate_multiplier: int = 5,
    ) -> list[SimilarTrack]:
        exclude_ids = set(exclude_track_ids)
        index_path = self.settings.index_path(self.model_name)
        if not index_path.exists():
            logger.warning("Index not found model=%s path=%s", self.model_name, index_path)
            raise FileNotFoundError(f"Index not found: {index_path}")

        index = HnswIndex.load(index_path, dim=int(seed_vector.shape[0]))
        indexed_count = self.store.count_embeddings(self.model_name)
        raw_k = min(max(k * candidate_multiplier, k + len(exclude_ids)), indexed_count)
        if raw_k < 1:
            logger.info(
                "Similar vector query has no indexed tracks model=%s indexed_count=%s",
                self.model_name,
                indexed_count,
            )
            return []
        labels, distances = index.query(seed_vector, raw_k)

        results: list[SimilarTrack] = []
        per_artist: dict[str, int] = {}
        for label, distance in zip(labels, distances, strict=False):
            track_id = int(label)
            if track_id in exclude_ids:
                continue
            track = self.store.get_track(track_id)
            if track is None:
                continue
            if _is_too_short(track):
                continue
            if exclude_same_album and _same_album_any(album_seeds, track):
                continue
            artist_key = (track.artist or "").strip().lower()
            if artist_key:
                count = per_artist.get(artist_key, 0)
                if count >= max_per_artist:
                    continue
                per_artist[artist_key] = count + 1
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
    logger.info(
        "Finished recommender index model=%s vectors=%s path=%s seconds=%.3f",
        model_name,
        len(ids),
        path,
        perf_counter() - started,
    )
    return path


def _same_album(seed: Track, result: Track) -> bool:
    if not seed.album or not result.album:
        return False
    if seed.artist and result.artist and seed.artist.strip().lower() != result.artist.strip().lower():
        return False
    return seed.album.strip().lower() == result.album.strip().lower()


def _same_album_any(seeds: list[Track], result: Track) -> bool:
    return any(_same_album(seed, result) for seed in seeds)


def _is_too_short(track: Track) -> bool:
    return track.duration is not None and track.duration < 60.0
