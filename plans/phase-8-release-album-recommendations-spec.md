# Phase 8 Spec: Release/Album Recommendations

## Purpose

Recommend whole releases/albums with evidence.

This powers:

- Albums For You dashboard shelf;
- recommended albums on release pages;
- release-level explanation and diagnostics.

Release recommendations should not be only "nearest track".

Whole-release recommendation needs aggregate evidence:

- release centroid;
- medoid track;
- best-track evidence;
- taste region coverage;
- user release stats;
- skip/completion behavior.

## Dependencies

Requires:

- normalized releases/artists;
- track embeddings;
- playback events/preferences;
- dashboard API;
- entity APIs.

Useful:

- taste regions from generated mixes/Flow work;
- release-level embeddings storage;
- audio feature summaries.

Does not require:

- MAEST;
- segment embeddings;
- Flow engine.

## Release Aggregates

### `release_aggregates`

Main DB summary fields:

- `release_id INTEGER PRIMARY KEY`
- `track_count INTEGER`
- `available_track_count INTEGER`
- `duration REAL`
- `centroid_model TEXT`
- `medoid_track_id INTEGER`
- `embedding_status TEXT`
- `top_region_matches_json TEXT`
- `audio_summary_json TEXT`
- `preference_summary_json TEXT`
- `updated_at TEXT NOT NULL`

Vector storage:

- store release centroid/embedding in embeddings DB or vector storage;
- keep only summary/status in main DB.

Reason:

- vector blobs can grow heavy;
- main DB should stay metadata/user-state focused.

## Album Representation

### Centroid

- normalized average of track embeddings;
- quick similarity to taste regions;
- weak for varied albums/compilations.

### Weighted Centroid

- weight standout tracks higher;
- possible weights:
  - liked/completed tracks;
  - medoid/representative tracks;
  - track preference score.

### Medoid

- real track closest to release centroid;
- useful as representative playable example;
- avoids explaining a release using an abstract vector only.

### Best-Track Evidence

Score release by how many tracks strongly match user taste.

Rule:

- a full album recommendation should usually have more than one good track;
- if only one track matches, it may be better as a track recommendation.

### Region Coverage

Store:

- which taste regions overlap the release;
- number of matching tracks per region;
- whether album is single-region or bridge album.

## Recommendation Score

Suggested score:

```text
score =
  centroid_to_taste_score
  + best_track_evidence
  + region_coverage_score
  + user_release_preference_score
  + freshness_or_forgotten_bonus
  - recently_played_penalty
  - too_many_skipped_tracks_penalty
  - length_bias_penalty
```

Important:

- repeated skips from a release lower score;
- liked/completed tracks in a release raise score;
- new/unplayed albums can still appear if embedding evidence is strong;
- explanations should be concrete.

Reason examples:

- `You liked 3 tracks`;
- `Near your electro region`;
- `Long time since last played`;
- `Similar to Release X`;
- `Several tracks match your recent listening`.

## APIs

### `GET /api/v1/releases/{id}/recommendations`

Purpose:

- recommended albums below a release page.

Inputs:

- current release centroid;
- current release artists;
- related regions;
- user profile.

Response:

```json
{
  "release": {
    "id": 12,
    "title": "Release title"
  },
  "available": true,
  "basis": "release_similarity",
  "items": []
}
```

### `GET /api/v1/dashboard/shelves/albums_for_you`

Purpose:

- Albums For You dashboard shelf.

Behavior:

- use user taste regions/preferences;
- avoid recently played releases;
- include reasons;
- paginate like other dashboard shelves.

### `POST /api/v1/jobs/release-aggregates`

Purpose:

- recompute release aggregates.

Can be implemented through existing job system.

## Settings

Album recommendation settings:

- centroid weight;
- best-track evidence weight;
- region coverage weight;
- freshness/forgotten bonus;
- recently played penalty window;
- minimum matching tracks for full-release recommendation;
- max releases per artist;
- include compilations/unknown releases.

Defaults:

- require at least 2 positive/matching tracks when recommending a full release,
  except for one-track releases;
- avoid releases played very recently;
- include unknown release types.

## Testing Plan

Aggregate tests:

- centroid is normalized;
- medoid is a real track;
- aggregate skips unavailable tracks;
- varied release stores region coverage.

Scoring tests:

- multiple matching tracks beat one-track evidence;
- recent play penalty applies;
- repeated skip penalty applies;
- liked tracks boost release.

API tests:

- release recommendations return stable response shape;
- Albums For You shelf paginates;
- explanations are present;
- unavailable releases are filtered.

## PR Slices

### Slice 1: Release Aggregate Storage And Job

Goal:

- compute and store release-level summaries.

Includes:

- `release_aggregates`;
- vector storage for centroids;
- medoid calculation;
- recompute job.

### Slice 2: Release Recommendation Scoring

Goal:

- rank releases with evidence.

Includes:

- centroid-to-taste score;
- best-track evidence;
- region coverage;
- user preference stats;
- penalties;
- reason generation.

### Slice 3: APIs And Dashboard Shelf

Goal:

- expose recommendations.

Includes:

- `GET /api/v1/releases/{id}/recommendations`;
- `GET /api/v1/dashboard/shelves/albums_for_you`;
- settings defaults;
- debug score breakdown.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slice 3.

## Open Decisions

No blocking decisions.

Tune later:

- minimum matching tracks;
- weights;
- include/exclude compilations;
- release recency windows.

Important context distinction:

- release-page recommendations answer "what albums/releases are related to this
  source release";
- Albums For You answers "what releases fit my profile";
- Flow uses release signals only as one part of a personal stream and should
  not inherit release-page source-similarity behavior.

Initial defaults:

- release page source similarity weight: `70%`;
- light personal bias on release page: `15%`;
- freshness/forgotten/diversity terms: `15%`;
- Albums For You personal taste weight: `60%`;
- release evidence weight: `25%`;
- freshness/forgotten/diversity terms: `15%`;
- minimum matching tracks for full-release recommendation: `2`, except
  one-track releases;
- recently played penalty applies to Albums For You and dashboard shelves;
- release-page recommendations may include recently played related releases,
  but should label/reason them and avoid over-promoting the currently playing
  release itself.
