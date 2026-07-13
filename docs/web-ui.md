# Web UI

This document describes the first-party web UI in `ui/src/`, a React SPA
served at `:5173` in dev (`ui/vite.config.ts` proxies `/api`, `/health`, and
`/admin` to the FastAPI backend on `:8711`) and mounted at `/app` in
production. It is a separate surface from the legacy operational admin at
`app/ui.html` (`:8711/admin`) — see the UI Rule in `CLAUDE.md` for which one
to edit.

Stack: React 19 + `react-router` (data router, `ui/src/router.tsx`),
TanStack Query for data fetching, Zustand for player/UI state, Tailwind v4
with CSS custom properties for theming, shadcn/ui primitives under
`ui/src/components/ui/`.

## App shell & navigation

`AppShell` (`ui/src/components/layout/AppShell.tsx`) is the top-level layout,
mounted once by the router around every authenticated route (`RequireAuth` ->
`AppShell` -> page `Outlet`). It renders, in a single fixed-height
(`h-svh`) flex column:

- a full-bleed animated plasma background (`PlasmaFBM`, WebGL via `ogl`)
  tinted by the current track's accent color;
- a desktop sidebar (`Sidebar`, hidden below the `md` breakpoint) plus a
  scrollable `<main>` containing the page header (`AppHeader`) and the routed
  page content;
- the persistent player: `PlayerBar` (collapsed bar) and `ExpandedPlayer`
  (full-screen overlay), both always mounted so player state and audio
  playback survive route changes.

`AppShell` also owns a few app-wide effects: restoring the playback session
on load, fetching Navidrome-liked track/album/artist ids, keyboard shortcuts,
and syncing the document title to the current track.

On mobile (`< md`), `Sidebar` is replaced by `TopBar` (hamburger + logo +
profile button) with a slide-in `MobileNav` drawer, and `MobileTabBar` renders
a bottom tab bar above the player. Both mobile and desktop navigation expose
the same two primary destinations: **Home** (`/`) and **Search** (`/search`).

`Sidebar` (`ui/src/components/layout/Sidebar.tsx`) is a collapsible left rail
(`220px` expanded, `56px`/`w-14` collapsed, click-to-toggle) with a
collapse/expand button, the two nav links above, active-route highlighting
via `NavLink`, and no playlist/browse tree — navigation is expected to
originate from the dashboard, search, and entity pages rather than a deep
sidebar hierarchy, matching the original shell spec.

`AppHeader` (`ui/src/components/layout/AppHeader.tsx`) renders inline at the
top of the scrollable content (not a separate fixed bar on desktop): the
`discocs` wordmark, a global search input that navigates to
`/search?q=...` on submit, and a profile button.

### Routes

Defined in `ui/src/router.tsx`:

```text
/login                (public)
/                      -> DashboardPage
/search                -> SearchPage
/artists/:id           -> ArtistPage
/releases/:id          -> ReleasePage
/mixes/:id             -> MixPage
/settings               -> SettingsPage
/shelf/:key             -> ShelfPage
/playlists/:id           -> PlaylistPage
```

All routes except `/login` are wrapped in `RequireAuth`, which gates on the
Navidrome-as-IdP login flow. There is no standalone track page — tracks only
appear inside release, playlist, mix, search-result, or queue contexts, each
rendering rows via `TrackTable`/`TrackRow` or `VirtualTrackList`.

## Core pages

The large header cover art on the artist, release, playlist, and mix pages
(`ArtworkImage` with `expandable` set) opens a modal showing the image at
full size on click — `object-contain` inside a `max-h/max-w: 92vh/92vw` box,
so it is never stretched and large images are capped to the viewport instead
of overflowing it. Fallback-letter placeholders (no real artwork) are not
clickable. Small artwork elsewhere (shelf/grid `MediaCard`s, queue rows) is
unaffected — it still just navigates on click.

### Dashboard (`/`, `DashboardPage.tsx`)

The music home screen. Renders `ForYouShelf` (a shelf of static
icon-illustrated entry cards — Flow, Liked Tracks, Recently Played, Mixes For
You, New Releases, Recently Added, Discover, Listen Again, Long Time No
Listen — each linking into its own `/shelf/:key` or playback action) followed
by the live data shelves returned from the backend, rendered via `Shelf`.

Backend: `GET /api/v1/dashboard` (`app/api/dashboard.py`,
`useDashboard` hook in `ui/src/api/hooks/useDashboard.ts`), fetched once with
`staleTime: Infinity` and polled every 60s only for the `history` shelf (via
`GET /api/v1/dashboard/shelves/history`) so "Recently Played" stays fresh
without refetching the whole dashboard. Clicking a shelf card either starts
playback (`playSource` / a `play_action` envelope POST) or navigates to the
entity page.

### Search (`/search`, `SearchPage.tsx`)

Reactive search backed by `GET /api/v1/search` via `useSearch`. URL keeps the
query in `?q=`. A `Tabs` component (All / Artists / Releases / Tracks) filters
the same result set client-side; tabs are disabled when a group is empty.
The All tab shows, in order: a "Top result" `MediaCard` (best single match),
an artists row, a releases row (each capped to 6 cards with a "View all" via
the tab), and a `TrackTable` (capped to 8 rows). Empty query, loading
(skeleton), and no-results states are all handled inline; the player is
unaffected by search navigation since it lives in `AppShell`.

### Release page (`/releases/:id`, `ReleasePage.tsx`)

Backend calls: `useRelease`, `useReleaseTracks`, `useReleaseRelated`,
`useReleaseRecommendations` (`GET /api/v1/releases/{id}`,
`/tracks`, `/related-discography`, `/recommendations`).

Layout: square cover (`ArtworkImage`, `176px`) on the left, title/metadata on
the right — release type label, all participating artists as links, year,
track count, duration — then Play / Shuffle / like-heart actions. Below the
header: `TrackTable` for the release's tracks, a "More from these artists"
`Shelf` built from the related-discography response (filtering out the
current release), and a "Recommended Albums" `Shelf` shown only when the
recommendations response reports `available: true` with items. Missing cover
falls back to a letter placeholder inside `ArtworkImage`.

### Artist page (`/artists/:id`, `ArtistPage.tsx`)

Backend calls: `useArtist`, `useArtistDiscography` (`GET
/api/v1/artists/{id}`, `/discography`).

Layout: circular avatar (`144px`) on the left, artist name and local stats
(`tracks · releases · plays`, each field only shown if > 0) on the right,
with Play and like-heart actions. Below: a `PopularTracks` block built from
`artist.top_tracks` (falls back to the first 5 tracks if none of them have a
positive play count — i.e. it does not hide the section when local play data
is genuinely absent, unlike the original "omit if unavailable" spec), then
one `Shelf` per non-empty discography group returned by the API (e.g. Albums,
EPs, Singles, Featured In — grouping logic lives server-side). There is
currently no tabbed Discography/Top Tracks/Similar Artists/Bio navigation on
this page — it is a single scrolling page with sequential sections, and no
Similar Artists section is rendered yet.

### Mix page (`/mixes/:id`, `MixPage.tsx`)

Backend: `useMix` (`GET` mix detail), `playMix`/`saveMix` mutations. Same
header pattern as release (cover, title, "Generated mix" label, track count,
created date, Play + Save-if-not-saved actions), track list rendered with
`VirtualTrackList` instead of `TrackTable` (mixes can be long).

### Playlist page (`/playlists/:id`, `PlaylistPage.tsx`)

Two branches on the same route:

- `id === "likes"` — the synthetic liked-tracks playlist
  (`fetchLikesPlaylist`/`playLikes`), generated gradient icon instead of
  cover art, Play only (no editing, no selection).
- numeric ids — user playlists (`fetchPlaylist`): 2x2 collage artwork,
  description, and Play / Edit / Delete actions. Edit reuses
  `CreatePlaylistDialog` in edit mode (title + description via `PATCH`);
  Delete asks for confirmation, then `DELETE` + navigate to the dashboard.

Track removal uses a selection mode: a selection checkbox lives in its own
column on the **right** of each row (`VirtualTrackRow` `selectable` prop) and
appears on hover; the play/index button on the left is never replaced.
Checking the first row keeps checkboxes visible on all rows and shows a
"N selected · trash · Cancel" bar above the list, where the trash button
batches `POST /playlists/{id}/tracks/remove`. Only the checkbox itself
toggles selection — the rest of the row keeps its normal play behaviour.

Rows can be reordered by drag-and-drop, built on **@dnd-kit** (`core` +
`sortable` + `utilities`) inside `VirtualTrackList`. The picked-up row follows
the cursor via `DragOverlay` (rendered transparent, no card background) while
siblings reflow via the canonical sortable pattern: the item order (and each
row's virtualizer-driven `top`) stays **frozen during the drag**, and
`verticalListSortingStrategy` shifts in-between rows with `transform` from
`useSortable`. dnd-kit deliberately excludes transforms from rect measurement,
so collision rects stay valid while rows visually make way — reordering the
data mid-drag instead would re-measure rows mid-animation and corrupt
collision detection. Reorderable rows are positioned with `top` rather than
`translateY` for the same reason: transform-positioned rows would all measure
at the container top. On drop, `onDragEnd` applies `moveTrackById`
(`arrayMove`) to a local optimistic order (kept until the refetch lands so the
row does not snap back). Pointer, touch (press-and-hold) and keyboard sensors
are wired for cross-device support; `MeasuringStrategy.Always` re-measures
droppables as virtualized rows mount/unmount during drag auto-scroll. On drop,
`onDragEnd` calls `POST /playlists/{id}/tracks/reorder` with the full new
track-id order and invalidates the playlist queries. The backend rejects
non-permutations with 409 `invalid_order`.

### Playlist dialogs (`components/playlists/`)

`AddToPlaylistDialog` (Recent 4 by `updated_at` + full list + "New playlist")
and `CreatePlaylistDialog` (name/description + cosmetic visibility select,
doubles as the edit dialog) are mounted once in `AppShell` and driven by a
`uiStore` slice (`openAddToPlaylist(trackIds)` /
`openCreatePlaylist(options)`). Entry points: the "Add to playlist" item in
`TrackMenu`, the ListPlus button in the ExpandedPlayer queue header (saves
all `queue.items`, deduped, excluding the autoplay pool), and the MixPage
Save button (opens the create dialog prefilled with the mix title; submit
posts the extended `/mixes/{id}/save` body). The likes playlist never
appears in these dialogs or in the Playlists shelf — it is not stored in the
`playlists` table.

### Shelf page (`/shelf/:key`, `ShelfPage.tsx`)

The "View all" destination for any dashboard shelf. Backed by `useShelf`
(paginated `GET /api/v1/dashboard/shelves/{key}`) with infinite-scroll via an
`IntersectionObserver` sentinel, rendering results in a virtualized grid
(`VirtualCardGrid`) of `MediaCard`s rather than a horizontal row.

### Settings (`/settings`, `SettingsPage.tsx`)

A personal settings page containing the **Flow Profile** status card (not built
/ building / ready / cold start / empty). It polls
`GET /api/v1/jobs/flow-profile/status` every 2s while building and exposes a
Build/Rebuild button backed by `POST /api/v1/jobs/flow-profile`.

Instance-wide Navidrome credentials and all operational settings (scan,
analysis, models, storage, advanced/debug) are intentionally absent from the
public UI. They remain in the private legacy admin at `:8711/admin`; the
public nginx also rejects their API endpoints.

The profile popover shows the active username, a language switcher (see
Internationalization below), and provides logout. It redirects to `/login`
only after the backend confirms that the session was revoked; a failed
request leaves the user in place and shows a retryable error.

## Internationalization

The UI ships English and Russian copy via `react-i18next` /`i18next`
(`ui/src/i18n/`). English is the source language and the fallback for any
untranslated key. This covers the public web UI only — the legacy admin
(`app/ui.html`) is not localized.

- **Dictionaries**: `ui/src/i18n/locales/{en,ru}/*.json`, one namespace per
  feature area (`common`, `nav`, `profile`, `auth`, `settings`, `player`,
  `dashboard`, `media`, `search`, `artist`, `release`, `mix`, `playlist`).
  All namespaces are bundled statically (no lazy per-route loading — the
  dictionaries are small) and registered in `ui/src/i18n/index.ts`.
- **Pluralization**: keys that vary by count use i18next's `_one`/`_few`/
  `_many`/`_other` suffixes (Russian has four plural categories vs. English's
  two); i18next picks the category via `Intl.PluralRules` from the `count`
  option passed to `t()`.
- **Language selection**: exposed only in the profile popover
  (`ProfileButton`), not on the Settings page — a `SUPPORTED_LANGUAGES`
  toggle that `PATCH`es `/api/v1/me/settings` (`{ language: "en" | "ru" }`)
  and calls `i18n.changeLanguage()` immediately (optimistic; no reload).
  `useUserSettings` (`ui/src/api/hooks/useUserSettings.ts`), mounted once in
  `AppShell`, fetches the stored setting on load and applies it.
- **Persistence**: the backend `user_settings` table (see
  `docs/data-model.md`) is the source of truth, so the choice follows the
  account across devices. `i18next-browser-languagedetector` also caches the
  active language to `localStorage` (key: `LANGUAGE_STORAGE_KEY` in
  `ui/src/i18n/index.ts`) purely to avoid an English flash before the
  `/me/settings` fetch resolves on the next load; on conflict the backend
  value wins once it arrives. Before login (and before that fetch), the
  detector falls back to the cached value or `navigator.language`.
- **Locale-aware formatting**: dates (`toLocaleString`/`toLocaleDateString`)
  and compact numbers (`Intl.NumberFormat`) are constructed with
  `i18n.language` rather than a hardcoded locale, so they follow the active
  UI language too.

## Player

Full behavioral detail (backdrop rendering, Flow vs. autoplay refill routing)
lives in `docs/ui-player.md`; this section covers layout only.

### Player bar (collapsed, `PlayerBar.tsx`)

Fixed to the bottom of the viewport, always mounted. Structure:

- a thin seek bar spanning the full width along the top edge of the bar;
- a `72px` control row: transport (prev / play-pause / next) and elapsed/total
  time on the left; track artwork, title, artist, release link, like/dislike,
  and an overflow menu (Instant mix, "Don't play this") in the center; volume
  (hover-reveal slider), repeat-one, shuffle, autoplay toggle, and an expand
  chevron on the right (volume/repeat/shuffle/autoplay are hidden below `md`).
- track swaps cross-fade (artwork preloaded before the swap; see
  `docs/ui-player.md` for exact timings) rather than popping instantly.

Native `<audio controls>` is never shown — audio is a hidden playback engine
only, consistent with the original player spec.

### Expanded player (`ExpandedPlayer.tsx`)

A full-screen overlay (`fixed inset-0`, translate-based open/close
animation), not a small card and not a separate route. Desktop layout is a
two-pane row: large artwork + transport/seek/volume controls centered on the
left, and a fixed-width (`440px`) queue panel on the right, separated by a
border. Mobile collapses this into a "Now Playing" / "Queue" tab switch.

The queue panel is simpler than the original spec's Up Next / Lyrics / Related
tab set: it is a single scrollable list — the current session queue, then (if
present) an "Autoplay" divider followed by the autoplay-generated pool
(dimmed). An Autoplay on/off switch sits in the panel header. There is no
lyrics tab, no "Related" tab, and no source label or preference-chip row
currently implemented. Queue rows are compact (`QueueItem`: small cover,
title, artist/source, duration), not framed cards, matching the spec's intent
there.

## Dashboard shelves

The dashboard is driven entirely by `app/services/dashboard.py` and exposed
via `app/api/dashboard.py`. It implements substantially more shelf types than
the original three-shelf plan; all are keyed, paginated, and independently
fetchable through `GET /api/v1/dashboard/shelves/{key}`:

```text
recently_added, history, listen_again, long_time_no_listen,
mixes_for_you, albums_for_you, discover_random, new_releases,
liked_artists, liked_releases
```

`GET /api/v1/dashboard` returns a `hero` block (Flow entry — `available` only
when a Flow profile exists with `status == "ready"`), the shelves above (each
computed with `limit`/`offset=0`), and an echoed `settings` object
(`visible_shelves`, `items_per_shelf`). Every shelf item uses the shared
shape: `entity_type`/`entity_id`, `title`, `subtitle`, `artwork`, `action`
(navigation target), `play_action`, optional `badges`/`reason`, and `debug`
(only with `include_debug=true`).

Ranking logic for the three shelves originally specced in phase 5:

- **Recently Added** — releases ordered by `added_at` (`releases.added_at`
  if set, else the max of member tracks' `added_at`/`created_at`) descending,
  tie-broken by release id descending. Excludes releases whose only tracks
  are missing (`tracks.missing_at IS NOT NULL`). Purely operational —
  no personalization.
- **Listen Again** — tracks from `user_track_preferences` where the user
  liked the track, or it has a nonzero completion/replay count, or a positive
  score, excluding disliked tracks and (unless liked) tracks whose most
  recent event was a skip after the last completed/played time. Order is
  randomized per request (`ORDER BY RANDOM()`) rather than score-ranked.
  Reason text is derived per-row: "You liked this" > "Replayed N times" >
  "Completed N times" > "Played N times" > "Played before".
- **Long Time No Listen** — same positive-signal criteria as Listen Again,
  additionally requiring `last_played_at` to be non-null and older than a
  180-day cutoff (not the 30/90-day windows described in the phase-5 spec),
  and that the last skip (if any) isn't more recent than the last play unless
  liked. Also randomized per request. Reason text is a static "Long time
  since last listen" rather than the dynamic "Not played in N months" text
  originally specced.

Additional shelves beyond the original plan, all backed by
`app/services/dashboard.py`:

- `history` ("Recently Played") — tracks ordered by `last_played_at` desc;
  this is the shelf polled every 60s from the dashboard hook.
- `mixes_for_you` — active/saved generated mixes (`app/mixes.py`); the
  dashboard endpoint also triggers `ensure_dashboard_mixes_fast`, which
  either generates mixes inline (small libraries) or kicks off a background
  thread (guarded by `MIX_GENERATION_LOCK`) for larger ones.
- `albums_for_you` — served from a precomputed per-model cache
  (`store.get_albums_for_you_cache`), reshuffled (not recomputed) on every
  read for variety while keeping the cache itself score-ordered.
- `discover_random` — tracks with no preference row or a fully "untouched"
  preference row (no dislike, no plays), randomized.
- `new_releases` — releases ordered by `release_year` desc then `added_at`
  desc (year must be set and `<= 2030`).
- `liked_artists` / `liked_releases` — straightforward "liked" listings from
  `user_artist_preferences` / `user_release_preferences`, ordered by
  `updated_at` desc.

There is no dedicated shelf settings UI yet (enable/disable, reordering,
per-shelf windows) — the shelf set and item count are effectively
hard-coded server-side (`shelf_keys` list in `api_v1_dashboard`) rather than
user-configurable, which was left as an open decision in the original spec.

## Visual design

Dark, dense, cover-art-forward layout in the spirit of mainstream streaming
apps, implemented with Tailwind v4 utility classes plus CSS custom properties
for theming (`ui/src/index.css`):

- **Palette**: near-black background (`#0b0d0f`), near-white foreground
  (`#eef2f3`), dark-gray card/muted surfaces (`#171a1d` / `#242629`), muted
  text at reduced contrast. The one deviation from the original "fixed
  purple/pink accent" plan: the accent color (`--track-accent` /
  `--primary`) is **dynamic**, extracted from the currently playing track's
  artwork and pushed as a CSS variable, so buttons, active states, and the
  player's plasma backdrop all retint per track rather than using one fixed
  brand color.
- **Layout**: fixed sidebar (collapsible, ~`220px`/`56px`) plus a single
  scrollable content column; the player is fixed to the viewport bottom and
  content gets bottom padding (`pb-[92px]`) so the last row is never hidden
  behind it. Page/section padding is generally `px-4`–`px-6` with `sm:`
  breakpoints, narrower than the 24–40px desktop figure in the original spec
  but consistent with a denser, mobile-aware layout — this project supports a
  responsive mobile layout (tab bar, drawer nav, stacked headers) that the
  original desktop-only spec explicitly deferred.
- **Density/typography**: headings use Tailwind scale classes rather than
  fixed pixel values — page titles (release/artist/mix headers) are
  `text-3xl font-bold` (~30px), the app wordmark is `text-2xl`/`text-lg`.
  Media card titles are noticeably smaller and denser than the original
  15–18px spec: `MediaCard` renders titles at `14px` (`text-[14px]`) and
  subtitles at `12px`/`10px` depending on whether subtitle links are present.
  Track rows and table text use Tailwind's `text-sm`/`text-xs` (~14px/12px).
  This general "bold heading, small dense body/card text" balance matches the
  spec's intent even where specific pixel values differ — treat exact sizes
  as needing a spot-check against `ui/src/index.css` and component classes
  rather than a frozen spec.
- **Cards**: square artwork with small-radius rounded corners
  (`rounded-md`, circular for artists), title + subtitle below, hover-reveal
  play button overlay bottom-right, no permanent Open/Play buttons — matching
  the "shelf cards are not management cards" rule from the original spec.
  Shelf-variant cards additionally get a subtle tilt-on-hover effect
  (`TiltedArtwork`).
- **Shelves**: horizontal, paged (not free-scroll) on desktop — `Shelf`
  slices items into `cols`-wide pages and animates between them with
  prev/next arrow buttons; mobile falls back to native horizontal scroll
  with snap points. A "More" link routes to the full `/shelf/:key` grid page.

Some of the pixel-level claims above (exact card widths, exact heading sizes
across all breakpoints) were spot-checked against current Tailwind classes
and CSS variables but not exhaustively audited against every component —
treat them as representative of current density/approach rather than a
pixel-perfect specification.
