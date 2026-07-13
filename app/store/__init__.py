"""app/store package.

Assembles Store from domain mixins; re-exports public symbols for
backward compatibility with all existing import sites.

Stage 7: Split Store into domain mixins.
"""
from __future__ import annotations

from app.store.base import INITIALIZED_DB_PATHS, StoreBase
from app.store.library import LibraryStoreMixin
from app.store.playback import PlaybackStoreMixin
from app.store.mixes import MixesStoreMixin
from app.store.files import FilesStoreMixin
from app.store.jobs import JobsStoreMixin
from app.store.embeddings import EmbeddingsStoreMixin
from app.store.release_aggregates import ReleaseAggregatesStoreMixin
from app.store.flow import FlowStoreMixin
from app.store.map_atlas import MapAtlasStoreMixin
from app.store.sessions import SessionsStoreMixin
from app.store.users import UsersStoreMixin
from app.store.settings import SettingsStoreMixin
from app.store._helpers import (
    playback_event_is_completion,
    playback_event_is_early_skip,
    playback_skip_score_delta,
    row_to_analysis_job,
    row_to_analysis_task,
    row_to_analysis_worker,
    row_to_artist,
    row_to_external_track,
    row_to_generated_mix,
    row_to_generated_mix_item,
    row_to_instant_mix_request,
    row_to_playback_event,
    row_to_playback_session,
    row_to_playlist,
    row_to_playlist_item,
    row_to_queue_item,
    row_to_release,
    row_to_track,
    row_to_user_artist_preference,
    row_to_user_release_preference,
    row_to_user_track_preference,
    similar_track_dict,
    track_dict,
    track_listing_dict,
)

# Re-export models imported via store for backward compatibility
from app.models import (  # noqa: F401
    AnalysisJob,
    AnalysisTask,
    AnalysisWorker,
    Artist,
    ArtistSummaryRow,
    ExternalTrack,
    FeatureSummary,
    FeatureTrack,
    GeneratedMix,
    GeneratedMixItem,
    HeadSummary,
    InstantMixRequest,
    InstantMixRequestParams,
    InstantMixRequestRecord,
    NormalizationStatus,
    Playlist,
    PlaylistItem,
    PlaybackEvent,
    PlaybackEventCreate,
    PlaybackSession,
    QueueItem,
    Release,
    FlowGenerationRun,
    FlowProfile,
    FlowRegion,
    FlowRegionTrack,
    MapProjection,
    ReleaseAggregate,
    ReleaseSummaryRow,
    ReleaseTrackRow,
    SimilarTrack,
    Track,
    TrackFeature,
    TrackListing,
    TrackModelOutput,
    TrackPrediction,
    UserArtistPreference,
    UserReleasePreference,
    UserTrackPreference,
    utc_now,
)


class Store(
    StoreBase,
    LibraryStoreMixin,
    PlaybackStoreMixin,
    MixesStoreMixin,
    FilesStoreMixin,
    JobsStoreMixin,
    EmbeddingsStoreMixin,
    ReleaseAggregatesStoreMixin,
    FlowStoreMixin,
    MapAtlasStoreMixin,
    SessionsStoreMixin,
    UsersStoreMixin,
    SettingsStoreMixin,
):
    """Assembled Store — all domain mixins composed into one class."""
