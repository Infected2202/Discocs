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

**Scroll restoration** (`hooks/useScrollRestoration.ts`, mounted in `AppShell`).
The page scrolls inside `<main>`, not the window, so the browser's own
restoration and react-router's `<ScrollRestoration>` don't apply. The hook
keeps the `<main>` offset per history entry (`location.key`, in memory):
back/forward (`POP`) returns to where the user was, opening a new page
(`PUSH`/`REPLACE` to another path) starts from the top, and a same-path
navigation (search-param updates, the player entry below) leaves the scroll alone. Lists render asynchronously, so a restore
retries every frame until the content is tall enough (up to 2 s) and gives up
as soon as the user wheels/touches the page themselves.

**Back closes the expanded player first** (`hooks/usePlayerHistory.ts`, mounted
in `AppShell`). Expanding the player pushes a same-URL history entry with
`location.state.playerOpen`; `playerStore.expanded` stays the source of truth
for rendering and the hook keeps the two in step by watching transitions:
browser/system back and the mobile back swipe collapse the player (the page
beneath is untouched); collapsing by a button pops that entry so history has no
phantom step; the artist/release links inside the player use `replace`, so
they swap the player entry for the new page; "forward" onto the entry
re-expands. After a reload the player is not reopened and the stale entry is
stepped off. The DJ surface is not covered (it is slated for removal).

### Routes

Defined in `ui/src/router.tsx`:

```text
/login                (public)
/                      -> DashboardPage
/search                -> SearchPage
/artists/:id           -> ArtistPage
/artists/:id/similar   -> ArtistSimilarPage          (full list)
/artists/:id/labels    -> ArtistLabelsPage           (full list)
/releases/:id          -> ReleasePage
/releases/:id/related  -> ReleaseRelatedPage         (full list)
/releases/:id/recommendations -> ReleaseRecommendationsPage (full list)
/labels/:id            -> LabelPage
/mixes/:id             -> MixPage
/settings               -> SettingsPage
/shelf/:key             -> ShelfPage
/playlists/:id           -> PlaylistPage
/shared-links            -> SharedLinksPage
/u/:username             -> ProfilePage
/u/:username/history     -> ListeningHistoryPage
/u/:username/top/:kind   -> ProfileTopPage   (kind = artists|releases|tracks, ?period=)
/u/:username/likes/:kind -> ProfileLikesPage (kind = tracks|releases|artists)
/u/:username/playlists   -> ProfilePlaylistsPage
```

The "full list" routes are the «Ещё» destinations of horizontal shelves — see
"Shelves: preview size and «Ещё»" below.

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

Collection titles wrap at word boundaries (and, for unbroken strings, at any
character) instead of widening the page on narrow screens. Track-row context
menus stay visible on touch/pen devices and use the compact hover-only
treatment only for fine pointers. Long playlist and mix track lists are
virtualized relative to their actual offset inside AppShell's scrolling
`<main>`, including the headers above them, so reversing scroll direction does
not expose an unrendered gap. Collection-header Shuffle actions are icon-only
at every viewport width while retaining accessible labels and tooltips. Release
pages omit the release-type kicker (Album, EP, Single, and similar) at every
viewport width. On mobile track rows, the duration/play-count column is sized
to its content instead of reserving the desktop `80px`; this gives titles more
room and keeps the metric visually close to the Like and context-menu actions.

### Dashboard (`/`, `DashboardPage.tsx`)

The music home screen. Renders, top to bottom: `PeopleShelf` («Люди» /
"People" — other discocs users and what they play right now, see
[`docs/social.md`](social.md#ui-главной-ф4); hidden when the list is empty; the viewer is listed too), `ForYouShelf` (a shelf of static
icon-illustrated entry cards — Flow, Liked Tracks, Recently Played, Mixes For
You, New Releases, Recently Added, Discover, Listen Again, Long Time No
Listen — each linking into its own `/shelf/:key` or playback action), then
the live data shelves returned from the backend, rendered via `Shelf`
(order set by `shelf_keys` in `app/api/dashboard.py`; `labels` comes first,
above Mixes For You). The
loading placeholder for both the People shelf and the data shelves is
`ShelfSkeleton`.

Backend: `GET /api/v1/dashboard` (`app/api/dashboard.py`,
`useDashboard` hook in `ui/src/api/hooks/useDashboard.ts`), fetched once with
`staleTime: Infinity` and polled every 60s only for the `history` shelf (via
`GET /api/v1/dashboard/shelves/history`) so "Recently Played" stays fresh
without refetching the whole dashboard. Clicking a shelf card either starts
playback (`playSource` / a `play_action` envelope POST) or navigates to the
entity page.

### Search (`/search`, `SearchPage.tsx`)

Reactive search backed by `GET /api/v1/search` via `useSearch`. URL keeps the
query in `?q=`. A `Tabs` component (All / Artists / Releases / Labels / Tracks) is
disabled when a group is empty; the trigger label always shows the group's
true total (from `search_group.total`, not the capped preview length).

The All tab is a small preview fed by a single `type=all` request (12 items
per group): a "Top result" `MediaCard` (best single match), an artists row,
a releases row and a labels row (each capped to 6 cards; label cards come
from `labelToCard` — name and release count, no Play button), and a track
list (capped to 8 rows). Each section header carries a "Show all" button (only rendered when
the group's total exceeds the preview) that switches to that group's tab.

The Artists / Releases / Labels / Tracks tabs are independent — each paginates through
*all* of its matches via `useInfiniteSearch` (`GET /api/v1/search?type=...`,
50 per page, driven by the response's `next_offset`), not the capped `all`
preview. An `IntersectionObserver` sentinel at the bottom of the list
triggers `fetchNextPage()` as the user scrolls, same pattern as
`ShelfPage`/`useShelf`. The Tracks tab uses `VirtualTrackList` with
virtualization enabled (unlike the All-tab preview) since a single query can
match thousands of tracks.

Empty query, loading (skeleton), and no-results states are all handled
inline; the player is unaffected by search navigation since it lives in
`AppShell`.

### Release page (`/releases/:id`, `ReleasePage.tsx`)

Backend calls: `useRelease`, `useReleaseTracks`, `useReleaseRelated`,
`useReleaseRecommendations` (`GET /api/v1/releases/{id}`,
`/tracks`, `/related-discography`, `/recommendations`).

Layout: square cover (`ArtworkImage`, `176px`) on the left, title/metadata on
the right — release type label, all participating artists as links, year,
record labels as links to `/labels/:id` (`release.labels` of the detail
response, tag order),
track count, duration; under that line up to five styles (`GenreTags`,
`release.genres`, strongest first — see "Genres" below) — then Play / Shuffle /
like-heart actions. Below the
header: `TrackTable` for the release's tracks, a "More from these artists"
`Shelf` built from the related-discography response (filtering out the
current release; for a Various Artists compilation the synthetic "Various
Artists" is dropped and the top 8 track artists, most-represented first, feed
the shelf instead), a "Recommended Albums" `Shelf` shown only when the
recommendations response reports `available: true` with items — both preview
16 cards and link to their full lists (`/releases/:id/related`,
`/releases/:id/recommendations`) when the response's `total` is larger — and
last an «От лейбла <name>» `Shelf` per record label (`LabelReleasesShelf`:
`GET /api/v1/labels/{id}/releases?sort=popularity`, without the current
release and releases of its artists — those are on the first shelf; hidden
when nothing is left, e.g. the artist's own label; «Ещё» leads to
`/labels/:id`). Missing cover
falls back to a letter placeholder inside `ArtworkImage`.

### Artist page (`/artists/:id`, `ArtistPage.tsx`)

Backend calls: `useArtist`, `useArtistDiscography`, `useArtistSimilar` (`GET
/api/v1/artists/{id}`, `/discography`, `/similar?limit=16`; the full list
`/artists/:id/similar` pages the same endpoint with `offset`).

Layout: circular avatar (`144px`) on the left, artist name and local stats
(`tracks · releases · plays`, each field only shown if > 0) with the artist's
styles and release counts under them (`GenreTags`, `genres` of the artist
response) on the right,
with Play and like-heart actions. Below: a `PopularTracks` block built from
`artist.top_tracks` by `pickPopularTracks` (`lib/popularTracks.ts`): every
played track, then tracks with a Deezer rank up to 20 in total (the API
already orders by plays, then rank); falls back to the first 5 tracks when
none has either signal — i.e. it does not hide the section when popularity
data is genuinely absent, unlike the original "omit if unavailable" spec), then
one grid `Shelf` per non-empty discography group returned by the API (e.g.
Albums, EPs, Singles, Featured In — grouping logic lives server-side). A
regular 16-item "Similar artists" shelf follows when artist
aggregates are available, with «Ещё» to the full ranked list (up to 200
artists) when there are more. Last comes the "Artist's labels" slider `Shelf`
(`useArtistLabels`, `GET /api/v1/artists/{id}/labels`, paged like similar
artists) with the same label cards as the dashboard (name and the label's
release count): labels of releases credited to the artist (a guest track on
another label's compilation does not count), most of the artist's releases
first; «Ещё» opens the full list `/artists/:id/labels`; hidden when the
artist's releases carry no label. Missing similar-artist images are enriched through
the same Navidrome `getArtistInfo2` path used by search and artist pages, then
served through the backend cover proxy. There is no tabbed Discography/Top
Tracks/Similar Artists/Bio navigation on this page; it remains a single
scrolling page with sequential sections.

### Label page (`/labels/:id`, `LabelPage.tsx`)

Backend calls: `useLabel`, `useLabelReleases` (`GET /api/v1/labels/{id}`,
`/releases?sort=release_date_desc|release_date_asc|popularity`; popularity =
Deezer album fans).

Layout: square label image (`144px`, from `/api/v1/labels/{id}/image` — the
stored logo or the bundled Beatport placeholder), name and release count
(no "Label" kicker — the page is obviously a label), the label's styles with
release counts (`GenreTags`, `label.genres`, up to 10), a Shuffle button and a like heart. Shuffle starts a playback
session with `source_type: "label"`: the backend queues a random sample of up
to 200 available tracks across all the label's releases (a plain, unshuffled
label session would take them newest release first, in track order), then
autoplay continues as usual. Label likes are local to discocs (Navidrome
has no label stars): `PUT`/`DELETE /api/v1/labels/{id}/like` through
`useToggleLabelLike` (optimistic, rolls back on error, then refetches the
dashboard and the `labels` shelf). `LikeButton` takes `liked`/`onToggle` in
this controlled mode instead of reading `navidromeStore`. No Play button and
no release-type filters — by design. Below the header, when present: the description (clamped to four
lines with "Show more"/"Show less", `[a=Name]` mentions rendered as links to
artists that exist in the library, plain text otherwise, plus a
"Source: …" caption — omitted for a hand-written `editorial` description) and the label's external links (new tab,
`rel="noopener noreferrer"`). The newest/oldest-first sort select sits in
the header actions next to the heart (no separate "Releases" heading row —
it read as an empty group above the real ones). Then one grid `Shelf` per
non-empty release-type group — Albums, EPs, Singles, Compilations, Other releases (soundtracks,
mixes, unknown type), in that order, from `groups` of the releases response —
with release cards (subtitle `artists · year`). Sorting is by release date; year-only releases sit by
year and are ordered by title inside it; undated releases go last in both
directions. Images, descriptions and links come from the label sync job in the admin
(see `plans/labels-shelf.md`).

### Genres (release, artist, label)

Styles come from the `genre_discogs400` predictions (`app/genres.py`, method and
the experiment behind it in `plans/genres.md`). A release shows up to five
styles: the mean score over its analyzed tracks, at least 25% of the strongest
style and at least 0.05; styles the model systematically gets wrong (Tech
Trance, Bassline, Electro House…) are never shown, and sub-styles Discogs folds
into a parent (Halftime, Jungle, Schranz, Hard Techno…) only next to that
parent. An artist (its own releases) and a label show how many releases carry
each style — styles on at least 5% of them, up to ten. A label card is the
same everywhere (`components/media/labelCard.ts`): the name and the label's
release count ("588 releases"), no styles.

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
  Delete opens a confirmation modal that names the playlist and its track
  count, then `DELETE` + navigate to the dashboard with `replace`. The cached
  `["playlist", id]` query is removed first, so "Back" can't render the
  deleted playlist from cache (where every action would 404). A 404 from
  `DELETE` itself (already gone) is treated as success and also leaves.

Destructive actions on this page go through `components/common/ConfirmDialog`
(in-app modal, not the browser's `confirm()`; focus starts on Cancel so a
stray Enter never destroys anything; API errors are shown inside the modal).

Selection mode: a selection checkbox lives in its own column on the **right**
of each row (`VirtualTrackRow` `selectable` prop) and appears on hover; the
play/index button on the left is never replaced. Checking the first row keeps
checkboxes visible on all rows and shows a bar above the list:
"N selected · Add to playlist · Move to playlist · Remove from playlist ·
Cancel". Remove asks for confirmation, then batches
`POST /playlists/{id}/tracks/remove`. Add/Move open `AddToPlaylistDialog`
(Move in move mode, see below). Only the checkbox itself toggles selection —
the rest of the row keeps its normal play behaviour. The selection is pruned
to ids still present in the playlist after each refetch.

A single track can also be removed from the row's `TrackMenu` ("Remove from
playlist", only on editable playlists, via `VirtualTrackList` `onRemoveTrack`)
— same confirmation modal.

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
`uiStore` slice (`openAddToPlaylist(trackIds, defaultTitle?, { moveFrom? })` /
`openCreatePlaylist(options)`). Move mode (`moveFrom` = source playlist id)
titles the dialog "Move to playlist", hides the source playlist from the list,
and adds to the target **first**, removing from the source only after the add
succeeded — a failed add leaves the tracks where they were. "New playlist" in
move mode forwards `moveFromPlaylistId` to `CreatePlaylistDialog`, which
removes the tracks from the source after creating the new playlist. Entry points: the "Add to playlist" item in
`TrackMenu`, the ListPlus button in the ExpandedPlayer queue header (saves
all `queue.items`, deduped, excluding the autoplay pool), and the MixPage
Save button (opens the create dialog prefilled with the mix title; submit
posts the extended `/mixes/{id}/save` body). The likes playlist never
appears in these dialogs or in the Playlists shelf — it is not stored in the
`playlists` table.

### Shelf page (`/shelf/:key`, `ShelfPage.tsx`) and other full lists

The "View all" destination for any dashboard shelf. Backed by `useShelf`
(paginated `GET /api/v1/dashboard/shelves/{key}`). The page itself is the
generic `FullListPage` (`components/media/FullListPage.tsx`): back button,
title (+ optional subtitle) with the total, infinite scroll via an
`IntersectionObserver` sentinel, results in a virtualized grid
(`VirtualCardGrid`) of `MediaCard`s rather than a horizontal row. Its data
source is any `limit`/`offset` endpoint answering `items`/`total`/
`next_offset`, wrapped in `usePagedList` (`api/hooks/usePagedList.ts`); the
route pages only plug in the source and the item → card mapping:

| Route | Page | Source |
|---|---|---|
| `/shelf/:key` | `ShelfPage` | `GET /api/v1/dashboard/shelves/{key}` |
| `/artists/:id/similar` | `ArtistSimilarPage` | `GET /api/v1/artists/{id}/similar` |
| `/artists/:id/labels` | `ArtistLabelsPage` | `GET /api/v1/artists/{id}/labels` |
| `/releases/:id/related` | `ReleaseRelatedPage` | `GET /api/v1/releases/{id}/related-discography` |
| `/releases/:id/recommendations` | `ReleaseRecommendationsPage` | `GET /api/v1/releases/{id}/recommendations` |
| `/u/:username/top/:kind?period=` | `ProfileTopPage` | `GET /api/v1/users/{username}/top/{kind}` |
| `/u/:username/likes/:kind` | `ProfileLikesPage` | `GET /api/v1/users/{username}/likes/{kind}` |
| `/u/:username/playlists` | `ProfilePlaylistsPage` | `GET /api/v1/users/{username}/playlists` |

Titles repeat the shelf's title (profile tops keep the period suffix,
«Топ артистов (30 дн.)»); the artist name / release title / username is the
subtitle.

### Profile page (`/u/:username`, `ProfilePage.tsx`)

A user's own listening profile, open to every logged-in user (social features,
details in [`docs/social.md`](social.md#ui-профиля-ф5)). Backend calls:
`useUserProfile` (`GET /api/v1/users/{username}/profile?period=&tz=`, tz from
`Intl`), `useUserLikes`, `useUserPlaylists`, plus `usePeople` for the live
"Now playing" line. One scrolling page, no tab navigation: `CollectionHeader`
with the round built-in avatar (clickable on one's own profile → avatar picker
dialog), member-since date and all-time totals; a `tabs` period switch
(7d/30d/90d/180d/year/all, default 30d, kept in the URL as `?period=` so
coming back from a top's full list restores it) driving the stats and tops; recent
listens (`VirtualTrackRow` with relative time, "All" → history); period stats
with div bar charts (by day/month, by hour) and the sound profile; top
artists/releases shelves; top tracks (`VirtualTrackList`); likes shelves;
playlists shelf. Another user's profile is the same minus private playlists
and the avatar picker.

### Listening history (`/u/:username/history`, `ListeningHistoryPage.tsx`)

Every listen, newest first, from `useUserListens` (paged `GET
/api/v1/users/{username}/listens`), grouped under local-day headings
(Today / Yesterday / weekday + date). The next page loads via an
`IntersectionObserver` sentinel or the "Load more" button.

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
  and the shared track overflow menu (play, play next, Instant mix, add to
  playlist, artist/release navigation) in the center; volume
  (hover-reveal slider), repeat-one, shuffle, autoplay toggle, and an expand
  chevron on the right (volume/repeat/shuffle/autoplay are hidden below `md`).
- track swaps cross-fade (artwork preloaded before the swap; see
  `docs/ui-player.md` for exact timings) rather than popping instantly.

The collapsed and expanded players reuse `TrackMenu`, so every track surface
exposes the same base actions; a surface can add only context-specific actions.

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
liked_artists, liked_releases, labels, playlists
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
  the dashboard first performs a lightweight, session-bound Navidrome
  play-state refresh and repeats it before polling this shelf every 60s.
  Each item also carries `played_at` (its `last_played_at`); `shelfItemToCard`
  turns it into the card's `meta` via `formatRelativeTime`
  (`ui/src/lib/relativeTime.ts`), so the subtitle reads «Artist · 3 ч назад».
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
- `labels` — record labels: the user's liked labels first, then (within
  each part) by the number of their releases that still have an available
  track (no minimum), then by name. Cards
  (`entity_type: "label"`) carry `release_count` instead of a subtitle — the
  client renders a pluralized "N releases" — and `play_action: null`, so
  there is no Play button. Same data as `GET /api/v1/labels`.
- `liked_artists` / `liked_releases` — listings from
  `user_artist_preferences` / `user_release_preferences` where `liked = 1`,
  ordered by `COALESCE(liked_at, updated_at)` desc so the newest like is first
  (`updated_at` alone would sort by last played). Those flags mirror Navidrome
  stars — see `docs/data-model.md`. Because the shelves read that mirror while
  `/api/v1/navidrome/starred/ids` is what *writes* it, the frontend invalidates
  `["dashboard"]` and the two shelf keys after every like toggle **and** after
  `fetchLikedIds()`; otherwise the initial dashboard fetch can win the race
  against the sync, come back empty, and the shelf hides itself
  (`Shelf.tsx` renders nothing for an empty list).

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
  (`rounded-md`, circular for artists and users), title + subtitle below, hover-reveal
  play button overlay bottom-right, no permanent Open/Play buttons — matching
  the "shelf cards are not management cards" rule from the original spec.
  Optional `meta` is appended to the subtitle line after « · »; `live` puts a
  pulsing green dot before it (`motion-safe:animate-ping`). Type `user`
  (`id` = username) never shows a play button and opens `/u/:username`.
  Shelf-variant cards additionally get a subtle tilt-on-hover effect
  (`TiltedArtwork`).
- **Shelves**: horizontal, paged (not free-scroll) on desktop — `Shelf`
  slices items into `cols`-wide pages and animates between them with
  prev/next arrow buttons; mobile falls back to native horizontal momentum
  scrolling, with 2¼ cards visible (a quarter of the third peeks out from the
  right edge so it is obvious the shelf scrolls; a shelf of ≤2 cards stays
  plain two across — `mobileCardFlex`). Snap points are disabled while the finger and native momentum
  are moving the shelf. After scroll events become idle, the shelf smoothly
  moves to the nearest card while snap remains disabled; `proximity` is
  restored only after that alignment also settles. This keeps light gestures
  local and avoids an abrupt final jump. See "Shelves: preview size and
  «Ещё»" below for the preview size and the "More" link.
  Every titled shelf header row (dashboard, artist discography groups, etc.)
  has a thin accent-colored divider (`bg-primary/50`) filling the gap between
  the title/subtitle and the More/prev/next controls, tracking the same
  dynamic per-track accent as the rest of the UI. Titleless shelves (the
  dashboard's For You row) omit the divider — its controls are right-aligned
  with `ml-auto` instead.

### Shelves: preview size and «Ещё»

Every horizontal shelf previews the same number of cards:
`SHELF_PREVIEW_LIMIT = 16` — two slider pages at the widest 8-column layout
(`useColumns`). The constant exists once on each side —
`ui/src/lib/shelves.ts` and `app/services/shelves.py` — and every preview
uses it instead of its own number: the dashboard (`useDashboard`, default
`limit` of `/api/v1/dashboard` and `/dashboard/shelves/{key}`), profile tops,
likes and playlists, similar artists, the artist's labels, "More from these
artists" and recommended albums. Grid shelves (`grid`: artist discography groups, label
releases) are not previews — they show everything and never get «Ещё».

`Shelf` takes `moreHref` (the full list; `shelfKey` is sugar for
`/shelf/{key}`) and `total` (the source's full length from the API). The
title link and «Ещё» appear only when there is more than the shelf shows:
`(total ?? items.length) > shown`, where `shown` is `cols × 2` cards on
desktop and every loaded card on mobile. So a shelf whose whole source fits
has no «Ещё», and a shelf without `total` still offers it when the loaded
cards do not fit the slider.

The rule holds for every slider shelf: no shelf hides cards without «Ещё». A
shelf without a full-list page (`PeopleShelf`: the people list is all users,
polled whole; `ForYouShelf`: static entry cards) unfolds in place — «Ещё»
turns the slider into a grid of every loaded card and becomes «Свернуть».
Grid shelves show everything and need neither.

Some of the pixel-level claims above (exact card widths, exact heading sizes
across all breakpoints) were spot-checked against current Tailwind classes
and CSS variables but not exhaustively audited against every component —
treat them as representative of current density/approach rather than a
pixel-perfect specification.
