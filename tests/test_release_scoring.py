import numpy as np

import app.services.release_scoring as release_scoring
from app.models import ReleaseAggregate, UserReleasePreference, utc_now
from app.services.release_scoring import (
    ReleaseRecommendationSettings,
    _days_since,
    rank_releases_for_user,
    score_release,
)


def _aggregate(
    release_id: int = 1,
    *,
    available_track_count: int = 2,
    duration: float | None = 1800.0,
    medoid_track_id: int | None = None,
) -> ReleaseAggregate:
    return ReleaseAggregate(
        release_id=release_id,
        track_count=available_track_count,
        available_track_count=available_track_count,
        duration=duration,
        centroid_model="discogs_multi",
        medoid_track_id=medoid_track_id,
        embedding_status="ready",
        top_region_matches_json="{}",
        audio_summary_json="{}",
        preference_summary_json="{}",
        updated_at=utc_now(),
    )


def _release_pref(
    release_id: int = 1,
    *,
    liked: bool = False,
    last_played_at: str | None = None,
    score: float = 0.0,
) -> UserReleasePreference:
    return UserReleasePreference(
        release_id=release_id,
        liked=liked,
        play_count=1 if last_played_at else 0,
        completion_count=0,
        skip_count=0,
        last_played_at=last_played_at,
        last_completed_at=None,
        score=score,
        updated_at=utc_now(),
    )


def test_days_since_returns_recent_age_for_valid_utc_timestamp() -> None:
    days = _days_since(utc_now(), "unused")

    assert days is not None
    assert 0.0 <= days < 2.0


def test_days_since_returns_none_for_invalid_timestamp() -> None:
    assert _days_since("not-a-timestamp", "unused") is None


def test_score_release_uses_taste_match_and_freshness_for_unplayed_release() -> None:
    settings = ReleaseRecommendationSettings()

    result = score_release(
        agg=_aggregate(available_track_count=1),
        release_pref=None,
        track_pref_rows=[],
        centroid=np.array([1.0, 0.0], dtype=np.float32),
        taste_centroid=np.array([1.0, 0.0], dtype=np.float32),
        taste_vectors=[np.array([1.0, 0.0], dtype=np.float32)],
        settings=settings,
    )

    assert result.centroid_to_taste == 1.0
    assert result.best_track_evidence == 1.0
    assert result.freshness_bonus == settings.freshness_bonus
    assert result.recently_played_penalty == 0.0
    assert "Fits your taste closely" in result.reasons
    assert "Not yet played" in result.reasons


def test_score_release_applies_recent_skip_and_length_penalties(monkeypatch) -> None:
    settings = ReleaseRecommendationSettings()
    monkeypatch.setattr(release_scoring, "_days_since", lambda _timestamp, _now: 7.0)

    result = score_release(
        agg=_aggregate(duration=(settings.max_duration_minutes + 5) * 60),
        release_pref=_release_pref(last_played_at="2026-07-01T00:00:00+00:00"),
        track_pref_rows=[
            {
                "liked": False,
                "score": 0.0,
                "skip_count": 8,
                "early_skip_count": 8,
                "play_count": 10,
                "completion_count": 0,
            }
        ],
        centroid=np.array([1.0, 0.0], dtype=np.float32),
        taste_centroid=np.array([1.0, 0.0], dtype=np.float32),
        taste_vectors=[],
        settings=settings,
    )

    assert result.recently_played_penalty == settings.recent_play_penalty * 0.5
    assert result.high_skip_penalty > 0.0
    assert result.length_bias_penalty == settings.length_bias_penalty
    assert result.freshness_bonus == 0.0


def test_rank_releases_respects_excludes_and_artist_cap() -> None:
    class FakeStore:
        def __init__(self) -> None:
            self.aggregates = [
                _aggregate(1, medoid_track_id=11),
                _aggregate(2, medoid_track_id=22),
                _aggregate(3, medoid_track_id=33),
            ]

        def list_ready_release_aggregates(self, model_name: str):
            assert model_name == "discogs_multi"
            return self.aggregates

        def list_positive_track_ids_with_embeddings(self, model_name: str, min_score: float, limit: int):
            assert model_name == "discogs_multi"
            assert min_score == 1.0
            assert limit == 500
            return [100]

        def load_embedding(self, track_id: int, model_name: str):
            assert track_id == 100
            return np.array([1.0, 0.0], dtype=np.float32)

        def load_release_embedding(self, release_id: int, model_name: str):
            vectors = {
                1: np.array([1.0, 0.0], dtype=np.float32),
                2: np.array([0.9, 0.1], dtype=np.float32),
                3: np.array([0.8, 0.2], dtype=np.float32),
            }
            return vectors[release_id]

        def get_release_preference(self, release_id: int):
            return None

        def list_release_track_preferences(self, release_id: int):
            return []

        def artist_ids_for_release(self, release_id: int):
            return [7]

    ranked = rank_releases_for_user(
        FakeStore(),
        "discogs_multi",
        limit=10,
        settings=ReleaseRecommendationSettings(min_matching_tracks=1, max_releases_per_artist=1),
        exclude_release_ids={1},
    )

    assert [item.release_id for item in ranked] == [2]
    assert ranked[0].medoid_track_id == 22
    assert ranked[0].score_breakdown["evidence_ok"] is True
