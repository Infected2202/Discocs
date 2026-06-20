# Recommendation Cards And Flow Plan

## Purpose

Design a recommendations section with familiar music-hosting style cards:
Flow, recommended playlists, genre lanes, discovery blocks, and user-context cards.

We will discuss and design one feature at a time, then append decisions here so
context is not lost between sessions.

## Working Rules

- Focus on one feature at a time.
- Keep product decisions separate from implementation tasks.
- Treat simple approaches as baselines, not as final answers.
- Prefer features that help evaluate recommendation quality for this library.
- Capture open questions before committing to UI/API shape.

## Current Focus: Flow

Flow is one universal personal listening entry point, not a family of separate
recommendation cards. In UI terms it is a single primary card/button: open the
site, press Flow, listen.

It should play what the user loves listening to, adapt through likes and skips,
and work as the main daily route into the product.

This work is also a concept pass for a future first-party web UI. The current
app is a proving ground; decisions here should preserve ideas that matter for a
full dashboard/start page later.

### Problem Statement

A naive average embedding across all likes or all plays is probably too blunt:

- A user can like several different musical regions.
- Averaging distant clusters can land between tastes rather than inside one.
- Recent listening intent can differ from long-term taste.
- Loved tracks, repeated plays, recent plays, skips, and explicit feedback should
  not have the same weight.
- A good Flow needs sequencing and diversity, not only nearest neighbors.

Important correction: Instant Mix, track radio, prompt search, and genre lanes
are not Flow. They can share recommender primitives, but product-wise they are
different features. Flow is the universal personal stream.

### Signals Already Available

From the current app:

- Track metadata: artist, title, album, genre, year, duration.
- Navidrome mapping through external track ids.
- Navidrome starred/loved songs through the Subsonic API.
- Discogs-EffNet embeddings and HNSW cosine search.
- Similar-track recommendations from a seed track.
- Mix recommendations from several seed tracks by blended embedding.
- Text-to-music search through MuQ-MuLan.
- Audio features: bpm, key, scale, loudness, dynamic complexity.
- Discogs head predictions for genre/mood/style-like labels.
- Local feedback on recommendation results.
- Instant mix request audit logs.

### Signals To Add Or Import

Likely needed for a better Flow:

- Navidrome play count.
- Navidrome last played timestamp.
- Navidrome rating.
- Navidrome loved/starred timestamp if available.
- Listen/scrobble history, especially recent listens.
- Skip or negative feedback if we can collect it.
- Per-card impressions/clicks/saves/dismissals.

Important: Navidrome is unlikely to give us reliable skip events. Skip tracking
should be treated as first-party product telemetry in our own web UI/player.
Flow quality depends on this enough that it should be designed into the future
dashboard from the beginning.

### Non-Flow Features

These are useful, but should not be confused with Flow:

- Instant Mix / track radio: "more like this track".
- Manual seed mix: "play something like these selected tracks".
- Text-prompt search: "find music matching this phrase".
- Genre lanes: browseable recommendation cards.
- Recommended playlists: finite editorial/algorithmic collections.

Flow can use some of their infrastructure, but it needs its own product model.

### Flow Product Model

Flow is:

- one persistent personal stream;
- launched from one primary dashboard card/button;
- driven by long-term taste plus recent behavior;
- adapted by likes, skips, plays, replays, and explicit dislikes;
- session-aware, so the next track depends on what happened in this listening
  run;
- not a static playlist, although it may expose a queue.

### Flow Engine Concepts

1. Taste model

Long-term representation of what the user tends to enjoy. This should likely be
multi-modal: several taste regions rather than one global average.

2. Session state

Short-term representation of the current listening run: tracks played, liked,
skipped, current energy, recent artist/album exposure, and whether the user is
accepting or rejecting the direction.

3. Candidate pool

A large set of plausible next tracks generated from taste regions, recent
positive tracks, and maybe exploration rules.

4. Reranker

Chooses the next track from candidates using taste fit, recent context, novelty,
diversity, fatigue, skips, and audio continuity.

5. Feedback loop

Every play/skip/like updates the session immediately and the long-term profile
eventually.

### Taste Regions

Cluster liked/high-play/recent tracks in embedding space, then choose one active
cluster for a Flow lane instead of averaging the whole user history.

Definition: a taste cluster is a group of tracks close to each other in vector
space. A cluster can be represented by a centroid vector, representative seed
tracks, dominant labels/genres, and audio traits such as BPM or energy. We call
these "regions" because they describe neighborhoods in the embedding space, not
hard music taxonomies.

Taste regions are internal engine state, not separate user-facing Flow cards.
The user should not need to choose a region in the normal Flow experience.

### Baseline To Test

Use starred/loved tracks as seeds, average their embeddings, and retrieve nearest
neighbors with existing artist/album caps.

This is useful as a measurable baseline, but not expected to be the final Flow.

### Better First Experiment

Use liked tracks, but split them into embedding neighborhoods first:

- Build clusters from starred tracks with embeddings.
- For each cluster, store centroid, representative tracks, top labels, and rough
  audio traits.
- Generate a Flow from one selected cluster at a time.
- Let the active cluster be chosen from recent listening, current track, or user
  selection.

This keeps Flow personal without flattening incompatible tastes into one vector.

### Technical Flow Design With Current Capabilities

What we can do now, before importing full listening history:

This plan targets the final product, not only a first MVP. The current app can
prototype pieces, but the product design should assume a first-party web player,
full settings, session state, and telemetry.

1. Taste source

- Use Navidrome starred/loved tracks as the positive seed set.
- Optionally include local recommendation feedback with high ratings.
- Later add play count, rating, recent listens, and first-party events.

2. Taste representation

- Baseline: one weighted taste vector.
- Better: multiple taste regions from starred/high-signal tracks.
- Store region centroids, member track ids, representative labels, and weights.

3. Candidate generation

- Query HNSW from several taste region centroids.
- Query HNSW from recently accepted tracks during the session.
- Merge and deduplicate candidates.

4. Reranking

- Positive factors: taste fit, active session fit, high similarity to accepted
  tracks, optional match to current energy/BPM range.
- Negative factors: already played in this session, recently skipped, repeated
  artist/album, too close to the previous track, too far from taste, too many
  familiar tracks in a row.
- Exploration factor: occasionally pick a lower-confidence but nearby track.

5. Session loop

- Generate an initial queue, but choose/refine the next item after each event.
- On like: reinforce current direction.
- On skip: penalize current track and nearby candidates for this session.
- On repeated skips: switch taste region or lower exploration.
- On long accepted run: allow slightly more discovery.

Settings can be exposed in a dedicated settings section. The UI entry point
should remain simple, but advanced controls are valuable because the primary
user is a power user.

### Possible Cluster Algorithm For First Version

Keep it simple and inspectable:

1. Collect seed tracks: starred tracks with embeddings, later recent positive
   plays and high ratings.
2. Normalize vectors. The app already stores normalized float32 embeddings.
3. Build clusters with a small, deterministic algorithm:
   - start with unassigned seeds;
   - pick a seed;
   - gather neighbors above a cosine similarity threshold;
   - form a cluster, including small clusters when they represent a real
     user taste pocket;
   - repeat until seeds are assigned.
4. Compute each centroid as the normalized average of vectors inside that
   cluster.
5. Describe the cluster from existing data:
   - representative tracks nearest to the centroid;
   - common artists/genres;
   - top Discogs head labels;
   - BPM/loudness/dynamic ranges when available.
6. Generate candidates from the centroid through HNSW.
7. Rerank candidates with:
   - similarity to active cluster centroid;
   - similarity to the current track if one exists;
   - novelty penalty for already starred or recently played tracks;
   - artist/album caps;
   - optional BPM/energy continuity;
   - negative feedback/skip penalty when available.

This avoids introducing heavier clustering dependencies early. If the simple
threshold approach is too crude, use k-means or HDBSCAN later.

### Region Neighbor Radius

Candidate radius must be tunable. "Gives neighbors outside the liked set" does
not mean "always goes far away"; it means the region has enough nearby tracks in
the library to continue listening beyond the original liked seeds.

Controls to expose internally, and maybe later in settings:

- `min_similarity`: lower cosine similarity bound to region centroid or seed.
- `candidate_count`: how many nearest HNSW candidates to request before rerank.
- `exploration_ratio`: how often Flow may choose a lower-similarity but still
  plausible candidate.
- `familiarity_mix`: balance between liked/familiar anchors and unliked
  neighbors.
- `region_focus`: stay tightly inside one region or allow adjacent regions.

Cosine similarity threshold is useful, but it should not be the only control.
A tight Flow can use high `min_similarity` and low exploration. A discovery
Flow can keep the same taste region but use a wider candidate radius.

### Centroid Definition

A centroid is the center vector of a group of tracks. It is not a real track; it
is a summary vector for the region.

For normalized embeddings:

1. take vectors for all tracks in the region;
2. optionally weight stronger positive signals higher;
3. average the vectors;
4. normalize the result back to unit length.

Then the centroid can be used as a query vector: "find tracks near this region
as a whole".

### Future First-Party Web Signals

The future web UI/player should store:

- track started,
- track played long enough to count,
- explicit skip,
- time-to-skip,
- completed play,
- replay,
- like/unlike,
- card impression,
- card click/play,
- card dismiss,
- save to playlist,
- Flow session id and active recipe.

These events make Flow trainable/evaluable instead of only "looks plausible".

### Region Quality Questions

Regions should be useful for next-track selection, not merely mathematically
neat. A good region:

- has tracks that sound coherent together;
- has enough member tracks to generate recommendations;
- is not automatically bad when small or artist-heavy, because the whole local
  45k-track library is already curated by the user;
- still exposes artist/album concentration so the Flow reranker can decide
  whether to diversify or intentionally stay focused;
- has representative tracks the user actually likes or accepts;
- has candidate neighbors outside the seed set;
- produces low skip rate in Flow sessions, which is an evaluation goal rather
  than a known property at build time.

We should evaluate regions with both offline and online signals:

- offline: cluster size, centroid similarity, representative track coherence,
  artist diversity, label/genre consistency, candidate coverage;
- online: skip rate, time-to-skip, completion rate, likes, repeated listens,
  region switches after consecutive skips.

Small regions should usually be kept. In a personal local library they may
represent narrow but real taste, especially when the collection contains only a
few tracks from an artist, compilation, label, or micro-style. Instead of
dropping them, mark their coverage and let the reranker decide how often to use
them.

### Region Description

Region descriptions are mainly for debugging, tuning, and future explainable UI.
The user may never see raw region objects, but we need to understand what Flow
is doing.

Useful fields:

- representative tracks: tracks closest to the centroid;
- positive seeds: liked/starred/completed tracks that formed the region;
- nearest candidates: unliked or not-recently-played neighbors near the centroid;
- artist and album concentration;
- metadata genres and years;
- top Discogs head labels and scores;
- audio ranges: BPM, key/scale distribution, loudness, dynamic complexity;
- region size and candidate coverage;
- later online metrics: skip rate, completion rate, like rate, average
  time-to-skip.

### Flow Queue Preparation

Flow should not prepare a fixed long playlist and blindly play through it.
Better shape:

1. Prepare a short visible buffer, default around 5 tracks.
2. Keep a larger candidate pool behind it.
3. After every event, update session state.
4. Re-rank or regenerate the next part of the queue.
5. If skips cluster around one region or trait, switch region or reduce that
   trait's weight for the session.

This lets the UI show a queue while still behaving like adaptive radio.

Visible buffer length should be configurable. A default around 5 keeps Flow
adaptive while still giving the user a sense of what is coming next.

The queue behavior should be:

- show the current playlist/queue source;
- keep 5 visible upcoming tracks by default;
- when one track is consumed, append one new track so the visible buffer returns
  to 5;
- if the user clicks another track already present in the playlist/queue, treat
  it as navigation, not as Flow feedback;
- feedback should come from explicit like/dislike and playback behavior such as
  skip, completion, replay, and time-to-skip;
- keep a larger hidden candidate pool below the visible queue.

Google Music style autoplay is a useful reference:

- queue/source at the top;
- autoplay toggle;
- upcoming tracks from the original source;
- after source queue ends, similar/autoplay tracks continue;
- preference chips such as familiar/recommended/party/energy/workout can shift
  the continuation;
- save generated queue/playlist action.

### Autoplay From Any Source

Autoplay should be a general playback feature, separate from the dashboard Flow
button.

Sources:

- track;
- album;
- playlist;
- search result;
- manual queue;
- Flow itself.

Concept:

- user plays a source;
- when the source queue approaches the end, autoplay appends matching tracks;
- the matching context is derived from the source, not necessarily from the
  user's full personal Flow;
- user can toggle autoplay on/off;
- user can shift autoplay preferences with chips/settings;
- clicking inside the current source queue is not negative feedback;
- skipping autoplay results can affect the autoplay session and optionally the
  long-term profile.

Flow and autoplay can share recommender infrastructure, but their product
contracts differ:

- Flow: "play my personal stream";
- Autoplay: "continue this source when it ends".

### Autoplay Design

Reference behavior from Google/YouTube Music style queues:

- source queue is visible first;
- autoplay is a toggle inside the queue/up-next panel;
- when the source queue runs out, similar tracks are appended;
- user can save the resulting queue/playlist;
- preference chips can shift the generated continuation;
- the queue may have tabs such as up next, lyrics/text, and related.

We should treat this as source-first continuation, not as Flow.

Core principle:

```text
Autoplay should mostly answer:
"what naturally continues this source?"

Flow should answer:
"what should I personally hear now?"
```

Autoplay can use personal taste as a bias, but it should not override the source.
If the user starts a dark ambient album, autoplay should continue that context
even if the user's global taste profile also contains peak-time techno.

### Autoplay Context By Source Type

Track source:

- strongest context is the source track;
- use source track global embedding;
- later use outro/current segment -> candidate intro/global segment for smoother
  continuation;
- personal taste is a light rerank bias.

Album source:

- context is album-level, not only the last track;
- use album centroid, album medoid, and best matching tracks;
- preserve album mood/region after it ends;
- avoid overreacting to one unusual track near the album end.

Playlist source:

- context is the playlist as a sequence and collection;
- use playlist centroid;
- use last N tracks for immediate direction;
- preserve playlist diversity and energy contour;
- if playlist is user-made, weight it as a stronger taste signal.

Manual queue source:

- infer context from queued tracks and recently played tracks;
- if queue is mixed, keep continuation broad and conservative;
- if queue is coherent, continue the dominant region.

Search/result source:

- use the query or result-set context if available;
- otherwise treat as manual queue.

Flow source:

- autoplay is usually redundant because Flow is already continuous;
- if used, it should simply keep Flow's active session running.

### Autoplay Candidate Generation

Recommended source-first candidate generation:

1. Build source context vectors:
   - source centroid;
   - recent played centroid;
   - optional ending/segment vector;
   - optional album/playlist aggregate.
2. Query HNSW from each context vector.
3. Merge candidates and remove duplicates/current source tracks.
4. Apply source-fit scoring.
5. Apply light personal-taste bias.
6. Apply queue rules: artist caps, album caps, recently played penalties,
   explicit dislikes/skips.
7. Fill visible queue back to configured size.

Do not use only a simple average embedding unless the source is short and
coherent. Average is a useful baseline, but source type should change how the
context is built.

### Autoplay Scoring

Suggested score:

```text
autoplay_score =
  source_similarity
+ recent_sequence_similarity
+ personal_taste_bias
+ preference_chip_bonus
+ audio_continuity_bonus
- repetition_penalty
- recent_play_penalty
- skip_or_dislike_penalty
- source_duplicate_penalty
```

Weighting principle:

- source similarity should be the strongest factor;
- recent sequence matters more for playlists/manual queues;
- personal taste should bias tie-breaks and remove bad fits;
- preference chips can deliberately shift the continuation;
- Flow/profile should not dominate unless the source is weak or ambiguous.

### Autoplay Preference Chips

Possible chips/settings:

- All.
- Familiar.
- Recommended.
- Discovery.
- Energy up.
- Calmer.
- Same vibe.
- More variety.
- Same artist/label.
- Less repeated artists.

Chips should modify weights, not switch to totally different algorithms.

Examples:

- Familiar: raise known/liked/replayed tracks.
- Recommended: balanced default.
- Discovery: lower familiarity, widen candidate radius.
- Energy up: prefer higher BPM/loudness/dynamic intensity.
- Calmer: prefer lower intensity.
- Same vibe: raise source similarity and lower exploration.
- More variety: reduce artist/album repetition and allow adjacent regions.

### Autoplay Queue Behavior

- Visible buffer default: 5 upcoming tracks.
- When source queue has 4 remaining visible tracks, append 1 to restore 5.
- If the user clicks another visible queue item, treat it as navigation.
- If the user skips an autoplay-generated track, update autoplay session state.
- Autoplay-generated tracks should be marked by source/session/generation.
- User should be able to save the generated queue as a playlist.

### Autoplay Research Notes

Public services do not expose exact autoplay algorithms. Observable patterns
from YouTube Music and similar services suggest:

- queue continuation is source-aware;
- "Up next" / queue UI separates the current queue from related/autoplay
  surfaces;
- user feedback actions such as like/dislike/save are prominent in now-playing;
- modern services increasingly sync queues across devices and treat queue state
  as first-class playback state.

Relevant references:

- YouTube Music now-playing/up-next UI reporting:
  https://www.androidcentral.com/apps-software/youtube/youtube-music-freshens-now-playing-with-dual-view-rework-next-songs-and-more
- YouTube Music queue sync reporting:
  https://www.androidcentral.com/apps-software/ive-waited-years-for-youtube-music-to-sync-my-queue-and-its-finally-here
- Track mix generation at Deezer:
  https://arxiv.org/abs/2307.03045

### Flow Session And State Storage

Recommended persistent model:

`flow_profiles`

- `id`
- `user_id`
- `version`
- `created_at`
- `updated_at`
- global settings snapshot or reference

`flow_regions`

- `id`
- `profile_id`
- `model_name`
- `centroid_blob`
- `weight`
- `member_count`
- `candidate_count`
- `label_summary_json`
- `audio_summary_json`
- `artist_concentration_json`
- `created_at`
- `updated_at`

`flow_region_members`

- `region_id`
- `track_id`
- `source`: liked, starred, completed, replayed, manual, etc.
- `weight`
- `similarity_to_centroid`

Flow uses generic playback session tables plus Flow-specific profile/region
tables. Do not create separate Flow-only queue/session tables unless a later
implementation proves the generic model insufficient.

`playback_sessions`

- `id`
- `user_id`
- `profile_id`
- `source_type`: flow, autoplay_track, autoplay_album, autoplay_playlist, etc.
- `source_id`
- `status`: active, paused, completed, abandoned
- `settings_json`
- `session_state_json`
- `started_at`
- `updated_at`
- `ended_at`

`queue_items`

- `id`
- `session_id`
- `track_id`
- `position`
- `state`: queued, visible, playing, played, skipped, removed
- `generation`
- `region_id`
- `score`
- `score_breakdown_json`
- `reason_json`
- `created_at`
- `played_at`

`playback_events`

- `id`
- `session_id`
- `track_id`
- `event_type`: started, progress, completed, skipped, liked, disliked,
  replayed, seeked, queued_click, removed, saved
- `position_seconds`
- `duration_seconds`
- `time_to_event_seconds`
- `source`: flow, autoplay, manual_queue, etc.
- `metadata_json`
- `created_at`

This separates:

- durable taste model;
- active session state;
- visible queue;
- raw telemetry for evaluation and future reranking.

`session_state_json` can cache fast-changing state such as recently played
artists, suppressed regions, skip streaks, current energy target, active
preference chips, and candidate pool ids. Raw events remain the source of truth.

### Playback Event Model

The first-party player should log raw playback events, not only final likes or
ratings. Flow quality depends on sequence-level behavior inside a listening
session.

Core event types:

- `track_started`: playback began.
- `progress`: periodic heartbeat while playing.
- `play_threshold_reached`: enough of the track played to count as a meaningful
  listen.
- `completed`: track reached the end or near-end.
- `skipped`: user skipped before meaningful completion.
- `queue_click`: user clicked a different visible queue item.
- `liked`: explicit positive feedback.
- `disliked`: explicit negative feedback.
- `replayed`: same track played again soon.
- `removed_from_queue`: explicit removal.
- `saved_to_playlist`: strong positive signal.
- `autoplay_toggled`: autoplay enabled/disabled.
- `preference_changed`: preference chip or setting changed during playback.

Important interpretation:

- `queue_click` is navigation, not negative feedback by itself.
- `skipped` strength depends on `time_to_skip` and played fraction.
- early skip is a strong negative signal for this track in this context.
- late skip can be neutral or weak negative.
- completion is weak positive.
- replay/save/like are strong positive.
- repeated early skips in a neighborhood or region can suppress that direction
  for the current session.

Useful event fields:

- `session_id`
- `track_id`
- `event_type`
- `source_type`: flow, autoplay, album, playlist, search, manual_queue
- `source_id`
- `queue_item_id`
- `position_seconds`
- `duration_seconds`
- `played_fraction`
- `time_to_event_seconds`
- `is_visible_queue_item`
- `region_id`
- `candidate_generation`
- `score`
- `score_breakdown_json`
- `preference_state_json`
- `created_at`

Suggested threshold logic:

- count as early skip: before 30 seconds or before 25 percent of duration.
- count as meaningful listen: at least 60 seconds or at least 50 percent.
- count as completion: at least 90 percent or playback naturally ended.

These thresholds should be settings. Long electronic tracks may need different
defaults than short pop tracks.

### Event Interpretation Levels

Feedback should update several levels with different strength:

1. Track level

The safest interpretation. A skip first penalizes the specific track for the
current session. Repeated skips across sessions can lower long-term track weight.

2. Neighborhood level

Early skips can lightly penalize very close embedding neighbors for the current
session, especially if several skipped tracks are close to each other.

3. Trait level

If skipped tracks share BPM range, loudness, head label, artist, or other traits,
temporarily penalize those traits in the session.

4. Region level

Only suppress a taste region after a pattern, such as repeated early skips from
that region without accepted/completed tracks. Do not treat one skip as evidence
that the whole region is bad.

This matches the research direction in session-based music recommendation:
negative feedback is noisy and contextual, and should be modeled in sequence.

### Flow Quality Evaluation

Offline evaluation before real usage:

- candidate coverage per region;
- average similarity to region centroid;
- diversity by artist/album/label/year;
- proportion of already-liked/familiar vs new tracks;
- region balance over generated queues;
- repeated artist distance;
- audio continuity: BPM/loudness/dynamic jumps;
- explanation sanity: reasons match actual scoring factors.

Online/session metrics:

- skip rate;
- early skip rate, for example skip before 30 seconds or before 25%;
- completion rate;
- like/dislike rate;
- replay rate;
- average time-to-skip;
- number of consecutive skips;
- manual queue jumps;
- session length;
- tracks played before abandonment;
- source-to-autoplay retention;
- region suppression/switch success after skips.

User-facing evaluation:

- "save queue/playlist" frequency;
- repeated use of Flow as start-page entry;
- preference chip usage;
- manual setting changes;
- tracks added to library/playlists.

Important interpretation:

- clicking another already visible queue item is navigation, not necessarily a
  negative signal;
- an early skip is stronger negative feedback than a late skip;
- completion without like is weak positive feedback;
- replay or save is strong positive feedback;
- several skips in one region should suppress the region for the session, not
  delete it from long-term taste.

### Long-Term Taste And Short-Term Session Behavior

Long-term taste:

- built from stable signals such as likes, stars, high ratings, repeat listens,
  completed plays, and later imported/user play counts;
- changes slowly;
- decides which taste regions exist and how important they are;
- prevents Flow from overreacting to one accidental skip.

Short-term session behavior:

- built from the current listening session;
- changes after every track event;
- includes recent plays, likes, skips, completion, time-to-skip, repeated
  artists/albums, and current audio direction;
- decides the next few tracks;
- can temporarily suppress a region after repeated skips without deleting it
  from long-term taste.

Flow should combine both:

- long-term taste says "this is generally your music";
- short-term behavior says "not this exact direction right now".

### Additional Track Analyses That May Help Flow

Already available:

- Discogs-EffNet embeddings.
- MuQ-MuLan text/music embeddings.
- BPM.
- Key and scale.
- Loudness.
- Dynamic complexity/loudness.
- Discogs head predictions.
- Metadata genre/year/artist/album.

Useful additions:

- Better energy/intensity estimate.
- Danceability/groove estimate.
- Vocal/instrumental estimate.
- Mood/valence/arousal tags.
- Main genre/style probabilities from multiple heads.
- Intro/outro or onset density if we later care about transitions.
- Replay/familiarity score from user events.
- Freshness/fatigue score from recent plays.

For first Flow versions, embeddings + BPM + loudness/dynamics + head labels are
probably enough. The most important missing ingredient is not another audio
feature; it is first-party interaction data, especially skip and completion.

### Embedding Storage And Future Models

Flow quality depends on the base track representation. A single pooled/global
embedding is useful, but it can hide structure inside long electronic tracks:
intro, main section, break, peak, outro.

For the current Discogs-EffNet models, add segment-level embeddings:

- keep existing global/pooled embeddings;
- add segment embeddings for the same model;
- store segment start/end time;
- store pooling/segment strategy metadata;
- use global vectors for fast candidate generation;
- use segment vectors for reranking, transition quality, and diagnostics.

Suggested storage shape:

`track_embeddings`

- `track_id`
- `model_name`
- `pooling`: global, mean, max, etc.
- `dim`
- `vector_blob`
- `vector_norm`
- `created_at`

`track_embedding_segments`

- `track_id`
- `model_name`
- `segment_index`
- `start_seconds`
- `end_seconds`
- `dim`
- `vector_blob`
- `vector_norm`
- optional segment stats such as energy/loudness
- `created_at`

Keep this compatible with multiple model families and segment strategies.

### MAEST

MAEST should be added later as an additional heavy model, not as a replacement
for the existing Discogs-EffNet stack.

Rationale:

- Discogs-EffNet remains the practical baseline and existing recommender space.
- MAEST is much larger and likely more expensive to analyze.
- MAEST may provide a stronger or different Discogs/style representation.
- It should be A/B tested against existing embeddings rather than assumed better.

Storage:

- MAEST should use a separate embeddings database or storage file because of
  model size and expected data volume.
- Keep metadata and user/session data in the main app database.
- Keep HNSW indexes separate per model.

Candidate layout:

- `data/app.db`: tracks, metadata, sessions, playback events, Flow state.
- `data/embeddings.db`: current Discogs-EffNet global and segment embeddings.
- `data/maest_embeddings.db`: MAEST embeddings and segments.
- `data/index_*_hnsw.bin`: per-model HNSW indexes.

### Analysis Jobs For Embeddings

Implementation should be one parameterized analysis pipeline, even if the web UI
shows several analysis cards.

Conceptual job:

```text
analyze-embeddings
model = discogs_multi | discogs_track | discogs_artist | discogs_label | maest
mode = global | segments | both
segment_seconds = 30
pooling = mean | max | ...
```

The dashboard can expose this as multiple cards for clarity, for example one
card per Discogs model plus MAEST later. Internally these should share the same
job logic.

Current global embedding extraction already exists; segment analysis should
reuse the same model loading and vector normalization path where possible.

### Open Questions

- Should Flow be infinite radio, a refreshable card, or a generated playlist?
- Should the first version be based on current track, starred tracks, recent
  history, or explicit user-selected seeds?
- How much should Flow optimize for similarity versus discovery?
- Should Flow avoid already played tracks, or include familiar anchors?
- Do we want separate Flow modes: "nearby", "discovery", "energy up",
  "deeper", "recent mood"?
- What Navidrome data can we reliably import in the current deployment?
- Should Flow state live only in the browser/session, or be stored server-side?

### Product Notes

A good Flow card should explain itself briefly:

- "Continuing from this track"
- "From your recent electro/techno listens"
- "Near your liked tracks, fewer repeated artists"
- "Faster, brighter picks from the same neighborhood"

The explanation matters because this project is evaluating recommendation
quality, not hiding it behind a black box.

### Research Notes

Playlist continuation research suggests that pure content similarity is useful
but often weaker than hybrid approaches that include playlist/user behavior.
For this local MVP, we can approximate a hybrid system by combining:

- audio/content embeddings,
- metadata and audio features,
- Navidrome user signals,
- explicit feedback from our UI.

Recent sequential recommendation work also treats skips/negative feedback as
important. We do not need that full model now, but it supports collecting
negative feedback early.

Relevant references:

- Navidrome Scrobbling:
  https://www.navidrome.org/docs/usage/features/scrobbling/
- Navidrome Smart Playlists:
  https://www.navidrome.org/docs/usage/features/smart-playlists/
- Hybrid playlist continuation:
  https://arxiv.org/abs/1805.09557
- Sequential music recommendation with negative feedback:
  https://arxiv.org/abs/2409.07367
- Large-scale user modeling over multiple time scales:
  https://arxiv.org/abs/1708.06520
- Spotify sequential skip prediction:
  https://arxiv.org/abs/1902.04743
- Skip prediction with acoustic/session features:
  https://arxiv.org/abs/1903.11833

Important research takeaways for our Flow:

- Playlist/radio continuation is not only "nearest songs to a user vector"; it is
  a fit problem between context and candidate track.
- Collaborative/user behavior signals are valuable, but this local app has sparse
  user data, so content embeddings remain important.
- A hybrid approach is realistic here: use embeddings for candidate generation,
  then rerank with user signals, metadata, audio features, and diversity rules.
- Negative signals such as skips/dismissals are worth storing early even if the
  first version only uses them as filters or penalties.
- Navidrome already exposes playlist-like user fields such as loved, dateLoved,
  lastPlayed, playCount, and rating in Smart Playlist rules; these are exactly
  the kind of fields Flow needs.
- Session-based skip-prediction work supports treating skip behavior as a
  sequence problem: the previous accepted/skipped tracks in the current session
  matter, not just the user's global profile.
- Multi-timescale user modeling supports keeping both long-term taste and
  short-term session state.

### Decisions

- 2026-06-19: Create this plan and focus discussion on Flow first.
- 2026-06-19: Treat "average all likes/listens into one embedding" as a baseline,
  not as the target design.

## Dashboard Concept

The future start page should feel like a music-hosting dashboard: one strong
personal entry point plus horizontal recommendation shelves.

Top priority:

- Flow as a large, attractive primary button/card above the shelves.
- Flow should read as the main daily action: press once and listen.

Shelf model:

- each shelf has a title, horizontal cards, left/right navigation, and optional
  "more" action;
- shelves can contain tracks, albums, playlists, mixes, or generated collections;
- cards should be visually rich and direct, with cover art first;
- recommendation logic may differ per shelf, but the layout pattern stays
  consistent.

Candidate shelves:

- Listen Again / Poslushat eshche raz.
- Long Time No Listen / Davno ne slushal.
- Mixes For You.
- Albums For You.
- Recently Added / New In Collection.
- From Your Regions.
- Similar To Recent.
- Energy / Mood shelves.
- Label, genre, or era shelves.

### Listen Again

Purpose: quick return to things the user already likes or recently accepted.

Signals:

- completed plays;
- replayed tracks/albums/playlists;
- liked/starred tracks;
- saved queues/playlists;
- recent sessions with good completion.

This shelf is relatively simple once first-party playback events exist.

### Long Time No Listen

Purpose: resurface good music that has fallen out of rotation.

Signals:

- liked/starred or historically completed tracks;
- high play count in the past;
- no recent plays for a long time;
- optional freshness/fatigue score.

This is also straightforward after playback history exists. It should not only
show random old tracks; it should combine oldness with positive affinity.

### Mixes For You

These are finite generated collections, not Flow.

Possible mechanism:

- choose distant taste regions from the user's profile;
- generate one mix per region or per region-combination;
- keep mixes diverse from each other;
- describe each mix by representative artists, labels, genres, and audio traits;
- allow "more" to show the full mix catalog.

Examples:

- region-focused mix;
- cross-region bridge mix;
- familiar favorites mix;
- discovery-near-region mix;
- energy or mood mix.

Mixes can use the same taste regions as Flow, but they are surfaced as discrete
playlists/cards.

Product definition:

- each mix is a finite playlist;
- default length: 100 tracks;
- each mix represents a different part of the user's taste profile;
- examples: rock-like region, psytrance region, drum and bass region, ambient
  region, etc.;
- mixes should be different from each other, even if the user's profile is
  concentrated in one broad region.

If taste regions are far apart, create mixes from different regions. If the
profile is dense in one region, choose separated anchor points inside that
region so the mixes still feel distinct.

Mix generation approach:

1. Build candidate anchors:
   - major taste regions;
   - sub-region centroids;
   - representative liked tracks;
   - high-quality clusters from recent completed sessions.
2. Select mix anchors with diversity:
   - maximize distance between mix anchors;
   - avoid same artist/album dominance across mixes;
   - prefer anchors with enough candidate coverage.
3. Generate 100-track playlists per anchor:
   - use HNSW candidates around the anchor;
   - bias toward user's positive signals;
   - include both familiar and discovery tracks;
   - apply artist/album caps;
   - keep region coherence.
4. Deduplicate across mixes:
   - avoid showing the same track in many mixes;
   - allow strong favorites in a "supermix", but avoid repeating them everywhere.

Suggested mix types:

- Region Mix: one coherent taste region.
- Subregion Mix: distinct points inside a dense region.
- Discovery Mix: near a region, less familiar.
- Familiar Mix: liked/replayed/completed tracks plus close neighbors.
- Bridge Mix: connects two related regions.
- Supermix: broad mix across strongest user regions.

Mix update cadence:

- regenerate daily or weekly depending on settings;
- refresh a mix after significant new listening history;
- refresh after enough likes/skips/completions change region weights;
- keep old saved mixes stable once user saves them;
- optionally keep generated unsaved mixes as rolling recommendations.

Suggested defaults:

- length: 100 tracks;
- dashboard visible mixes: 6;
- update cadence: daily for dynamic users, weekly for stable library mode;
- familiarity mix: 40-60 percent familiar/positive-nearby, rest discovery;
- max tracks per artist: configurable, default 3-5 per 100-track mix;
- max tracks per album: configurable, default 2-3;
- cross-mix duplicate limit: low, unless supermix is enabled.

Settings:

- number of mixes shown;
- tracks per mix;
- update cadence: manual, daily, weekly, after N events;
- familiarity/discovery ratio;
- region spread: tighter or more diverse;
- allow small regions;
- max per artist;
- max per album;
- allow already liked tracks;
- allow recently played tracks;
- cross-mix duplicate strictness;
- include/exclude source types: liked, completed, starred, local feedback.

Quality checks:

- mixes should have clear identity;
- mixes should be meaningfully different from each other;
- each mix should have enough candidate coverage;
- avoid one artist dominating all mixes;
- skip/completion/like rates should be measured per mix and per anchor.

### Albums For You

Albums probably need their own aggregate representation, not only track-by-track
recommendations.

Possible album representation:

- album centroid: normalized average of track embeddings in the album;
- weighted album centroid: weight standout/popular/liked tracks higher;
- album region coverage: which taste regions the album overlaps;
- album audio summary: BPM/loudness/dynamic ranges;
- album label/genre/head summary;
- album familiarity: played/liked/completed count per album.

Recommendation approaches:

1. Album centroid matching

Compare album centroid to user taste regions or recent session context.

2. Track evidence aggregation

Score album by how many tracks are strong candidates, not just by centroid.
This helps albums with varied tracks.

3. Hybrid album score

Combine album centroid similarity, best-track similarity, region coverage,
familiarity, freshness, and diversity.

Album embeddings do not need a separate model initially. They can be derived
from existing track embeddings and stored/cached as album-level aggregates.

Useful album tables later:

- `albums` or album identity table if metadata is normalized;
- `release_embeddings`;
- `album_stats`;
- `album_recommendation_cache`.

Album recommendation should be evidence-based, not only centroid-based.

Important nuance: an album is a package, not just one item. It may be cohesive,
varied, an EP, a single, a compilation, a soundtrack, or a remix collection.
Therefore a single average embedding is useful but insufficient.

Recommended album signals:

1. Album centroid

- normalized average of track embeddings;
- useful for quick similarity to taste regions;
- weak for varied albums, compilations, and soundtracks.

2. Best-track evidence

- identify top album tracks that strongly match user taste regions;
- require more than one good track when recommending a full album;
- if only one track matches, it may be better as a track recommendation.

3. Region coverage

- count how many album tracks overlap each taste region;
- distinguish single-region albums from bridge albums;
- use this for both ranking and explanation.

4. Album behavior

Future player events should produce album-level stats:

- album started;
- tracks completed inside album;
- skips inside album;
- album completion depth;
- album saved/liked;
- individual tracks liked before album was recommended;
- repeated return to the album.

Suggested album score:

```text
album_score =
  centroid_similarity
+ top_tracks_similarity
+ matching_track_count
+ region_coverage
+ user_album_affinity
+ freshness_or_forgotten_bonus
- recently_played_penalty
- too_many_skipped_tracks_penalty
- length_bias_penalty
```

Album aggregates to precompute:

- `release_centroid_embedding`;
- `album_medoid_track_id`: real track closest to album centroid;
- `album_top_region_matches`;
- `album_audio_summary`;
- `album_head_label_summary`;
- `album_track_count`;
- `album_duration`;
- `album_artist_concentration`;
- `album_play_stats`.

Album recommendation nuances:

- long albums should not win only because they have more tracks;
- normalize evidence by album length;
- EPs and short releases should remain eligible;
- compilations may need best-track and region-coverage scoring more than
  centroid scoring;
- artist-heavy albums are normal and should not be filtered by default;
- if many tracks from an album were already liked/completed, that is strong
  evidence for recommending the album;
- if a user repeatedly skips tracks from an album, lower the album score;
- explain recommendations with concrete reasons, such as "you liked 3 tracks",
  "near your electro region", or "long time since last played".

### Dashboard Settings

Because the target user is a power user, dashboard and recommender settings can
be exposed in a dedicated settings section.

Potential settings:

- visible shelf count;
- visible queue size for Flow/autoplay;
- candidate pool size;
- exploration ratio;
- familiarity mix;
- shelf enable/disable;
- region focus;
- album recommendation weights;
- history windows for "listen again" and "long time no listen".

### Recently Added

Recently Added should stay simple.

Purpose:

- show newly added library items, newest first;
- mirror familiar Navidrome-style recently added behavior;
- help inspect what entered the collection and whether it is analyzed.

Content:

- tracks, albums, or both depending on view;
- sorted by added/scanned timestamp descending;
- no recommender scoring required;
- optional status badges: analyzed, missing embeddings, missing cover, lost file.

This shelf is operationally useful but should not pretend to be personalized
recommendation.

## Search Page

Search should be a full reactive page, not only a small overlay. Deezer-style
search is a good reference.

Layout:

- search input at the top;
- tabs: All, Artists, Tracks, Albums, Playlists;
- results update while typing;
- Top Result block for the best match;
- sections below for entity types;
- track results use a dense table/list;
- albums/playlists/artists use visual cards.

Reactive behavior:

- on input focus or first character, navigate to/search within the results page;
- debounce typing;
- keep URL state, for example `/search?q=juno%20reactor`;
- update results without interrupting the current player;
- keyboard navigation can be added later.

All tab:

- Top Result card;
- Artists row;
- Tracks list preview;
- Albums row;
- Playlists row.

Tracks tab:

- dense table/list;
- columns: title, artist, album, duration, liked/status/actions;
- optional analysis/status columns in advanced mode.

Albums tab:

- cover grid;
- title, artist, year, track count, duration;
- optional match reason later.

Artists tab:

- artist cards;
- top tracks and albums reachable from artist page.

Playlists tab:

- user playlists;
- saved generated mixes;
- saved queues.

Search ranking:

- exact text matches first;
- artist/title/album/path metadata;
- liked/starred/local play history can boost;
- later semantic/text embedding search can be a separate mode or blended result.

## Web App Layout

The target product is a first-party web music app, not only an API/UI prototype.
Screenshots are references only; the plan should describe the web structure in
text.

Detailed visual/UI specification lives in:

- `plans/web-ui-visual-spec.md`

Backend/data model overview lives in:

- `plans/data-model-overview.md`

Implementation roadmap lives in:

- `plans/implementation-roadmap.md`

Main shell:

- dark music-app layout;
- left sidebar navigation;
- central content area;
- persistent bottom player;
- expandable player/queue view;
- settings as a structured page with tabs.

Left sidebar:

- Home;
- Navigator / Browse;
- Library;
- Search access if not always visible;
- New playlist/action button;
- user playlists and saved mixes;
- scrollable playlist list.

Home/dashboard:

- large Flow entry card/button at the top;
- horizontal shelves below;
- shelves use cover-first cards with title/subtitle;
- shelf navigation with left/right controls;
- "more" action where useful;
- Recently Added is simple newest-first;
- recommendation shelves remain visually consistent even when their logic
  differs.

Search:

- dedicated route/page;
- reactive results while typing;
- tabs for entity types;
- top result and grouped sections;
- dense track table for track results;
- visual grids/rows for albums, artists, playlists.

Player:

- collapsed bottom bar by default;
- expanded view opens from the right-side arrow;
- expanded view shows large artwork and queue panel;
- queue panel includes Up Next, Lyrics/Text, Related tabs;
- autoplay controls and preference chips live in the queue panel;
- bottom player persists while navigating across app pages.

Settings:

- separate top-level page;
- tabs across the top or another clear section navigation;
- no mixed single long settings page;
- power-user controls are acceptable because this product is for the owner first.

## Player Layout

The player should be persistent at the bottom of the app.

Collapsed bottom player:

- previous/play-next controls on the left;
- progress bar;
- current cover/title/artist/year in the center;
- like/dislike and menu actions;
- volume, repeat, shuffle/autoplay controls on the right;
- arrow/expand control on the far right.

Expanded player:

- opens from the right-side arrow;
- large cover/current track area;
- queue panel on the right;
- tabs in queue panel: Up Next, Lyrics/Text, Related;
- autoplay toggle and preference chips inside queue panel;
- generated/autoplay tracks visually separated from source queue;
- save queue/playlist action.

Queue behavior:

- bottom player remains available across dashboard/search/library/settings;
- clicking a queue item is navigation, not negative feedback;
- skip buttons and explicit dislike are feedback;
- expanded player should expose enough information to debug why autoplay/Flow is
  continuing a direction.

## Settings Structure

Settings should be organized into tabs/sections instead of one mixed page.

Suggested tabs:

- General.
- Library and Scan.
- Analysis.
- Embeddings and Models.
- Flow.
- Autoplay.
- Mixes.
- Albums.
- Dashboard.
- Player.
- Storage.
- Advanced / Debug.

General:

- app URL/base settings;
- theme/layout basics;
- default page.

Library and Scan:

- music folders;
- scan behavior;
- missing/lost file handling.

Analysis:

- worker settings;
- job defaults;
- audio feature analysis;
- head predictions.

Embeddings and Models:

- Discogs-EffNet models;
- segment embedding settings;
- MAEST storage/settings;
- index rebuild controls.

Flow:

- visible queue size;
- candidate pool size;
- exploration ratio;
- familiarity mix;
- region focus;
- skip/time-to-skip thresholds;
- long-term vs session weighting.

Autoplay:

- enabled by default;
- visible buffer size;
- source-vs-personal weighting;
- preference chip defaults;
- max per artist/album;
- candidate pool and exploration.

Mixes:

- number of dashboard mixes;
- tracks per mix;
- update cadence;
- region spread;
- familiar/discovery ratio;
- duplicate strictness.

Albums:

- album recommendation weights;
- EP/single handling;
- compilation handling;
- history windows;
- length normalization.

Dashboard:

- shelf enable/disable;
- shelf ordering;
- card density;
- items per shelf.

Player:

- progress event frequency;
- completion/meaningful listen thresholds;
- queue behavior;
- default expanded/collapsed state.

Storage:

- database paths;
- embeddings database;
- MAEST database;
- cache sizes;
- cleanup controls.

Advanced / Debug:

- show score breakdowns;
- show regions;
- show candidate pools;
- export diagnostics.

### Next Discussion Step

Decide the first Flow shape to prototype conceptually:

- current-track radio,
- recent-session flow,
- liked-cluster flow,
- or multi-lane flow.
