"""Collection-map / embedding-atlas 2D projections.

Reduces high-dimensional track embeddings to 2D coordinates for the atlas
diagnostic view and persists them. This is a viewing surface only — real
similarity, nearest neighbors, generated mixes and recommendations keep using
the original embeddings / HNSW / cosine path, never these 2D coordinates.

Heavy projection libraries (umap-learn, scikit-learn) are imported lazily
inside `default_projector_factory` so importing this module (and running unit
tests with an injected fake projector) never requires them.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol

import numpy as np

from app.models import MapProjection, utc_now

logger = logging.getLogger(__name__)

# Bump when the persisted coordinate format or profile semantics change.
PROJECTION_VERSION = 1


class SupportsFitTransform(Protocol):
    def fit_transform(self, vectors: np.ndarray) -> np.ndarray:  # pragma: no cover
        ...


# Projection profiles. `params` are method-specific; `metric` is the
# embedding-space distance used for the reduction.
PROJECTION_PROFILES: dict[str, dict[str, Any]] = {
    # Detail-oriented: tight neighborhoods, local structure.
    "umap_local": {
        "method": "umap",
        "metric": "cosine",
        "params": {"n_neighbors": 15, "min_dist": 0.1, "random_state": 42},
    },
    # Overview-oriented: broader neighborhoods, global layout.
    "umap_global": {
        "method": "umap",
        "metric": "cosine",
        "params": {"n_neighbors": 50, "min_dist": 0.5, "random_state": 42},
    },
    # Linear baseline / debug.
    "pca": {
        "method": "pca",
        "metric": "euclidean",
        "params": {"random_state": 42},
    },
}
DEFAULT_PROFILE = "umap_local"

# (method, params_including_metric, embedding_dim) -> reducer
ProjectorFactory = Callable[[str, dict[str, Any], int], SupportsFitTransform]


def default_projector_factory(
    method: str,
    params: dict[str, Any],
    dim: int,
) -> SupportsFitTransform:
    """Build a real reducer, importing the heavy dep lazily."""
    if method == "umap":
        import umap  # noqa: PLC0415 — lazy heavy dep

        return umap.UMAP(
            n_components=2,
            n_neighbors=int(params.get("n_neighbors", 15)),
            min_dist=float(params.get("min_dist", 0.1)),
            metric=str(params.get("metric", "cosine")),
            random_state=params.get("random_state", 42),
        )
    if method == "pca":
        from sklearn.decomposition import PCA  # noqa: PLC0415 — lazy heavy dep

        return PCA(n_components=2, random_state=params.get("random_state", 42))
    raise ValueError(f"Unknown projection method: {method}")


def build_projection(
    store,
    *,
    model_name: str,
    profile: str = DEFAULT_PROFILE,
    force: bool = False,
    projector_factory: ProjectorFactory | None = None,
    progress: Callable[[str], None] | None = None,
) -> MapProjection:
    """Build (or rebuild) a 2D projection for a model and persist it.

    Returns the resulting MapProjection. On a ready projection without `force`,
    returns the existing one without recomputing. Raises on empty source or a
    projector error (after marking the projection `failed`).
    """
    if profile not in PROJECTION_PROFILES:
        raise ValueError(f"Unknown projection profile: {profile}")
    spec = PROJECTION_PROFILES[profile]
    method = str(spec["method"])
    metric = str(spec["metric"])
    params = dict(spec["params"])
    factory = projector_factory or default_projector_factory

    def _emit(message: str) -> None:
        logger.info("Projection model=%s profile=%s: %s", model_name, profile, message)
        if progress is not None:
            progress(message)

    existing = store.find_map_projection(model_name, profile)
    if existing is not None and existing.status == "ready" and not force:
        _emit(f"already ready (id={existing.id}); pass force to rebuild")
        return existing

    source_count = int(store.count_embeddings(model_name))
    if existing is None:
        projection = store.create_map_projection(
            model_name=model_name,
            name=profile,
            method=method,
            metric=metric,
            params_json=json.dumps(params, sort_keys=True),
            source_embedding_count=source_count,
            version=PROJECTION_VERSION,
            status="running",
        )
    else:
        projection = store.update_map_projection(
            existing.id, status="running", source_embedding_count=source_count
        )
        # Drop stale coordinates from a previous build before recomputing.
        store.replace_map_projection_points(
            existing.id, [], np.empty((0, 2), dtype=np.float32)
        )

    started = perf_counter()
    try:
        ids, vectors = store.load_projection_source(model_name)
        projected = int(len(ids))
        skipped = max(source_count - projected, 0)
        if projected == 0:
            store.update_map_projection(
                projection.id,
                status="failed",
                projected_count=0,
                skipped_count=skipped,
                embedding_dim=0,
                diagnostics_json=json.dumps({"error": "no embeddings to project"}),
                completed_at=utc_now(),
            )
            raise ValueError(f"No embeddings to project for model {model_name}")

        dim = int(vectors.shape[1])
        _emit(f"projecting {projected} embeddings (dim={dim}) via {method}")
        reducer = factory(method, {**params, "metric": metric}, dim)
        coords = np.asarray(reducer.fit_transform(vectors), dtype=np.float32)
        if coords.ndim != 2 or coords.shape[1] != 2 or coords.shape[0] != projected:
            raise ValueError(
                f"Projector returned shape {coords.shape}, expected ({projected}, 2)"
            )

        store.replace_map_projection_points(projection.id, ids, coords)
        runtime = perf_counter() - started
        diagnostics = {
            "runtime_seconds": round(runtime, 3),
            "profile": profile,
            "method": method,
            "metric": metric,
            "params": params,
            "projected_count": projected,
            "skipped_count": skipped,
            "embedding_dim": dim,
        }
        result = store.update_map_projection(
            projection.id,
            status="ready",
            projected_count=projected,
            skipped_count=skipped,
            embedding_dim=dim,
            diagnostics_json=json.dumps(diagnostics, sort_keys=True),
            completed_at=utc_now(),
        )
        _emit(f"done in {runtime:.2f}s (points={projected}, skipped={skipped})")
        return result  # type: ignore[return-value]
    except Exception as exc:
        current = store.get_map_projection(projection.id)
        if current is not None and current.status != "failed":
            store.update_map_projection(
                projection.id,
                status="failed",
                diagnostics_json=json.dumps({"error": str(exc)}),
                completed_at=utc_now(),
            )
        logger.exception("Projection failed model=%s profile=%s", model_name, profile)
        raise
