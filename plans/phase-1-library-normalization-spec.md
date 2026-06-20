# Phase 1 Spec: Library Entity Normalization

## Purpose

Add normalized library entities for artists and releases while preserving the
existing track-centric MVP behavior.

This phase prepares the backend for:

- artist pages;
- release/album pages;
- entity search;
- album/release recommendations;
- generated mixes;
- Flow and autoplay context;
- future web UI on another stack.

## Current State

Current catalog model:

- `tracks` table stores `artist`, `title`, `album`, `genre`, `year`, `duration`,
  path, size, mtime.
- `external_tracks` maps provider/external track id to internal `track_id`.
- Navidrome sync imports songs as tracks and stores raw song JSON.
- Local scanner reads only simple tags: artist, title, album, genre, year,
  duration.
- Embeddings/features/predictions are track-based.
- Existing tests expect rescans to preserve track IDs and keep embeddings when
  file size/mtime do not change.

Important compatibility constraint:

- Do not remove or stop updating `tracks.artist`, `tracks.album`, `tracks.title`
  in this phase.
- Existing scan/analyze/index/similar/Navidrome endpoints must keep working.

## Phase Strategy

Use sidecar normalization:

1. Keep current `tracks` schema as compatibility surface.
2. Add normalized tables.
3. Backfill normalized entities from existing tracks and Navidrome raw JSON.
4. Update scan/sync code to maintain normalized tables after each track upsert.
5. Add new `/api/v1` entity endpoints on top of normalized data.

This avoids a high-risk rewrite and lets the future web use better APIs while
the current prototype keeps working.

## Schema Additions

### `artists`

Purpose:

- stable artist entity for artist pages, search, credits, and recommendations.

Fields:

- `id INTEGER PRIMARY KEY`
- `name TEXT NOT NULL`
- `sort_name TEXT`
- `normalized_name TEXT NOT NULL`
- `image_url TEXT`
- `bio TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Indexes:

- unique index on `normalized_name`
- index on `name`

Notes:

- `normalized_name` should be casefolded, whitespace-normalized display name.
- Do not aggressively merge aliases yet.

### `artist_aliases`

Purpose:

- future alias and metadata-provider support.

Fields:

- `artist_id INTEGER NOT NULL`
- `alias TEXT NOT NULL`
- `normalized_alias TEXT NOT NULL`
- `source TEXT NOT NULL`
- `created_at TEXT NOT NULL`

Indexes:

- unique `(normalized_alias, source)`
- index `(artist_id)`

Phase 1 use:

- optional. Add table now, but backfill can be minimal.

### `releases`

Purpose:

- stable release/album/EP/single/compilation entity.

Fields:

- `id INTEGER PRIMARY KEY`
- `title TEXT NOT NULL`
- `normalized_title TEXT NOT NULL`
- `release_type TEXT NOT NULL DEFAULT 'unknown'`
- `release_date TEXT`
- `release_year INTEGER`
- `cover_art_id TEXT`
- `track_count INTEGER NOT NULL DEFAULT 0`
- `duration REAL`
- `label TEXT`
- `catalog_number TEXT`
- `identity_key TEXT NOT NULL`
- `identity_confidence TEXT NOT NULL DEFAULT 'derived'`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Indexes:

- unique `identity_key`
- index `(normalized_title)`
- index `(release_year)`
- index `(release_type)`

Release types:

- `album`
- `ep`
- `single`
- `compilation`
- `soundtrack`
- `mix`
- `unknown`

### `release_tracks`

Purpose:

- ordered relationship between releases and tracks.

Fields:

- `release_id INTEGER NOT NULL`
- `track_id INTEGER NOT NULL`
- `disc_number INTEGER`
- `track_number INTEGER`
- `position INTEGER NOT NULL`
- `title_override TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

Constraints/indexes:

- primary key `(release_id, track_id)`
- unique `(release_id, position)`
- index `(track_id)`

### `track_artists`

Purpose:

- track-level artist credits.

Fields:

- `track_id INTEGER NOT NULL`
- `artist_id INTEGER NOT NULL`
- `role TEXT NOT NULL DEFAULT 'primary'`
- `position INTEGER NOT NULL DEFAULT 0`
- `credit_text TEXT`
- `confidence TEXT NOT NULL DEFAULT 'derived'`
- `created_at TEXT NOT NULL`

Constraints/indexes:

- primary key `(track_id, artist_id, role, position)`
- index `(artist_id, role)`
- index `(track_id)`

Roles:

- `primary`
- `featured`
- `remixer`
- `composer`
- `producer`
- `unknown`

Phase 1:

- use `primary` for parsed/simple artist credits.
- keep full text in `credit_text`.

### `release_artists`

Purpose:

- release-level artist credits and discography grouping.

Fields:

- `release_id INTEGER NOT NULL`
- `artist_id INTEGER NOT NULL`
- `role TEXT NOT NULL DEFAULT 'primary'`
- `position INTEGER NOT NULL DEFAULT 0`
- `credit_text TEXT`
- `confidence TEXT NOT NULL DEFAULT 'derived'`
- `created_at TEXT NOT NULL`

Constraints/indexes:

- primary key `(release_id, artist_id, role, position)`
- index `(artist_id, role)`
- index `(release_id)`

### `external_ids`

Purpose:

- generic provider mapping for tracks, artists, releases, playlists.

Fields:

- `provider TEXT NOT NULL`
- `entity_type TEXT NOT NULL`
- `entity_id INTEGER NOT NULL`
- `external_id TEXT NOT NULL`
- `raw_json TEXT`
- `synced_at TEXT NOT NULL`

Constraints/indexes:

- primary key `(provider, entity_type, external_id)`
- index `(entity_type, entity_id)`

Phase 1 compatibility:

- keep existing `external_tracks`.
- add `external_ids` and optionally mirror Navidrome track mappings into it.
- do not remove `external_tracks` yet.

## Normalization Rules

### Text normalization

Use one shared helper:

```text
normalize_text(value):
  strip
  collapse whitespace
  casefold
```

Keep display text as originally read where possible.

### Artist identity

Recommended solution:

- create one artist entity for the full display artist string;
- also parse likely component artists into `track_artists` only when confidence
  is acceptable;
- keep `credit_text` with the original full artist string.

Decision point: how aggressive should artist splitting be?

Options:

1. No splitting in Phase 1.
2. Conservative splitting on clear separators.
3. Aggressive splitting on many patterns.

Recommendation:

- Conservative splitting, but keep full-credit artist too only if needed for
  display/search.

Reason:

- Full credit preserves exact local metadata.
- Conservative components make artist pages useful.
- Aggressive splitting can damage names and collaborations.

Conservative split patterns:

- `;`
- ` & `
- ` feat. `
- ` ft. `
- ` featuring `
- comma only when metadata provider clearly uses array/list, not plain text.

Do not split:

- `The Artist`
- names with punctuation that may be part of artist identity;
- unknown ambiguous strings.

### Release identity

Recommended identity key priority:

1. Provider release/album ID if available.
2. Local path/folder grouping + normalized album title + album artist.
3. Normalized album title + album artist + year.
4. Fallback synthetic release for track if album title missing.

Decision point: do we create releases for tracks without album?

Options:

1. Leave `release_id` null.
2. Create synthetic one-track release.

Recommendation:

- Create synthetic one-track release.

Reason:

- UI rule says every track belongs to a release-like container.
- It simplifies queue/search/player navigation.

Synthetic release title:

- track title if available;
- file stem fallback.

Synthetic release type:

- `unknown` unless provider/tag metadata explicitly says otherwise.

### Release artist

Priority:

1. all explicit release-level artists from provider/tag metadata, if available;
2. all track-level primary artists participating in the release;
3. album artist tag/provider field as display grouping signal, if available;
4. `Various Artists` only if provider/raw metadata explicitly indicates it;
5. unknown artist fallback.

Current local scanner does not read albumartist, so Phase 1 should add support
for extra metadata fields before relying heavily on release grouping.

Album/release page display rule:

- show all known artists participating in the release card/header;
- artist links navigate to artist pages;
- related discography and recommendations below the release can be mixed from
  all participating artists, not only the first/primary artist.

Recommended scanner metadata additions:

- album_artist
- track_number
- disc_number
- total_tracks optional
- date/originaldate already partially handled

### Release type

Source priority:

1. explicit provider/raw metadata;
2. local tags if available;
3. `unknown`.

Important:

- Do not guess release type from track count or duration in normal product
  behavior.
- If type is not explicit, keep it as `unknown`.
- UI should show unknown type under `Releases`.
- Any future inferred type should be debug/analysis-only unless explicitly
  accepted by the user.

## Backfill Algorithm

Input:

- existing `tracks`;
- existing `external_tracks.raw_json` for Navidrome;
- local path/folder information.

Algorithm:

1. Iterate tracks ordered by id.
2. Load Navidrome raw JSON if available.
3. Build a `TrackMetadataEnvelope`:
   - track title;
   - track artist credit;
   - album/release title;
   - album artist if available;
   - genre/year;
   - duration;
   - path;
   - provider IDs/raw fields.
4. Resolve/create artists:
   - release artist;
   - track artists.
5. Resolve/create release:
   - provider ID if available;
   - path-aware identity fallback;
   - metadata fallback;
   - synthetic one-track fallback.
6. Upsert `release_tracks`.
7. Upsert `track_artists`.
8. Upsert `release_artists`.
9. Optionally mirror current `external_tracks` into `external_ids`.
10. Recompute release aggregate basics:
    - track count;
    - duration;
    - release year/date if available;
    - cover art id if available.

Backfill should be idempotent.

## Runtime Maintenance

After `upsert_track`:

- update compatibility fields on `tracks`;
- update normalized artist/release sidecar tables;
- if file changed and derived data is invalidated, normalized metadata should
  still be updated from tags.

After Navidrome sync:

- update tracks as today;
- update `external_tracks` as today;
- update/mirror `external_ids`;
- update normalized sidecar tables from Navidrome raw JSON.

After deletion:

- `release_tracks`, `track_artists`, and generic mappings should cascade or be
  explicitly cleaned.
- releases/artists with zero tracks can remain initially, but a cleanup job can
  prune or mark them later.

Decision point: cleanup orphan artists/releases immediately?

Options:

1. Delete orphans immediately.
2. Keep orphans and cleanup periodically.

Recommendation:

- Keep orphans initially, cleanup periodically.

Reason:

- Safer during sync/backfill.
- Avoids accidental deletion during temporary missing-file states.

## API Consequences

Phase 1 should enable these future endpoints:

- `GET /api/v1/artists/{id}`
- `GET /api/v1/artists/{id}/discography`
- `GET /api/v1/releases/{id}`
- `GET /api/v1/releases/{id}/tracks`
- `GET /api/v1/search`

But Phase 1 implementation can stop at store/query methods and schema if needed.

API response IDs should use normalized entity IDs, not text names.

## Code Touchpoints

Observed current files:

- `app/metadata.py`
  - currently returns `AudioMetadata` with artist, title, album, genre, year,
    duration;
  - Phase 1 should extend it with album artist, track number, disc number, and
    maybe total tracks.
- `app/scanner.py`
  - currently emits `ScannedTrack` with track-level metadata;
  - Phase 1 should pass through the extra metadata fields without changing
    existing callers.
- `app/navidrome.py`
  - currently models Navidrome songs and keeps raw provider data;
  - Phase 1 should keep using raw data as the richest source for provider IDs,
    album/release fields, and future artwork mapping.
- `app/store.py`
  - owns SQLite schema initialization and track/embedding/external mapping
    behavior;
  - Phase 1 should add schema creation, normalization helpers, sidecar upserts,
    backfill, and query methods here or in a small adjacent module if it becomes
    too large.
- `app/main.py`
  - currently exposes track-centric API routes;
  - Phase 1 can add `/api/v1` entity routes after store methods exist.
- `tests/test_store.py`
  - important compatibility coverage for track upsert, embedding preservation,
    and `external_tracks`.
- `tests/test_scanner.py`
  - extend for album artist and ordering tags.
- `tests/test_navidrome_sync.py`
  - extend for normalized sidecars and idempotent sync.
- `tests/test_api.py`
  - extend when `/api/v1` entity endpoints are added.

## Phase 1 Build Order

Recommended implementation order:

1. Metadata expansion.
   - Extend `AudioMetadata` and `ScannedTrack`.
   - Read album artist, track number, disc number.
   - Keep existing fields and tests passing.
2. Schema additions.
   - Add `artists`, `artist_aliases`, `releases`, `release_tracks`,
     `track_artists`, `release_artists`, `external_ids`.
   - Keep table creation idempotent.
3. Normalization helpers.
   - Add shared text normalization.
   - Add release identity key builder.
   - Add conservative artist parser.
4. Store-side sidecar upsert.
   - Add a method that accepts track id plus metadata envelope.
   - Resolve/create artist and release rows.
   - Upsert relationship rows.
5. Backfill.
   - Add command/job to normalize existing tracks.
   - Make it idempotent and safe to rerun.
6. Navidrome sync integration.
   - After each track import/update, update sidecars from raw provider data.
   - Mirror Navidrome track mappings into `external_ids`.
7. Query methods.
   - Fetch release with tracks.
   - Fetch artist core.
   - Fetch artist discography grouped by release relationship/type.
8. Optional `/api/v1` endpoints.
   - Add only after query methods are covered by tests.

Stop points:

- A safe first PR can stop after metadata expansion + schema + backfill tests.
- A second PR can wire scan/Navidrome runtime maintenance.
- A third PR can expose `/api/v1` artist/release/search endpoints.

## Testing Plan

Schema/backfill tests:

- creates artist from existing track artist;
- creates release from existing album;
- creates release_tracks with correct order fallback;
- backfill is idempotent;
- unknown album creates synthetic one-track release;
- missing release type groups as unknown;
- external_tracks mirrored into external_ids;
- deletion does not break existing external mapping behavior.

Scanner tests:

- reads album_artist when present;
- reads track_number;
- reads disc_number;
- still handles existing simple metadata.

Navidrome sync tests:

- sync creates normalized artist/release sidecars;
- raw_json provider metadata is preserved;
- migrated local mapping still maps to same normalized track/release;
- idempotent sync does not duplicate artists/releases.

Compatibility tests:

- existing `Store.upsert_track` behavior unchanged;
- embedding invalidation still depends on file size/mtime;
- metadata-only rescan still preserves embeddings;
- existing track APIs still return `artist`, `album`, `genre`, `year`.

## Finalized Decisions

### Artist splitting aggressiveness

Decision:

- conservative splitting with original credit preserved.

### Synthetic release for tracks without album

Decision:

- create synthetic release.

### Release type heuristic

Decision:

- do not guess release type in product behavior.
- if provider/tags do not specify type, store `unknown` and show it as
  `Releases`.
- possible future inference belongs to debug/analysis, not normal UI grouping.

### External IDs migration

Decision:

- add generic `external_ids`, keep `external_tracks` for compatibility, mirror
  Navidrome track mappings.

## Implementation Notes

Do not perform this as one huge rewrite.

Suggested implementation chunks:

1. Extend metadata structs to carry album artist, track/disc numbers.
2. Add schema tables and row mappers.
3. Add normalization helpers.
4. Add backfill command/job.
5. Update `upsert_track` and Navidrome sync to maintain sidecars.
6. Add store query methods for artist/release pages.
7. Add `/api/v1` entity endpoints.

Each chunk should have tests.

## PR Slices

The slices below are ordered to keep the app usable after each merge. Each PR
should preserve existing tests and avoid changing recommender behavior unless
the slice explicitly says so.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slices 2-3.
- PR 3: Slice 4.
- PR 4: Slice 5.
- PR 5: Slice 6.
- PR 6: Slices 7-8.
- PR 7: Slice 9.

Reason:

- metadata expansion is low-risk and should land alone;
- schema and pure helpers can be reviewed together;
- sidecar writes deserve isolated review;
- backfill and runtime maintenance are separate risk areas;
- query methods and API endpoints are close enough to pair once storage is
  stable;
- diagnostics can come last.

### Slice 1: Metadata Expansion

Goal:

- carry richer release metadata through scanner/Navidrome paths without adding
  normalized tables yet.

Code changes:

- extend `AudioMetadata` in `app/metadata.py`;
- extend `ScannedTrack` in `app/scanner.py`;
- read and parse:
  - `album_artist`;
  - `track_number`;
  - `disc_number`;
  - optionally `total_tracks`;
- keep existing `artist`, `title`, `album`, `genre`, `year`, `duration`
  behavior unchanged.

Tests:

- scanner reads album artist when tag exists;
- scanner reads track/disc number when tags exist;
- scanner still handles files with only simple metadata;
- existing scan/store tests still pass.

Acceptance:

- no schema changes required;
- no API behavior changes required;
- no Essentia/model dependency introduced.

### Slice 2: Normalization Schema

Goal:

- add durable tables for artists/releases/credits without writing runtime logic
  into them yet.

Code changes:

- add schema creation for:
  - `artists`;
  - `artist_aliases`;
  - `releases`;
  - `release_tracks`;
  - `track_artists`;
  - `release_artists`;
  - `external_ids`;
- add lightweight row types/helpers if current store style benefits from them;
- keep `tracks` and `external_tracks` unchanged.

Tests:

- schema initializes on empty DB;
- schema initialization is idempotent;
- existing store tests still pass;
- old DB shape can be opened and upgraded by `Store`.

Acceptance:

- the app can start with the new schema;
- no track upsert behavior changes yet;
- no backfill required yet.

### Slice 3: Normalization Helpers

Goal:

- implement deterministic helper logic before wiring it into writes.

Code changes:

- add shared `normalize_text`;
- add release identity key builder;
- add conservative artist credit parser;
- add metadata envelope object/function that can be built from:
  - local scanned metadata;
  - current `tracks` row;
  - Navidrome raw JSON.

Rules:

- conservative artist splitting only;
- preserve original credit text;
- do not guess release type;
- release type is explicit value or `unknown`;
- synthetic release type is `unknown` unless provider/tags explicitly say
  otherwise.

Tests:

- text normalization trims/casefolds/collapses whitespace;
- identity key prefers provider release/album ID when present;
- identity key falls back to path-aware local identity;
- artist parser splits clear separators and avoids ambiguous punctuation;
- release type remains `unknown` when not explicit.

Acceptance:

- helpers are pure/deterministic;
- no DB writes are required in this slice.

### Slice 4: Store Sidecar Upserts

Goal:

- create/update normalized sidecar entities for one track.

Code changes:

- add store methods for:
  - get/create artist by normalized name;
  - get/create release by identity key;
  - upsert release-track relationship;
  - upsert track artist credits;
  - upsert release artist credits;
  - mirror external mapping into `external_ids`;
- add one high-level method, for example
  `upsert_normalized_track_sidecars(track_id, metadata_envelope)`;
- do not call it from scan/Navidrome paths yet unless tests use it directly.

Tests:

- creates artists from a single track;
- creates release from album metadata;
- creates synthetic release when album is missing;
- creates all participating artists for a multi-artist release;
- stores original credit text;
- idempotent repeated upsert does not duplicate rows;
- explicit unknown release type remains unknown;
- `external_tracks` can be mirrored into `external_ids`.

Acceptance:

- normalized data can be created manually through store methods;
- existing track upsert and embedding invalidation behavior remain unchanged.

### Slice 5: Backfill Command/Job

Goal:

- normalize existing libraries without requiring a rescan.

Code changes:

- add a backfill function that iterates existing tracks;
- read Navidrome raw JSON when available;
- build metadata envelope for each track;
- call sidecar upsert method;
- recompute release basics:
  - track count;
  - duration;
  - release year/date if explicit;
  - cover/art reference if available;
- expose through CLI or existing job mechanism.

Suggested command shape:

```text
recs normalize-library
```

or a job endpoint if that matches current operational UI better.

Tests:

- backfill creates sidecars from existing tracks;
- backfill is idempotent;
- backfill handles missing album by synthetic release;
- backfill uses provider raw JSON when available;
- backfill does not delete or alter embeddings.

Acceptance:

- user can run one command/job to populate normalized tables;
- rerunning is safe.

### Slice 6: Runtime Maintenance

Goal:

- keep normalized tables current during normal scan and Navidrome sync.

Code changes:

- after local `upsert_track`, build metadata envelope and upsert sidecars;
- after Navidrome song import/update, build provider-aware envelope and upsert
  sidecars;
- mirror Navidrome track mapping into `external_ids`;
- preserve current `external_tracks` behavior.

Tests:

- scan creates/updates normalized sidecars;
- Navidrome sync creates/updates normalized sidecars;
- Navidrome sync remains idempotent;
- migrated local mapping still points to the same track;
- stale/missing Navidrome behavior stays compatible with current tests.

Acceptance:

- normalized data stays fresh without manual backfill for new/updated tracks;
- current operational workflows still work.

### Slice 7: Entity Query Methods

Goal:

- provide backend query primitives for artist/release pages before adding API
  routes.

Code changes:

- query release core by id;
- query release tracks ordered by disc/track/position;
- query all release artists;
- query artist core by id;
- query artist discography grouped by:
  - explicit type groups;
  - `Releases` for unknown type;
  - `Featured In` for releases where artist appears on tracks but is not a
    release artist;
- query mixed related discography for all artists on a release.

Tests:

- release query includes all participating artists;
- release track order is stable;
- artist discography groups unknown type under `Releases`;
- featured-in releases are separated;
- multi-artist release related discography can mix all participants.

Acceptance:

- future UI/API can render album and artist pages from store methods.

### Slice 8: `/api/v1` Entity Endpoints

Goal:

- expose the normalized data through stable API contracts for the future web.

Code changes:

- add `/api/v1/releases/{id}`;
- add `/api/v1/releases/{id}/tracks`;
- add `/api/v1/artists/{id}`;
- add `/api/v1/artists/{id}/discography`;
- optionally add a minimal `/api/v1/search` grouped by artists/releases/tracks;
- keep existing prototype routes unchanged.

Response principles:

- use internal normalized IDs;
- include display fields and artwork URLs/IDs;
- include all known release artists;
- use `release` naming internally, while UI can label it album/release;
- no guessed release type.

Tests:

- release endpoint returns metadata, all artists, and actions/display fields;
- release tracks endpoint returns ordered tracks;
- artist endpoint returns core fields;
- artist discography endpoint returns grouped releases;
- unknown types appear as `Releases`;
- existing API tests still pass.

Acceptance:

- a future frontend can build album and artist pages without reading old
  track-centric endpoints.

### Slice 9: Operational Cleanup And Diagnostics

Goal:

- make normalized catalog state inspectable and safe to maintain.

Code changes:

- add basic normalization status:
  - total tracks;
  - tracks with release sidecar;
  - tracks with artist sidecar;
  - releases count;
  - artists count;
  - orphan releases/artists count;
- add optional cleanup command for orphan sidecars;
- keep cleanup manual/explicit at first.

Tests:

- status counts are correct on tiny catalog;
- cleanup does not remove artists/releases that still have relationships;
- cleanup leaves `external_tracks` compatibility intact.

Acceptance:

- user can see whether normalization is complete;
- cleanup is explicit and not risky during sync/backfill.
