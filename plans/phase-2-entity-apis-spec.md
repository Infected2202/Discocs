# Phase 2 Spec: Entity APIs

## Purpose

Expose stable `/api/v1` backend contracts for the future web UI.

Phase 2 turns normalized Phase 1 data into API surfaces for:

- search results page;
- release/album page;
- artist page;
- artist discography;
- top tracks when local playback data exists later;
- similar artists/recommended releases when recommendation data exists later.

This phase should not replace existing prototype routes. Current operational
routes such as `/tracks`, `/jobs`, `/navidrome/...`, `/tracks/{id}/similar`,
and the current HTML UI can remain while `/api/v1` becomes the future web
contract.

## Current State

Current API shape is mostly track-centric:

- `/tracks`
- `/tracks/search`
- `/tracks/{track_id}`
- `/tracks/{track_id}/audio`
- `/tracks/{track_id}/cover`
- `/tracks/{track_id}/similar`
- `/tracks/similar/mix`
- `/text-search`
- `/browse/facets`
- Navidrome and job endpoints

Limitations:

- no normalized artist API;
- no normalized release API;
- no grouped entity search;
- no release page contract;
- no artist discography contract;
- existing track endpoints expose the MVP data shape, not the future web model.

## API Namespace

Decision:

- use `/api/v1`.

Reason:

- future frontend may use another stack;
- versioned API avoids coupling the new web to prototype routes;
- existing endpoints can stay as operational/internal compatibility routes.

## API Design Principles

### Stable IDs

Responses should use normalized internal entity IDs:

- `artist.id`
- `release.id`
- `track.id`

Do not use display text as identity.

### Release Naming

Backend contract should use `release` internally.

UI can label releases as albums where that is natural, but API response names
should avoid assuming every release is an album.

### No Guessed Release Type

If release type is not explicit in provider/tags:

- return `release_type: "unknown"`;
- return `release_type_label: "Release"` or let UI label unknown groups as
  `Releases`;
- do not infer EP/single/album from track count or duration in product API.

### UI-Friendly Shape

Responses should include:

- core entity fields;
- display fields;
- image/artwork URLs or IDs;
- playback/action hints;
- relationship summaries;
- pagination metadata where relevant.

Avoid making frontend assemble basic page layout from many tiny calls when a
single page endpoint can reasonably return the needed summary.

### Debug Fields

Recommendation/scoring/debug fields should be optional:

- hidden by default;
- enabled with query param like `include_debug=true`;
- useful for owner/developer diagnostics.

### Errors

Use consistent JSON errors:

```json
{
  "error": {
    "code": "not_found",
    "message": "Release not found"
  }
}
```

Recommended codes:

- `not_found`
- `invalid_request`
- `unsupported`
- `conflict`
- `internal_error`

## Shared Response Shapes

### `ImageRef`

Purpose:

- keep artwork/avatar references consistent.

Shape:

```json
{
  "url": "/api/v1/releases/123/cover",
  "source": "local",
  "placeholder": false
}
```

Fields:

- `url`: nullable string;
- `source`: `local`, `navidrome`, `generated`, `external`, `none`;
- `placeholder`: boolean.

### `EntityAction`

Purpose:

- let UI know which actions are available without hardcoding every state.

Shape:

```json
{
  "type": "play",
  "enabled": true,
  "endpoint": "/api/v1/playback/sessions"
}
```

Initial action types:

- `play`
- `shuffle`
- `mix`
- `like`
- `unlike`
- `save`
- `overflow`

Phase 2 can return simple action descriptors even before playback APIs exist.
Endpoints may be `null` until Phase 3.

### `TrackSummary`

Shape:

```json
{
  "id": 42,
  "title": "Track title",
  "artists": [
    {
      "id": 7,
      "name": "Artist"
    }
  ],
  "duration": 245.2,
  "release": {
    "id": 12,
    "title": "Release title"
  },
  "artwork": {
    "url": "/api/v1/tracks/42/cover",
    "source": "local",
    "placeholder": false
  },
  "explicit": false,
  "liked": false,
  "actions": []
}
```

Notes:

- `artists` should include parsed known artists;
- if parsing is uncertain, include display text elsewhere or a single fallback
  artist entity;
- no standalone track page URL is required.

### `ArtistSummary`

Shape:

```json
{
  "id": 7,
  "name": "Artist",
  "image": {
    "url": null,
    "source": "none",
    "placeholder": true
  },
  "library_stats": {
    "tracks": 18,
    "releases": 4,
    "liked_tracks": 0,
    "plays": 0
  }
}
```

### `ReleaseSummary`

Shape:

```json
{
  "id": 12,
  "title": "Release title",
  "release_type": "unknown",
  "release_type_label": "Release",
  "artists": [
    {
      "id": 7,
      "name": "Artist"
    }
  ],
  "release_date": null,
  "release_year": 2024,
  "track_count": 6,
  "duration": 2040.0,
  "artwork": {
    "url": "/api/v1/releases/12/cover",
    "source": "local",
    "placeholder": false
  }
}
```

Important:

- multi-artist releases return all known participating artists;
- unknown type remains `unknown`.

## Endpoints

### `GET /api/v1/search`

Purpose:

- power the reactive search page.

Query parameters:

- `q`: required string;
- `type`: optional `all`, `artist`, `release`, `track`, `playlist`;
- `limit`: default 8 per group;
- `offset`: optional for single-type pagination;
- `include_debug`: default false.

Response:

```json
{
  "query": "juno reactor",
  "top_result": {
    "entity_type": "artist",
    "entity": {}
  },
  "groups": [
    {
      "type": "artists",
      "title": "Artists",
      "items": [],
      "total": 0,
      "next_offset": null
    },
    {
      "type": "tracks",
      "title": "Tracks",
      "items": [],
      "total": 0,
      "next_offset": null
    },
    {
      "type": "releases",
      "title": "Releases",
      "items": [],
      "total": 0,
      "next_offset": null
    }
  ]
}
```

Search behavior:

- search normalized artists;
- search releases by title and artist names;
- search tracks by title, artist names, release title;
- start with text search;
- semantic/text-embedding search can be added later as an optional mode;
- playlists can be present as an empty/future group until playlist API exists,
  or omitted until implemented.

Top result:

- choose highest scoring entity across groups;
- prefer exact artist/release/title matches;
- include one object that UI can render as a prominent card.

### `GET /api/v1/artists/{artist_id}`

Purpose:

- render artist page header.

Response:

```json
{
  "artist": {
    "id": 7,
    "name": "Solee",
    "sort_name": "Solee",
    "image": {
      "url": null,
      "source": "none",
      "placeholder": true
    },
    "library_stats": {
      "tracks": 18,
      "releases": 4,
      "liked_tracks": 0,
      "plays": 0
    }
  },
  "actions": [
    {
      "type": "mix",
      "enabled": true,
      "endpoint": null
    }
  ],
  "links": {
    "discography": "/api/v1/artists/7/discography",
    "top_tracks": "/api/v1/artists/7/top-tracks",
    "similar": "/api/v1/artists/7/similar"
  }
}
```

Notes:

- do not show fake global fan counts;
- local stats can be zero/unknown;
- omit social/external fields unless reliable metadata exists.

### `GET /api/v1/artists/{artist_id}/discography`

Purpose:

- render artist discography page/section.

Query parameters:

- `sort`: `release_date_desc` default, `release_date_asc`, `title`;
- `limit`: optional per group;
- `include_tracks`: default false.

Response:

```json
{
  "artist": {
    "id": 7,
    "name": "Artist"
  },
  "groups": [
    {
      "key": "albums",
      "title": "Albums",
      "items": []
    },
    {
      "key": "eps",
      "title": "EPs",
      "items": []
    },
    {
      "key": "singles",
      "title": "Singles",
      "items": []
    },
    {
      "key": "compilations",
      "title": "Compilations",
      "items": []
    },
    {
      "key": "featured_in",
      "title": "Featured In",
      "items": []
    },
    {
      "key": "releases",
      "title": "Releases",
      "items": []
    }
  ]
}
```

Grouping rules:

- explicit `album` -> Albums;
- explicit `ep` -> EPs;
- explicit `single` -> Singles;
- explicit `compilation` -> Compilations;
- artist appears on tracks but is not release artist -> Featured In;
- `unknown` -> Releases.

Do not infer group from track count.

### `GET /api/v1/artists/{artist_id}/top-tracks`

Purpose:

- support Artist Page Top Tracks later.

Phase 2 behavior:

- endpoint can exist but return empty if there is no local playback data;
- alternatively defer endpoint until Phase 3 events exist.

Recommended response:

```json
{
  "artist": {
    "id": 7,
    "name": "Artist"
  },
  "items": [],
  "basis": "local_playback",
  "available": false
}
```

Important:

- do not fake top tracks from external/global popularity;
- if no data, UI should omit the section.

### `GET /api/v1/artists/{artist_id}/similar`

Purpose:

- support similar artists shelf.

Phase 2 behavior:

- endpoint can return empty or low-confidence local results;
- stronger implementation can wait until artist aggregates exist.

Response:

```json
{
  "artist": {
    "id": 7,
    "name": "Artist"
  },
  "items": [],
  "available": false,
  "basis": "not_available"
}
```

Future bases:

- `embedding_regions`;
- `shared_releases`;
- `playlist_cooccurrence`;
- `external_metadata`.

### `GET /api/v1/releases/{release_id}`

Purpose:

- render release/album page header and related links.

Response:

```json
{
  "release": {
    "id": 12,
    "title": "Grindmaster",
    "release_type": "unknown",
    "release_type_label": "Release",
    "artists": [
      {
        "id": 7,
        "name": "Extrawelt"
      }
    ],
    "release_date": "2024-12-06",
    "release_year": 2024,
    "track_count": 6,
    "duration": 2040.0,
    "artwork": {
      "url": "/api/v1/releases/12/cover",
      "source": "local",
      "placeholder": false
    }
  },
  "actions": [
    {
      "type": "play",
      "enabled": true,
      "endpoint": null
    }
  ],
  "links": {
    "tracks": "/api/v1/releases/12/tracks",
    "discography": "/api/v1/releases/12/related-discography",
    "recommendations": "/api/v1/releases/12/recommendations"
  }
}
```

Notes:

- all known participating artists should be included;
- release type must not be guessed;
- label/tour/social fields are not part of the main response for now.

### `GET /api/v1/releases/{release_id}/tracks`

Purpose:

- render release track table.

Response:

```json
{
  "release": {
    "id": 12,
    "title": "Release title"
  },
  "items": [
    {
      "id": 42,
      "disc_number": 1,
      "track_number": 1,
      "position": 1,
      "title": "Track title",
      "artists": [],
      "duration": 245.2,
      "liked": false,
      "artwork": {}
    }
  ]
}
```

Ordering:

- disc number;
- track number;
- stored position;
- fallback to track id only if ordering metadata is unavailable.

Click behavior belongs to playback APIs in Phase 3. Phase 2 only exposes enough
data to render the row and identify track/release context.

### `GET /api/v1/releases/{release_id}/related-discography`

Purpose:

- render discography shelf below an album page.

Behavior:

- use all participating release artists as context;
- mix releases from all meaningful participants;
- avoid flooding the shelf with one artist when several artists are present;
- exclude the current release.

Response:

```json
{
  "release": {
    "id": 12,
    "title": "Release title"
  },
  "context_artists": [],
  "items": []
}
```

### `GET /api/v1/releases/{release_id}/recommendations`

Purpose:

- reserve contract for recommended albums below release page.

Phase 2 behavior:

- can return empty with `available: false`;
- real scoring belongs to Phase 8.

Response:

```json
{
  "release": {
    "id": 12,
    "title": "Release title"
  },
  "available": false,
  "basis": "not_available",
  "items": []
}
```

Future bases:

- same artist;
- shared artists;
- release embedding aggregate;
- user taste regions;
- Flow/listening profile.

### Artwork Endpoints

Recommended:

- `GET /api/v1/releases/{release_id}/cover`
- `GET /api/v1/artists/{artist_id}/image`
- optional `GET /api/v1/tracks/{track_id}/cover`

Phase 2 can reuse existing `/tracks/{track_id}/cover` internally, but future UI
should receive entity-level URLs in response objects.

Behavior:

- return local artwork when available;
- use generated placeholder only if UI requests a URL for missing artwork;
- do not fail the whole entity response because artwork is missing.

## Pagination And Sorting

Use a simple offset/limit shape initially:

```json
{
  "items": [],
  "total": 120,
  "limit": 50,
  "offset": 0,
  "next_offset": 50
}
```

Reason:

- enough for local library sizes;
- easy for search pages and "View all" shelves;
- cursor pagination can be added later if needed.

Sort options:

- release date descending;
- release date ascending;
- title;
- recently added where available later;
- local play/like stats later.

## Compatibility Rules

- Do not remove old routes.
- Do not change existing `/tracks` response shapes in this phase unless tests
  explicitly cover compatibility.
- `/api/v1` should be additive.
- Existing CLI/analyze/index/Navidrome workflows should not depend on `/api/v1`.

## Testing Plan

API tests:

- search returns grouped artists/releases/tracks;
- search returns top result;
- artist endpoint returns core fields and links;
- artist discography groups explicit types and unknown Releases correctly;
- release endpoint returns all known artists;
- release tracks endpoint returns stable order;
- related discography excludes current release and can use all participants;
- recommendation endpoints can return `available: false`;
- missing entity returns consistent `not_found` JSON;
- existing route tests still pass.

Contract tests:

- JSON response has stable required keys;
- unknown release type is not guessed;
- no global fake stats are returned;
- image refs tolerate missing artwork.

## PR Slices

Keep Phase 2 compact. The API layer should land in a few coherent pieces, not
one PR per endpoint.

### Slice 1: API Foundation

Goal:

- establish `/api/v1` response conventions before adding many routes.

Code changes:

- add `/api/v1` router or route grouping;
- add shared response helpers/models for:
  - `ImageRef`;
  - `EntityAction`;
  - `ArtistSummary`;
  - `ReleaseSummary`;
  - `TrackSummary`;
  - consistent error response;
- add small mappers from Phase 1 store query rows to API response shapes;
- add consistent `not_found` handling.

Endpoints:

- optional `GET /api/v1/health` or skip if current `/health` is enough;
- no required entity endpoints yet.

Tests:

- response helpers produce stable keys;
- missing entity helper returns consistent error shape;
- existing prototype routes still pass.

Acceptance:

- API conventions are ready;
- no frontend-visible entity behavior is half-built.

### Slice 2: Search API

Goal:

- support the reactive search page with grouped results.

Endpoints:

- `GET /api/v1/search`

Code changes:

- search artists by normalized/display name;
- search releases by title and artist names;
- search tracks by title, artist names, and release title;
- return grouped response:
  - top result;
  - artists;
  - tracks;
  - releases;
- support `type=all|artist|release|track`;
- support `limit`, `offset`;
- keep semantic search out of scope for now.

Tests:

- exact artist match becomes top result;
- exact release/title match appears in correct group;
- tracks include artist and release summaries;
- empty query returns empty grouped results with `query: ""`;
- pagination fields are stable.

Acceptance:

- search UI can open immediately while user types;
- no playlist group is required until playlist entities exist.

Decision:

- empty or whitespace-only `q` returns empty grouped results with `query: ""`.

### Slice 3: Release APIs

Goal:

- render release/album page from `/api/v1`.

Endpoints:

- `GET /api/v1/releases/{release_id}`;
- `GET /api/v1/releases/{release_id}/tracks`;
- `GET /api/v1/releases/{release_id}/related-discography`;
- `GET /api/v1/releases/{release_id}/cover` if needed.

Code changes:

- map release core response;
- include all participating artists;
- include artwork reference;
- return ordered tracks;
- return related discography based on all release artists;
- no release recommendations scoring yet.

Tests:

- release response includes all known artists;
- unknown release type stays unknown;
- track ordering is disc/track/position stable;
- related discography excludes current release;
- multi-artist release can use all participants.

Acceptance:

- future frontend can build the album/release page including related
  discography.

### Slice 4: Artist APIs

Goal:

- render artist page and discography from `/api/v1`.

Endpoints:

- `GET /api/v1/artists/{artist_id}`;
- `GET /api/v1/artists/{artist_id}/discography`;
- `GET /api/v1/artists/{artist_id}/image` if needed.

Code changes:

- map artist core response;
- expose local library stats where available;
- group discography by explicit type;
- put unknown type under `Releases`;
- separate `Featured In`.

Tests:

- artist response has stable header fields;
- no fake global fan counts;
- discography groups explicit album/ep/single/compilation;
- unknown type appears under `Releases`;
- featured-in releases are separated.

Acceptance:

- future frontend can build artist page main content.

### Slice 5: Future Contract Stubs

Goal:

- define stable endpoints for sections that exist visually but depend on later
  phases.

Endpoints:

- `GET /api/v1/artists/{artist_id}/top-tracks`;
- `GET /api/v1/artists/{artist_id}/similar`;
- `GET /api/v1/releases/{release_id}/recommendations`.

Code changes:

- return `available: false` when required data is missing;
- include `basis: "not_available"` or a real basis when possible;
- keep response shapes stable for future UI.

Tests:

- top tracks returns unavailable without playback data;
- similar artists returns unavailable without aggregates;
- release recommendations returns unavailable before Phase 8 scoring;
- frontend can omit sections based on `available: false`.

Acceptance:

- visual pages can call these endpoints without special-case failures;
- no fake quality signals are introduced.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slices 3-4.
- PR 4: Slice 5.

Reason:

- API foundation should be reviewed alone;
- search is a distinct page/workflow;
- artist and release APIs share mappers and normalized entity patterns, so they
  can land together if the diff remains readable;
- stubs are small and clarify future UI behavior.

## Open Decisions

### Search Endpoint Shape

Recommendation:

- use one grouped `GET /api/v1/search` endpoint first.

Reason:

- matches reactive Deezer-like search page;
- lets UI show top result, artists, tracks, releases together;
- single-type pagination can be added through `type=tracks` later.

### Top Tracks Endpoint Timing

Recommendation:

- define the contract in Phase 2, but allow `available: false` until Phase 3
  playback events exist.

Reason:

- artist page knows how to omit the section;
- avoids fake popularity.

### Similar Artists Timing

Recommendation:

- define the contract in Phase 2, return empty/disabled until aggregates exist.

Reason:

- UI can be built without pretending confidence is high.

### Release Recommendations Timing

Recommendation:

- define the endpoint in Phase 2, real implementation waits for Phase 8.

Reason:

- release page layout can be stable while recommendation quality work remains
  separate.

## Audit Follow-Up Queue

These items are intentionally tracked for a later cross-phase audit instead of
blocking the Phase 2 entity API contract:

- decide whether `/api/v1/artists/{id}/image` should remain a JSON `ImageRef`
  endpoint or become a byte/redirect image endpoint after external image
  caching is added;
- verify whether release/track artwork should expose a future
  `/api/v1/tracks/{id}/cover` URL instead of the existing prototype
  `/tracks/{id}/cover` path;
- revisit search ranking quality after real library usage, especially when
  exact artist, release, and track matches compete;
- decide whether all `/api/v1` routes, including later playback/dashboard
  endpoints, should use fully typed Pydantic response models.
