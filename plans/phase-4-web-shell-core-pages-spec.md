# Phase 4 Spec: Web Shell And Core Pages

## Purpose

Build the future web UI shell and core pages against API-first backend
contracts.

Phase 4 is not a recommender-quality phase. It is the product surface that makes
the library, search, artist pages, release pages, and player feel like one
coherent app.

## Current State

Current UI is a prototype inside the FastAPI app.

Known target direction:

- future web may use another stack;
- backend should expose stable `/api/v1`;
- current prototype can coexist;
- final web should be component/page structured;
- player should persist across navigation;
- search and detail pages should use entity APIs;
- settings should be tabbed, not one mixed page.

## Implementation Strategy

Decision:

- stabilize `/api/v1` first, then build the new frontend shell against it.

Phase 4 should not require rewriting backend data logic. It should consume:

- Phase 2 entity APIs;
- Phase 3 playback/session APIs;
- existing audio/cover endpoints or their `/api/v1` wrappers.

If the current FastAPI HTML remains useful operationally, keep it. The target UI
should not depend on SQLite internals or old `/tracks` response shapes.

## App Shell

Desktop layout:

- fixed left sidebar;
- vertically scrollable main content;
- fixed bottom player;
- expanded player opens above/over main content;
- dark UI, dense but readable;
- desktop is primary target.

Approximate dimensions:

- sidebar width: 210-240px;
- bottom player height: 64-80px;
- content padding: 24-40px;
- shelf spacing: 36-56px.

Sidebar:

- Home;
- Search or Navigator;
- Library;
- Settings;
- New/Add action where useful;
- optional playlist list later.

Sidebar should not become a giant browse tree. Navigation mostly comes from
dashboard, search, entity pages, and player queue.

## Routes

Initial web routes:

- `/`
- `/search`
- `/artists/:artistId`
- `/releases/:releaseId`
- `/settings`

Future routes:

- `/playlists/:playlistId`
- `/mixes/:mixId`
- `/flow`
- `/library`

No standalone track page:

- tracks are shown inside release, playlist, queue, search result, or generated
  mix contexts.

## Dashboard Skeleton

Purpose:

- provide the first screen shell before advanced dashboard shelves exist.

Phase 4 dashboard can include:

- prominent Flow card placeholder;
- Recently Added placeholder/shelf if Phase 5 API is not ready;
- Search entry;
- basic operational status link;
- empty/loading/error states.

Real dashboard shelves belong to Phase 5.

## Search Page

Source API:

- `GET /api/v1/search`.

Layout:

- search input at top;
- tabs under input: All, Artists, Tracks, Albums/Releases, Playlists later;
- results update reactively while typing;
- URL keeps query state, for example `/search?q=juno%20reactor`;
- current player is not interrupted by search navigation.

All tab:

- top result card;
- artists row/grid;
- tracks table;
- releases row/grid.

Single-type tabs:

- show larger paginated result list for selected type.

States:

- empty query: show idle empty grouped state;
- loading: preserve previous results or show skeletons;
- no results: clear empty state;
- API error: non-blocking error message, player still usable.

## Release Page

Source APIs:

- `GET /api/v1/releases/{id}`;
- `GET /api/v1/releases/{id}/tracks`;
- `GET /api/v1/releases/{id}/related-discography`;
- `GET /api/v1/releases/{id}/recommendations`.

Layout:

- large square cover left;
- large release title right;
- all participating artists shown under title as links;
- metadata row: track count, duration, date/year if known;
- actions: Play, Like/Save, overflow;
- track table below;
- related discography below tracks;
- recommended albums below related discography when available;
- similar artists lower if available later.

Rules:

- unknown release type displays as generic Release/Releases;
- no tour module;
- no prominent label module for now;
- missing cover uses placeholder.

Track rows:

- track number;
- title;
- artists;
- duration;
- like/action controls;
- active row highlight;
- clicking a track starts playback session from release context.

## Artist Page

Source APIs:

- `GET /api/v1/artists/{id}`;
- `GET /api/v1/artists/{id}/discography`;
- `GET /api/v1/artists/{id}/top-tracks`;
- `GET /api/v1/artists/{id}/similar`.

Layout:

- large circular artist image/avatar left;
- large artist name;
- local stats under name;
- actions: Mix, Like/Save, overflow;
- tabs/anchors: Discography, Top Tracks, Similar Artists, Playlists, Bio;
- discography is primary content.

Rules:

- do not show fake fan counts;
- omit Top Tracks if `available: false`;
- omit Similar Artists if unavailable/low confidence;
- unknown release types go under Releases;
- Featured In is separate from artist-owned releases.

## Bottom Player

Persistent across pages.

Collapsed layout:

- left: previous, play/pause, next;
- progress bar;
- center: cover thumbnail, title, artist, release/year;
- like/dislike and menu actions;
- right: volume, repeat, shuffle/autoplay controls;
- far right: expand arrow.

Behavior:

- player state comes from Phase 3 playback session API;
- audio can stream from existing audio endpoint;
- progress events are sent through playback events API;
- navigation does not reset player state.

## Expanded Player

Desktop layout:

- large artwork/current track area on the left/center;
- queue panel on the right;
- bottom player remains visible or integrated;
- background stays dark and focused.

Queue panel:

- tabs: Up Next, Lyrics/Text, Related;
- source label at top;
- autoplay toggle;
- save queue/playlist action;
- current source queue and autoplay/generated items visually separated;
- preference chips for autoplay later.

Rules:

- clicking queue item is navigation, not negative feedback;
- skip/dislike are feedback;
- debug/reason fields can appear only in advanced/debug mode.

## Settings Page

Use tabs or top segmented navigation.

Tabs:

- General;
- Library and Scan;
- Analysis;
- Embeddings and Models;
- Flow;
- Autoplay;
- Mixes;
- Albums;
- Dashboard;
- Player;
- Storage;
- Advanced / Debug.

Phase 4 minimum:

- render the tabbed settings structure;
- move existing settings into logical sections where available;
- reserve placeholders for future recommender settings;
- avoid one huge mixed settings page.

## Component Inventory

Core components:

- `AppShell`;
- `Sidebar`;
- `TopNav` or page header;
- `Dashboard`;
- `SearchPage`;
- `SearchTabs`;
- `TopResult`;
- `MediaCard`;
- `ArtistCard`;
- `ReleaseCard`;
- `TrackTable`;
- `ReleasePage`;
- `ArtistPage`;
- `BottomPlayer`;
- `ExpandedPlayer`;
- `QueuePanel`;
- `SettingsPage`;
- `SettingsTabs`;
- `LoadingState`;
- `EmptyState`;
- `ErrorState`.

## State Management

Required state:

- current route;
- search query/results;
- active playback session;
- queue;
- player UI state: collapsed/expanded;
- settings tab;
- entity page cache optional.

Rules:

- player state is global/persistent;
- page data can reload independently;
- search updates should not interrupt playback;
- errors in page content should not break bottom player.

## API Dependencies

Phase 4 consumes:

- Phase 2 search/entity APIs;
- Phase 3 playback/session/queue/events APIs.

Phase 4 should not require:

- Flow engine;
- generated mixes;
- release recommendation scoring;
- segment embeddings;
- MAEST.

Where later APIs are unavailable:

- render placeholders;
- omit unavailable shelves/sections;
- use `available: false` contracts.

## Visual QA Requirements

Before considering a page done:

- no overlap with bottom player;
- main content has bottom padding so last rows are not hidden;
- expanded player opens/closes without layout jump;
- search input and tabs remain readable;
- release page cover/title/track table align on desktop;
- artist page header and discography fit without clipping;
- settings tabs are reachable and not collapsed into an unreadable row.

## PR Slices

Keep Phase 4 focused on core UX, not every future page.

### Slice 1: App Shell And Routing

Goal:

- create the stable layout skeleton.

Includes:

- app shell;
- sidebar;
- route structure;
- page content outlet;
- fixed bottom player placeholder;
- global loading/error primitives.

Tests/QA:

- dashboard/search/settings routes render;
- bottom player does not cover content;
- navigation preserves shell.

### Slice 2: Search Page

Goal:

- build the reactive search experience.

Includes:

- search input;
- tabs;
- grouped All view;
- artist/release cards;
- track table;
- URL query state;
- loading/empty/error states.

Tests/QA:

- typing updates results;
- empty query shows idle state;
- result clicks navigate to artist/release or start playback for track;
- player remains active.

### Slice 3: Release And Artist Pages

Goal:

- build core entity detail pages.

Includes:

- release page header;
- release track table;
- related discography shelf;
- artist page header;
- artist discography groups;
- unavailable top-tracks/similar sections omitted.

Tests/QA:

- multi-artist release shows all artists;
- unknown release type appears as Releases;
- artist Featured In grouping appears separately;
- track click starts playback session from release context if Phase 3 API is
  available.

### Slice 4: Player UI

Goal:

- connect persistent player UI to Phase 3 APIs.

Includes:

- bottom player;
- expanded player;
- queue panel;
- progress reporting;
- queue click handling;
- like/dislike/skip controls.

Tests/QA:

- player persists across routes;
- expanded view opens/closes;
- queue click does not emit skip;
- explicit skip emits skip event;
- progress/completion events are sent.

### Slice 5: Settings Structure

Goal:

- replace mixed settings with structured tabs.

Includes:

- tabbed settings page;
- existing operational settings mapped into logical tabs;
- placeholders for Flow/Autoplay/Mixes/Albums/Dashboard/Player;
- Advanced/Debug area.

Tests/QA:

- tabs switch without losing unsaved form state unexpectedly;
- existing settings remain reachable;
- page remains dense and readable.

Recommended grouping:

- PR 1: Slice 1.
- PR 2: Slice 2.
- PR 3: Slice 3.
- PR 4: Slice 4.
- PR 5: Slice 5.

Reason:

- shell first;
- search and detail pages are distinct workflows;
- player UI touches global state and deserves its own review;
- settings can land last without blocking core navigation.

## Open Decisions

No blocking decisions for this spec.

Known future visual questions remain in the visual spec:

- exact accent color;
- expanded player as route vs drawer vs overlay;
- Flow card visual treatment;
- mobile layout strategy.

Recommended default for Phase 4:

- expanded player as overlay/drawer above the app shell, not a separate route.
