"""Metrics/feature-search API routes.

Extracted from app/main.py — Stage 6b.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import context
from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.models import FeatureFilter
from app.schemas.requests import FeatureSearchRequest
from app.serializers.tracks import enriched_feature_track_dict

router = APIRouter()


@router.get("/metrics/features")
def metrics_features(
    source: str = Query(default="audio_features", pattern="^(audio_features|heads)$"),
    extractor: str = AUDIO_FEATURE_EXTRACTOR,
):
    store, _settings = context()
    if source == "heads":
        summaries = store.list_head_summaries()
        return {
            "source": source,
            "features": [
                {
                    "name": item.model_name,
                    "extractor": "discogs_effnet_heads",
                    "value_count": item.prediction_track_count,
                    "text_count": item.label_count,
                    "track_count": item.output_count,
                    "min_value": None,
                    "max_value": item.max_score,
                    "avg_value": item.avg_score,
                    "unit": "score",
                }
                for item in summaries
            ],
        }
    summaries = store.list_feature_summaries(extractor or None)
    return {
        "source": source,
        "extractor": extractor,
        "features": [
            {
                "name": item.name,
                "extractor": item.extractor,
                "value_count": item.value_count,
                "text_count": item.text_count,
                "track_count": item.track_count,
                "min_value": item.min_value,
                "max_value": item.max_value,
                "avg_value": item.avg_value,
                "unit": item.unit,
            }
            for item in summaries
        ],
    }


@router.get("/metrics/features/{feature_name}/values")
def metrics_feature_values(
    feature_name: str,
    source: str = Query(default="audio_features", pattern="^(audio_features|heads)$"),
    extractor: str = AUDIO_FEATURE_EXTRACTOR,
    limit: int = Query(default=100, ge=1, le=500),
):
    store, _settings = context()
    if source == "heads":
        values = store.list_head_prediction_labels(feature_name, limit)
        return {
            "feature": feature_name,
            "source": source,
            "values": [
                {
                    "value": label,
                    "track_count": track_count,
                    "avg_score": avg_score,
                    "max_score": max_score,
                }
                for label, track_count, avg_score, max_score in values
            ],
        }
    values = store.list_feature_text_values(feature_name, extractor or None, limit)
    return {
        "feature": feature_name,
        "source": source,
        "extractor": extractor,
        "values": [
            {"value": value, "track_count": track_count}
            for value, track_count in values
        ],
    }


@router.post("/metrics/search")
def metrics_search(request: FeatureSearchRequest):
    store, _settings = context()
    filters = [
        FeatureFilter(
            name=item.name,
            min_value=item.min_value,
            max_value=item.max_value,
            text_values=tuple(value for value in item.text_values if value),
        )
        for item in request.filters
        if item.name.strip()
    ]
    if request.source == "heads":
        results = store.search_tracks_by_head_predictions(
            filters,
            query=request.query,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
            limit=request.limit,
        )
    else:
        results = store.search_tracks_by_features(
            filters,
            query=request.query,
            extractor=request.extractor or None,
            sort_by=request.sort_by,
            sort_direction=request.sort_direction,
            limit=request.limit,
        )
    return {
        "source": request.source,
        "extractor": request.extractor,
        "count": len(results),
        "results": [enriched_feature_track_dict(store, item) for item in results],
    }
