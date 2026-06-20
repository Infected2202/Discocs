# Web UI Visual Specification

## Purpose

Describe the target visual design for the future first-party web music app. This
file complements the product/recommendation plan in
`plans/recommendation-cards-flow.md`.

Screenshots from music services are references only and are not stored in this
plan. The goal is to capture enough layout, density, and component behavior to
recreate a similar web experience.

## Specification Standard

This file should be written as an implementation-oriented UI plan, not as loose
inspiration. For each page or major component, capture:

- what elements exist;
- where they are positioned;
- how they look;
- what each element does;
- what data it displays;
- what states it has;
- what should be omitted.

When a page is not specified to this level yet, treat it as incomplete and
expand it before implementation.

Recommended page spec structure:

```text
Page purpose
Layout zones
Header
Primary actions
Main content
Secondary sections
Navigation links
Empty/loading/error states
What not to show
Open questions
```

Recommended component spec structure:

```text
Component purpose
Visual shape
Content/data fields
Interactions
States
Where used
```

## Overall Direction

- Dark music-hosting interface.
- Dense but readable layout.
- Cover art is the primary visual material.
- Persistent playback controls.
- Horizontal recommendation shelves on the dashboard.
- Search results page with grouped entity sections.
- Expanded player with large artwork and queue panel.
- Power-user settings organized into tabs.

## App Shell

Desktop layout:

- Left sidebar fixed.
- Main content scrolls vertically.
- Bottom player fixed across the app.
- Expanded player overlays or expands above the bottom player.

Approximate dimensions:

- Sidebar width: 210-240px.
- Bottom player height: 64-80px.
- Main content max width: fluid, full available width.
- Content padding: 24-40px desktop.
- Shelf vertical spacing: 36-56px.

Mobile/tablet can be designed later. Desktop is the primary target.

## Color And Tone

Base:

- Background: near-black.
- Panels/cards: dark gray.
- Hover/active rows: medium dark gray.
- Text primary: near-white.
- Text secondary: muted gray.
- Dividers: subtle dark gray lines.

Accent:

- One strong accent color for active tabs, primary buttons, sliders, and play
  indicators.
- Purple/pink is acceptable if it matches the app identity, but keep it
  restrained.

Avoid:

- overly decorative gradients as primary layout;
- bright backgrounds behind dense text;
- low-contrast gray-on-black text.

## Typography

Use a bold, compact music-app style:

- Page/shelf headings: large, heavy, 32-48px desktop depending on context.
- Card titles: 15-18px, bold.
- Card subtitles: 14-16px, muted.
- Table/list text: 13-15px.
- Metadata/actions: 12-14px.

Headings can be large like streaming apps, but content density should remain
high.

## Sidebar

Structure:

- Top navigation: Home, Navigator/Browse, Library.
- Primary actions: New playlist/action button.
- Scrollable playlist/saved-mix list.
- Active item highlighted with dark gray background.

Visual:

- Fixed left rail.
- Icons plus labels.
- Divider lines between groups.
- Playlist items use title and small owner/source subtitle.

Behavior:

- Sidebar remains visible on desktop.
- Playlist list scrolls independently if long.

## Dashboard

Top:

- Large Flow card/button above shelves.
- Flow should feel like the main daily action.
- It can use richer visual treatment than normal shelves.

Shelves:

- Horizontal scroll rows.
- Section title on left.
- Optional "More" action and left/right arrows on right.
- Items are cover-first cards.
- Different shelves share visual structure even when recommendation logic differs.

Default shelves:

- Flow primary card.
- Listen Again.
- Long Time No Listen.
- Mixes For You.
- Albums For You.
- Recently Added.

Do not prioritize genre/energy/label shelves until tags and metrics are more
trustworthy.

## Media Cards

Track/album/playlist card:

- Square cover art.
- Rounded corners, small radius.
- Title below cover.
- Subtitle below title: artist, album, source, or summary.
- Optional play overlay on hover.
- Optional small badges: generated, analyzed, missing embedding, explicit, etc.

Sizing:

- Standard shelf card: 180-260px wide depending on shelf.
- Album cards: square cover with 2 text lines.
- Mix cards can use generated collage art later.

Behavior:

- Click card opens detail or starts playback depending on context.
- Hover shows play/action controls.
- Context menu available through overflow.

## Search Page

Reference: Deezer-style full results page.

Layout:

- Search input at top.
- Tabs under input: All, Artists, Tracks, Albums, Playlists.
- Results update reactively while typing.
- Keep URL state, e.g. `/search?q=juno%20reactor`.

All tab:

- Top Result block.
- Artists row.
- Tracks preview list.
- Albums row.
- Playlists row.

Top Result:

- Large card/panel.
- Main result on left.
- Optional best track/album on right.
- Primary actions: play top tracks, artist mix, open result.

Tracks:

- Dense table/list.
- Columns: track, artist, album, duration, liked/status/actions.
- Active row highlighted.
- Hover actions visible.

Albums/artists/playlists:

- Visual cards/rows.
- "View all" action for each section.

## Track Table

Use for search results, album tracks, playlists, queues, and history lists.

Columns:

- Cover/track title.
- Artist.
- Album.
- Duration.
- Optional popularity/status/analysis columns.
- Like and overflow actions.
- Selection checkbox in management views only.

Visual:

- Thin row dividers.
- Active/playing row highlighted.
- Hover row slightly lighter.
- Play icon appears on cover/left cell.

## Bottom Player

Persistent across all pages.

Collapsed layout:

- Left: previous, play/pause, next.
- Progress bar spans along top or within player.
- Center: cover thumbnail, title, artist, album/year.
- Center/right: like, dislike, overflow menu.
- Right: volume, repeat, shuffle/autoplay, expand arrow.

Behavior:

- Does not reset while navigating.
- Shows current progress.
- Supports keyboard shortcuts later.
- Expand arrow opens expanded player/queue view.

## Expanded Player

Desktop layout:

- Left/center: large cover artwork.
- Right: queue panel.
- Bottom player remains visible or becomes integrated.
- Background stays dark and focused.

Queue panel:

- Tabs: Up Next, Lyrics/Text, Related.
- Source label at top.
- Autoplay toggle.
- Save queue/playlist button.
- Source queue first.
- Autoplay/generated continuation below.
- Preference chips below autoplay header or between source and generated tracks.

Queue item:

- Small cover.
- Title.
- Artist.
- Duration.
- Active item highlighted.
- Generated/autoplay items can be visually grouped.

Behavior:

- Clicking visible queue item is navigation, not negative feedback.
- Skip/dislike are feedback.
- Preference chips update future generated items.

## Autoplay Preference Chips

Visual:

- Horizontal chip row.
- Active chip filled/light.
- Inactive chips dark gray.
- Scrollable if too many.

Candidate chips:

- All.
- Familiar.
- Recommended.
- Discovery.
- Same vibe.
- More variety.
- Energy up.
- Calmer.
- Less repeated artists.

These shift weights, not switch to entirely separate algorithms.

## Settings Page

Use tabs or top segmented navigation.

Tabs:

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

Visual:

- Dense forms.
- Grouped sections.
- Clear labels and descriptions.
- Use sliders, toggles, number inputs, selects, and buttons.
- Power-user controls are acceptable.

Avoid:

- one huge mixed settings page;
- hiding important controls behind vague labels.

## Detail Pages

There is no standalone track page in the target design. A track is always shown
inside an album, EP, single, compilation, playlist, queue, search result, or
generated mix. Even a one-track release is still represented as a release/album
page.

Navigation should mostly come from dashboard shelves, search, album pages,
artist pages, playlist/mix pages, and the player queue. A large generic browse
sidebar is not required for the player experience.

### Album Page

Purpose:

- present one release/album as a playable object;
- show its track list;
- provide navigation to the artist;
- surface related artist discography and recommendations below.

Reference layout:

- large cover on the left;
- album/release title as a very large heading on the right;
- artist row under title, clickable to artist pages;
- if the release has several known artists, show all participating artists in
  the header/card, not only the first artist;
- metadata row under artist: track count, duration, release date/year, optional
  local popularity/fan/play stats if available;
- action row below cover/header area;
- track table below;
- recommendation/related shelves below track table.

Header:

- cover size: large square, roughly 320-420px desktop depending on viewport;
- title: oversized, heavy, high-contrast;
- artist link: avatar optional, name clickable;
- metadata separated by small dividers;
- do not show tour or label blocks by default; label can appear only as subtle
  metadata later if reliable.

Primary actions:

- Play release.
- Like/save release.
- Share/export later if useful.
- Overflow menu.

Track table:

- album track order;
- small cover thumbnail can repeat album cover per row;
- track number;
- title;
- right-side actions: like, overflow;
- duration;
- optional local popularity/status in advanced mode;
- optional selection checkbox only in management mode.

Rows:

- active/playing row highlighted;
- hover shows play/action affordances;
- clicking track starts playback from that release context.

Below track list:

- artist discography shelf/section;
- recommended albums shelf placed below discography;
- similar artists shelf can appear lower if useful;
- no tour section for this app;
- no label section for now.

For multi-artist releases:

- discography can mix releases from all participating artists;
- recommended albums can also use all participating artists as context;
- ordering should avoid flooding the row with one artist when several artists
  are meaningful participants.

Recommended albums on album page:

- can include similar albums from the same artist;
- can include albums close to this album's aggregate embedding;
- can include albums matching the user's taste regions;
- should visually use the standard album card grid/shelf.

Empty/missing data:

- if cover is missing, use a neutral generated cover placeholder;
- if release date is missing, omit it from metadata row;
- if artist mapping is uncertain, show artist text but avoid broken artist link;
- if recommendations are unavailable, omit the recommendation shelf.

What not to show:

- no tour module;
- no prominent label module;
- no global fan count unless we later have reliable external data;
- no standalone track page links from track rows.

### Artist Page

Purpose:

- present one artist as a library entity;
- provide entry to artist mix/playback;
- organize the artist's releases;
- expose top tracks and related artists when supported by data.

Reference layout:

- large circular artist image/avatar on the left;
- artist name as very large heading;
- optional local stats under name;
- action row: artist mix/play, like/save artist, overflow;
- tabs or section navigation: Discography, Top tracks, Similar artists,
  Playlists, Bio.

Sidebar:

- no artist-specific sidebar needed;
- the global app sidebar remains only for primary sections and playlists.

Header:

- circular image/avatar if available;
- fallback generated image/initials if missing;
- artist name large and bold;
- local stats can replace external fan counts:
  - tracks in library;
  - albums/releases in library;
  - local play count if available;
  - liked track count if available.

Actions:

- Mix: starts artist-based mix/autoplay context;
- Like/save artist;
- Overflow menu.

Tabs:

- Discography.
- Top tracks.
- Similar artists.
- Playlists.
- Bio.

Tabs can be anchors/filters on one page rather than separate routes.

Top tracks:

- show only if there is enough data;
- in a local app this should be based on local play/completion/like data, not
  global popularity;
- if local data is missing, omit the section instead of showing fake popularity;
- use compact track list/table;
- include "View all" when there are more tracks.

Latest release:

- show the newest release by date if available;
- can be a small featured release block with cover, release date, and track
  preview;
- omit if release dates are unreliable.

Similar artists:

- show as circular artist cards or compact list;
- derive from embedding proximity, shared regions, co-occurrence in playlists,
  or later external metadata;
- if confidence is low, keep the section lower on the page.

Empty/missing data:

- if no artist image exists, use generated avatar/initials;
- if no local play data exists, omit Top Tracks;
- if release dates are unreliable, omit Latest Release or sort by known metadata
  only;
- if similar artists confidence is low, omit or move lower.

What not to show:

- no tour module;
- no social links unless they are reliable local/external metadata;
- no fake fan counts; use local stats instead.

### Artist Discography

Discography is the primary artist page content.

Group releases by type:

- Albums.
- EPs.
- Singles.
- Compilations.
- Featured In.
- Releases fallback.

Rules:

- Albums: releases where album/release artist is the selected artist and release
  type is album.
- EPs: release type EP.
- Singles: release type single.
- Compilations: release/album artist is the selected artist and release type is
  compilation, best-of, anthology, remix collection, or similar when known.
- Featured In: releases where the selected artist appears on one or more tracks,
  but the release/album artist is another artist or Various Artists.
- Releases fallback: when type is missing or uncertain, place in generic
  Releases instead of guessing too aggressively.

If release type metadata is missing:

- do not hide the release;
- group under Releases;
- optionally infer weakly from track count/duration only in advanced processing,
  but keep the original uncertainty.

Discography layout:

- section title with divider line;
- optional sort dropdown, default release date descending;
- album/release cards in a grid;
- card includes cover, title, artist, release date/year;
- "View all" for long sections;
- desktop grid density similar to the screenshots: many cards per row, compact
  text.

Featured In:

- use for compilations/samplers where the selected artist has track credits but
  is not the main release artist;
- include Various Artists releases here;
- card subtitle should show release artist/Various Artists;
- the selected artist's matching tracks can be shown inside release details.

### Playlist / Mix Page

- Cover or generated collage.
- Title.
- Description/subtitle.
- Generated/static status.
- Source/recipe metadata for generated mixes in advanced mode.
- Track table.
- Save/regenerate/settings actions for generated mixes.

Track page/details can be minimal at first because player and search provide most
track interactions.

## States

Loading:

- Skeleton cards/rows.

Empty:

- Short, direct empty state.
- Include action if useful, e.g. "Run scan", "Analyze embeddings".

Error:

- Inline error with retry where possible.
- Avoid blocking the whole page unless necessary.

Analysis/status:

- Use small badges, not noisy banners.
- Missing embeddings or model readiness should be visible in advanced contexts.

## Component List

Core components:

- `AppShell`
- `Sidebar`
- `Dashboard`
- `DashboardShelf`
- `MediaCard`
- `FlowHeroCard`
- `SearchPage`
- `SearchTabs`
- `TopResult`
- `TrackTable`
- `BottomPlayer`
- `ExpandedPlayer`
- `QueuePanel`
- `PreferenceChips`
- `SettingsPage`
- `SettingsTabs`
- `AlbumPage`
- `ArtistPage`
- `PlaylistPage`

## Open Visual Questions

- Exact accent color.
- Whether expanded player is full-page route, drawer, or overlay.
- Whether Flow card uses generated artwork, current taste collage, or abstract
  branded visual.
- How much analysis/debug metadata is visible outside advanced mode.
- Mobile layout strategy.
