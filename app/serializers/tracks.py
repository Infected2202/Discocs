"""Track enrichment serializers.

These build "fat" track dicts that include analysis metadata, features,
predictions, and navidrome metadata. Extracted from app/main.py.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.audio_features import AUDIO_FEATURE_EXTRACTOR
from app.models import FeatureTrack, Track, TrackPrediction
from app.store import Store, similar_track_dict, track_dict, track_listing_dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Primitive builders
# ---------------------------------------------------------------------------

def prediction_dict(prediction: object) -> dict[str, object]:
    return {
        "label": prediction.label,  # type: ignore[attr-defined]
        "score": prediction.score,  # type: ignore[attr-defined]
        "rank": prediction.rank,  # type: ignore[attr-defined]
    }


def feature_dict(feature: object) -> dict[str, object]:
    return {
        "name": feature.name,  # type: ignore[attr-defined]
        "value": feature.value,  # type: ignore[attr-defined]
        "text_value": feature.text_value,  # type: ignore[attr-defined]
        "unit": feature.unit,  # type: ignore[attr-defined]
        "confidence": feature.confidence,  # type: ignore[attr-defined]
        "extractor": feature.extractor,  # type: ignore[attr-defined]
    }


def feature_track_dict(item: FeatureTrack) -> dict[str, object]:
    data = track_dict(item.track)
    data["features"] = [feature_dict(feature) for feature in item.features]
    return data


def enriched_feature_track_dict(store: Store, item: FeatureTrack) -> dict[str, object]:
    data = feature_track_dict(item)
    data.update(track_card_metadata(store, item.track))
    return data


# ---------------------------------------------------------------------------
# Navidrome / format helpers
# ---------------------------------------------------------------------------

def navidrome_raw_metadata(store: Store, track: Track) -> dict[str, object]:
    external_id = store.external_id_for_track("navidrome", track.id)
    if external_id is None:
        return {}
    mapping = store.get_external_track("navidrome", external_id)
    if mapping is None or not mapping.raw_json:
        return {}
    try:
        parsed = json.loads(mapping.raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def audio_format(track: Track, raw: dict[str, object]) -> str | None:
    suffix = raw.get("suffix") or raw.get("format")
    if suffix:
        return str(suffix).strip(". ").upper() or None
    content_type = raw.get("contentType") or raw.get("content_type")
    if content_type:
        clean = str(content_type).split("/")[-1].strip()
        return clean.upper() if clean else None
    path_suffix = Path(track.path).suffix
    return path_suffix.strip(".").upper() if path_suffix else None


def audio_bitrate(track: Track, raw: dict[str, object]) -> int | None:
    for key in ("bitRate", "bitrate", "bit_rate"):
        value = raw.get(key)
        if value is None:
            continue
        try:
            bitrate = int(float(value))
        except (TypeError, ValueError):
            continue
        if bitrate > 0:
            return bitrate
    if track.duration and track.file_size > 0:
        return max(1, round((track.file_size * 8) / (float(track.duration) * 1000)))
    return None


def first_prediction_dict(
    predictions: list[TrackPrediction],
    preferred_label: str,
) -> dict[str, object] | None:
    preferred = preferred_label.strip().lower()
    for prediction in predictions:
        if prediction.label.strip().lower() == preferred:
            return prediction_dict(prediction)
    return None


# ---------------------------------------------------------------------------
# Card metadata (combined audio analysis, features, predictions)
# ---------------------------------------------------------------------------

def track_card_metadata(store: Store, track: Track) -> dict[str, object]:
    features = store.load_features(track.id, AUDIO_FEATURE_EXTRACTOR)
    feature_by_name = {feature.name: feature for feature in features}
    navidrome_item_id = store.external_id_for_track("navidrome", track.id)
    raw = navidrome_raw_metadata(store, track)
    return {
        "navidrome_item_id": navidrome_item_id,
        "card_features": {
            name: feature_dict(feature)
            for name, feature in feature_by_name.items()
            if name in {"bpm", "key", "scale"}
        },
        "genre_discogs400": [
            prediction_dict(prediction)
            for prediction in store.load_predictions(track.id, "genre_discogs400", limit=3)
        ],
        "approachability_3c": first_prediction_dict(
            store.load_predictions(track.id, "approachability_3c", limit=3),
            "approachable",
        ),
        "engagement_3c": first_prediction_dict(
            store.load_predictions(track.id, "engagement_3c", limit=3),
            "engaging",
        ),
        "audio_format": audio_format(track, raw),
        "bitrate": audio_bitrate(track, raw),
    }


# ---------------------------------------------------------------------------
# Enriched track builders
# ---------------------------------------------------------------------------

def enriched_track_dict(store: Store, track: Track) -> dict[str, object]:
    data = track_dict(track)
    data.update(track_card_metadata(store, track))
    return data


def enriched_track_listing_dict(store: Store, listing: object) -> dict[str, object]:
    data = track_listing_dict(listing)
    data["navidrome_item_id"] = store.external_id_for_track("navidrome", listing.track.id)  # type: ignore[attr-defined]
    return data


def enriched_similar_track_dict(store: Store, result: object) -> dict[str, object]:
    data = similar_track_dict(result)
    data.update(track_card_metadata(store, result.track))  # type: ignore[attr-defined]
    return data
