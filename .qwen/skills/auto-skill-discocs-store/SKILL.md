---
name: discocs-store
description: Understanding the Store class and data persistence layer in the Discocs music recommendation system
source: auto-skill
extracted_at: '2026-06-25T18:44:52.340Z'
---

# Discocs Store Architecture and Data Persistence Layer

## Overview of the Store Class

The Store class is the central data access layer in the Discocs application, serving as the ORM/database abstraction layer for all data operations within the application. Located in `app/store.py`, this class manages:

- Audio track metadata (path, artist, title, album, year, etc.)
- Track embeddings (vector representations for recommendation engine)
- Predictions and analysis outputs by various ML models
- Playback sessions, queue items, and events
- User feedback and preferences
- Generated mixes and playlists
- Analysis jobs and tasks
- Relationships between entities (artists, releases, tracks)

## Data Model and Schema

The Store manages a comprehensive relational schema with these main entities:

- **tracks**: Contains basic metadata about audio tracks
- **embeddings**: Stores vector representations for recommendation
- **track_predictions**: Model output predictions (genres, moods, etc.)
- **track_model_outputs**: Full model output vectors (classification head outputs)
- **track_features**: Numeric audio features (BPM, key, loudness, etc.)
- **artists**: Music artist information with normalization
- **releases**: Album/release metadata
- **playback_sessions**: Active playback sessions and settings
- **queue_items**: Items in playback queues
- **playback_events**: Events logged during playback (play, pause, skip, etc.)
- **feedback**: User feedback on recommendation quality
- **analysis_jobs**: Running analysis tasks (for embeddings, features, etc.)
- **generated_mixes**: Generated themed playlists
- **external_tracks**: Mapping between internal tracks and external IDs (e.g. Navidrome)

## Key Methods and Functionality

The Store class provides:

### Track Management
- `upsert_track()` - Add or update a track from scanned audio file metadata
- `list_tracks()`, `search_tracks()` - List and search tracks
- `get_track()` - Retrieve a single track
- Methods for managing track missing states (`mark_track_missing()`, `mark_track_available()`)

### Embedding Operations
- `save_embedding()` - Persist model embeddings for tracks
- `load_embedding()`, `load_embeddings()` - Load vector representations
- `list_tracks_missing_embedding()` - Get tracks that need embedding extraction

### Recommendation Integration
- `save_feedback()` - Store user feedback on track recommendations
- `feedback_for_seed()` - Get past user feedback for a given seed track

### Playback and Session Management
- `create_playback_session()` - Create a new playback session
- `get_playback_session()` - Retrieve session details
- `update_playback_session()` - Modify session state
- `record_playback_event()` - Log events during playback (completions, skips, etc.)

### Analysis and Processing Jobs
- `create_analysis_job()` - Start analysis processing tasks
- `claim_analysis_tasks()` - Assign work to processing nodes
- `complete_analysis_task()` - Mark analysis tasks as complete
- `expire_analysis_leases()` - Handle abandoned tasks

### User Preferences and Analytics
- `get_track_preference()`, `get_release_preference()`, `get_artist_preference()` - Get user engagement data
- `_apply_playback_event_preferences()` - Update preferences based on playback events

## Usage Patterns

### Integration with API Layer (`main.py`)
The FastAPI application extensively uses the Store to:
1. Implement all endpoints (search, recommendations, playback, etc.)
2. Process feedback from UI and bots
3. Manage user settings and sessions
4. Serve analytics and dashboard information

### Integration with CLI (`cli.py`)
The command-line interface uses Store to:
1. Perform scans and track database updates
2. Process batch analysis (extract embeddings/features)
3. Execute administrative tasks (normalize library, check db, rebuild clean)
4. Process Navidrome sync

### Integration with Telegram Bot
The discocs_bot connects to the Store indirectly through the Discocs API, which itself uses the Store for:
1. Retrieving track information
2. Getting recommendation inputs
3. Logging user interactions

## Key Design Principles

1. **Single Transaction Wrapper**: All data methods wrap database connections in transactions, automatically handling commit/rollback.
2. **Immediate Isolation Level**: Uses IMMEDIATE isolation level to reduce lock contention.
3. **Foreign Key Enforcement**: Enables SQLite foreign key constraints to maintain referential integrity.
4. **Normalized Sidecar Tables**: Separates related but less frequently accessed data (like release/artist relationships) into "sidecar" tables connected by foreign keys.
5. **Timestamp Management**: Automatically manages created_at/updated_at timestamps across entities.
6. **Lease-Based Job Processing**: Supports distributed analysis workers through lease mechanisms in analysis tasks.

## Testing Considerations

Testing of the Store occurs primarily in `tests/test_store.py` and covers:
- Embedding round-trips (save and load)
- Changed file detection and cache invalidation
- Vector normalization and pooling
- Recommendation filtering (remove seed, cap artists, exclude same album)
- Playback event processing and preference updates
- File availability and missing file handling

This approach ensures data consistency while maintaining good performance characteristics for a personal music library system.