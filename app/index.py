from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

import numpy as np


logger = logging.getLogger(__name__)


class HnswIndex:
    def __init__(self, dim: int, space: str = "cosine"):
        try:
            import hnswlib
        except ImportError as exc:
            raise RuntimeError("hnswlib is required for vector search") from exc
        self._hnswlib = hnswlib
        self.dim = dim
        self.space = space
        self.index = hnswlib.Index(space=space, dim=dim)
        self._query_lock = Lock()

    @classmethod
    def build(
        cls,
        ids: np.ndarray,
        vectors: np.ndarray,
        ef_construction: int = 200,
        m: int = 16,
        ef: int = 50,
    ) -> "HnswIndex":
        if vectors.ndim != 2 or vectors.shape[0] == 0:
            logger.error("Cannot build index without vectors shape=%s", getattr(vectors, "shape", None))
            raise ValueError("Cannot build index without vectors")
        logger.info(
            "Building HNSW index count=%s dim=%s space=cosine ef_construction=%s m=%s ef=%s",
            vectors.shape[0],
            vectors.shape[1],
            ef_construction,
            m,
            ef,
        )
        index = cls(dim=int(vectors.shape[1]))
        index.index.init_index(
            max_elements=int(vectors.shape[0]),
            ef_construction=ef_construction,
            M=m,
        )
        index.index.add_items(vectors.astype(np.float32), ids.astype(np.int64))
        index.index.set_ef(ef)
        return index

    @classmethod
    def load(cls, path: Path, dim: int, ef: int = 50) -> "HnswIndex":
        logger.info("Loading HNSW index path=%s dim=%s ef=%s", path, dim, ef)
        index = cls(dim=dim)
        index.index.load_index(str(path))
        index.index.set_ef(ef)
        return index

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Saving HNSW index path=%s dim=%s space=%s", path, self.dim, self.space)
        self.index.save_index(str(path))

    def count(self) -> int:
        return int(self.index.get_current_count())

    def query(self, vector: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        indexed_count = self.count()
        if indexed_count < 1:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
        k = min(k, indexed_count)
        with self._query_lock:
            self.index.set_ef(max(50, k))
            labels, distances = self.index.knn_query(
                np.asarray(vector, dtype=np.float32).reshape(1, -1),
                k=k,
            )
        return labels[0], distances[0]
