"""API response Pydantic models.

Extracted from app/main.py.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ApiErrorDetail(BaseModel):
    code: str
    message: str


class ApiErrorResponse(BaseModel):
    error: ApiErrorDetail


class ImageRefResponse(BaseModel):
    url: str | None
    source: str
    placeholder: bool


class EntityActionResponse(BaseModel):
    type: str
    enabled: bool
    endpoint: str | None = None


class ArtistLinkResponse(BaseModel):
    id: int
    name: str


class LibraryStatsResponse(BaseModel):
    tracks: int
    releases: int
    liked_tracks: int
    plays: int


class ArtistSummaryResponse(BaseModel):
    id: int
    name: str
    image: ImageRefResponse
    library_stats: LibraryStatsResponse
    sort_name: str | None = None


class ReleaseSummaryResponse(BaseModel):
    id: int
    title: str
    release_type: str
    release_type_label: str
    artists: list[ArtistLinkResponse]
    release_date: str | None
    release_year: int | None
    track_count: int
    duration: float | None
    artwork: ImageRefResponse


class TrackReleaseLinkResponse(BaseModel):
    id: int
    title: str


class TrackSummaryResponse(BaseModel):
    id: int
    title: str
    artists: list[ArtistLinkResponse]
    duration: float | None
    release: TrackReleaseLinkResponse | None
    artwork: ImageRefResponse
    explicit: bool
    liked: bool
    actions: list[EntityActionResponse]


class ReleaseTrackItemResponse(TrackSummaryResponse):
    disc_number: int | None
    track_number: int | None
    position: int


class SearchTopResultResponse(BaseModel):
    entity_type: str
    entity: dict[str, object]


class SearchGroupResponse(BaseModel):
    type: str
    title: str
    items: list[dict[str, object]]
    total: int
    next_offset: int | None


class SearchResponse(BaseModel):
    query: str
    top_result: SearchTopResultResponse | None
    groups: list[SearchGroupResponse]


class ArtistResponse(BaseModel):
    artist: ArtistSummaryResponse
    actions: list[EntityActionResponse]
    links: dict[str, str]
    top_tracks: list[dict[str, object]] = []


class DiscographyGroupResponse(BaseModel):
    key: str
    title: str
    items: list[dict[str, object]]


class ArtistDiscographyResponse(BaseModel):
    artist: ArtistLinkResponse
    groups: list[DiscographyGroupResponse]


class AvailabilityStubResponse(BaseModel):
    artist: ArtistLinkResponse | None = None
    release: TrackReleaseLinkResponse | None = None
    items: list[dict[str, object]]
    available: bool
    basis: str


class ArtistAvailabilityStubResponse(BaseModel):
    artist: ArtistLinkResponse
    items: list[dict[str, object]]
    available: bool
    basis: str


class ReleaseAvailabilityStubResponse(BaseModel):
    release: TrackReleaseLinkResponse
    items: list[dict[str, object]]
    available: bool
    basis: str


class ReleaseResponse(BaseModel):
    release: ReleaseSummaryResponse
    actions: list[EntityActionResponse]
    links: dict[str, str]


class ReleaseTracksResponse(BaseModel):
    release: TrackReleaseLinkResponse
    items: list[ReleaseTrackItemResponse]


class RelatedDiscographyResponse(BaseModel):
    release: TrackReleaseLinkResponse
    context_artists: list[ArtistLinkResponse]
    items: list[ReleaseSummaryResponse]


class ImageInfoResponse(BaseModel):
    image: ImageRefResponse


# ---------------------------------------------------------------------------
# Navidrome
# ---------------------------------------------------------------------------

class NavidromeSimilarItem(BaseModel):
    item_id: str
    track_id: int
    artist: str | None = None
    title: str | None = None
    album: str | None = None
    distance: float
    similarity: float


class NavidromeSimilarResponse(BaseModel):
    provider: str = "navidrome"
    request_id: str
    seed_item_id: str
    seed_track_id: int
    model: str
    requested_count: int | None = None
    effective_count: int
    min_similarity: float | None = None
    skipped_without_external_id: int = 0
    results: list[NavidromeSimilarItem]


class ExternalAudioSimilarResponse(BaseModel):
    """Similar catalog tracks for a seed that is not in the catalog.

    The seed vector is computed on the fly and never stored, so there is no
    seed track id to report — only what was analyzed.
    """

    source: str = "external_audio"
    request_id: str
    model: str
    effective_count: int
    min_similarity: float | None = None
    duration_seconds: float | None = None
    analyzed_seconds: float | None = None
    analysis_offset_seconds: float = 0.0
    vector_cached: bool = False
    skipped_without_external_id: int = 0
    results: list[NavidromeSimilarItem]


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

class PlaybackSessionSummaryResponse(BaseModel):
    id: str
    source_type: str
    source_id: int | None = None
    source_label: str | None = None
    mode: str
    status: str
    current_track_id: int | None = None
    current_queue_item_id: str | None = None
    current_track: dict[str, object] | None = None
    autoplay_enabled: bool
    shuffle_enabled: bool
    repeat_mode: str
    started_at: str
    updated_at: str
    ended_at: str | None = None
    settings: dict[str, object]
    state: dict[str, object]


class PlaybackQueueItemResponse(BaseModel):
    id: str
    session_id: str
    track_id: int
    track: dict[str, object] | None = None
    position: int
    origin: str
    source_type: str | None = None
    source_id: int | None = None
    status: str
    locked: bool
    reason: str | None = None
    score: float | None = None
    created_at: str
    updated_at: str
    debug: dict[str, object] | None = None


class PlaybackQueueResponse(BaseModel):
    items: list[PlaybackQueueItemResponse]
    current_index: int
    current_item: PlaybackQueueItemResponse | None = None
    upcoming: list[PlaybackQueueItemResponse]
    played: list[PlaybackQueueItemResponse]
    source_items: list[PlaybackQueueItemResponse]
    generated_items: list[PlaybackQueueItemResponse]
    autoplay_pool: list[dict[str, object]] = Field(default_factory=list)


class PlaybackSessionEnvelopeResponse(BaseModel):
    session: PlaybackSessionSummaryResponse
    queue: PlaybackQueueResponse


class PlaybackEventSummaryResponse(BaseModel):
    id: str
    session_id: str | None = None
    queue_item_id: str | None = None
    track_id: int | None = None
    release_id: int | None = None
    artist_id: int | None = None
    event_type: str
    position_seconds: float | None = None
    duration_seconds: float | None = None
    play_fraction: float | None = None
    created_at: str
    client_event_id: str | None = None
    source: str
    payload: dict[str, object]


class PlaybackEventIngestResponse(BaseModel):
    accepted: bool
    duplicate: bool
    event_id: str
    event: PlaybackEventSummaryResponse
    preference_delta: dict[str, object]
    navidrome_scrobble: dict[str, object] | None = None


class PlaybackSettingsResponse(BaseModel):
    settings: dict[str, object]


class AutoplayRefillResponse(BaseModel):
    session_id: str
    added_items: list[PlaybackQueueItemResponse]
    candidate_count: int
    debug: dict[str, object] | None = None
