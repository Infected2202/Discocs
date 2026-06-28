---
name: discocs-api
description: Understanding the Discocs API architecture, routes, and data models for recommendations and playback
source: auto-skill
extracted_at: '2026-06-25T18:46:12.456Z'
---

# Discocs API Architecture, Endpoints, and Data Models

## Overview

The Discocs API is implemented in `app/main.py` using FastAPI framework and provides endpoints for:
- Music discovery and recommendations
- Playback management and state
- Track metadata and analysis data
- User feedback and preferences
- Task management for analysis workflows
- Navidrome integration

## Data Models in Store Layer (`app/store.py`)

The application defines comprehensive data models representing music domain entities:

### Core Entities
- **Track**: Contains metadata like ID, path, artist, title, album, year, duration, etc.
- **Artist**: Music performer information with normalized names
- **Release**: Album/release details with associated data like year, artist, etc.
- **UserPreferences**: Models for track, release, and artist preferences with engagement metrics
- **InstantMixRequest**: Represents a request for generating similar tracks
- **PlaybackSession/Event/QueueItem**: Models for playback management
- **AnalysisJob/Task**: Models for embedding/feature analysis workflow
- **TrackPrediction/Feature/ModelOutput**: Models for AI analysis results

### Data Aggregation Classes
- **TrackListing**: Combines Track with embedding status and predictions
- **SimilarTrack**: Combines Track with distance/similarity metadata
- **FeatureTrack**: Combines Track with audio features
- **NormalizationStatus**: Statistics about normalized library structure

## Core API Endpoints

### Discovery and Search APIs
- **GET `/api/v1/search`**: Unified search across tracks, artists, releases
- **GET `/tracks/search`**: Track-specific search with filtering
- **GET `/tracks/{track_id}/similar`**: Get recommended tracks similar to the specified track
- **GET `/api/v1/artists/{artist_id}`**: Get artist details and statistics
- **GET `/api/v1/releases/{release_id}/tracks`**: Get tracks on a specific album

### Recommendation and Mix APIs
- **POST `/tracks/{track_id}/instant-mix`**: Generate an Instant Mix from a seed track
- **GET `/navidrome/similar`**: Navidrome plugin endpoint for similar tracks (used by Navidrome Instant Mix)
- **POST `/text-search`**: Semantic search by textual description
- **POST `/index/rebuild`**: Rebuild recommendation indexes

### Playback APIs
- **POST `/api/v1/playback/sessions`**: Create a new playback session
- **GET `/api/v1/playback/sessions/{session_id}`**: Get session details
- **PATCH `/api/v1/playback/sessions/{session_id}`**: Update session state
- **GET `/api/v1/playback/sessions/{session_id}/queue`**: Get track queue
- **PATCH `/api/v1/playback/sessions/{session_id}/queue`**: Update queue state
- **POST `/api/v1/playback/events`**: Submit playback events (progress, completion, skip, etc.)

### Management and Analysis APIs
- **GET `/jobs`**: View analysis job status
- **POST `/jobs/analyze`**: Start embedding analysis
- **POST `/jobs/analyze-audio-features`**: Start audio feature extraction
- **POST `/jobs/analyze-heads`**: Start classification head prediction extraction
- **POST `/jobs/check-missing-files`**: Reconcile file system with catalog
- **GET `/lost-files`**: List tracks pointing to non-existent files
- **DELETE `/lost-files`**: Remove missing tracks from database

### Settings and Configuration APIs
- **GET `/api/v1/playback/settings`**: Get playback settings defaults
- **GET `/settings`**: Get system settings
- **POST `/feedback`**: Submit user feedback about recommendations

## Key API Utilities and Pattern

### Request/Response Models (`app/main.py`)
FastAPI uses Pydantic models for typed request/response validation:
- **Input requests**: Models for creating sessions (`PlaybackSessionCreateRequest`), processing events (`PlaybackEventRequest`), etc.
- **Response models**: Structured responses with standardized field sets like `TrackSummaryResponse`, `SearchResponse`, `PlaybackSessionEnvelopeResponse`

### API Design Patterns

#### 1. Similar Track Pattern
Uses `similar_track_dict(result: SimilarTrack)` utility function which creates consistent dictionaries for recommended tracks:
```python
def similar_track_dict(result: SimilarTrack) -> dict[str, object]:
    data = track_dict(result.track)
    data["distance"] = result.distance
    data["similarity"] = result.similarity
    data["rating"] = result.rating  # from user feedback
    return data
```

#### 2. Session Continuation
The playback system uses UUID identifiers for:
- Playback Sessions: Allow continuing playback across device sessions
- Queue Items: Identify tracks in playback queues with detailed metadata
- Events: Track playback history and enable analytics

#### 3. Event-Based Preference System
Playback events are converted to preference updates using a rules-based system:
- Play threshold reached: Increases play count
- Completion: Increases completion count
- Skip: Increases skip count with early/late skip scoring
- Like/unlike/dislike: Updates like status and scores
- Reposting to queue: Boosts score
- Removed from queue: Decreases score

#### 4. Lease-Based Task Distribution
Analysis endpoints implement distributed processing:
- Workers claim analysis tasks via lease mechanism
- Leased tasks prevent multiple processing
- Lease expirations return abandoned tasks to queue
- Atomic updates ensure consistency

## Frontend Integration
The API serves a React-like frontend embedded in main.py, with server-side templating for:
- Dashboard with pipeline controls
- Browse and search interface
- Mix management
- Detailed track analysis inspector
- Job status monitoring
- Settings configuration

## Navidrome Integration API
Additional endpoints facilitate Navidrome plugin functionality:
- `/navidrome/ping` - Health and compatibility check
- `/navidrome/similar` - Core similar track recommendation API for Navidrome plugin
- `/navidrome/starred` - Get user's starred items in Navidrome
- `/navidrome/starred/similar` - Similar tracks to starred items
- Integration for `external_tracks` linking Navidrome IDs to internal tracks
- `/jobs/navidrome-sync` - Synchronize catalog metadata

## Error Handling Pattern
The APIs follow a consistent pattern for errors with structured responses containing error codes, messages, and additional debugging information where appropriate.

## Performance Optimizations
The API implementation includes optimizations for:
- Caching of index metadata on disk
- Efficient vector storage and retrieval for embeddings
- Lazy loading where possible
- Batching operations (queues, events)
- SQLite indexing strategy
- Conditional response caching

This API structure enables both the web UI as well as the separate Telegram bot to interact with the music library in a cohesive way while maintaining scalable performance characteristics for personal collections.