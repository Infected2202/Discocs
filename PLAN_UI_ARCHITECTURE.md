# UI Rewrite — Architecture

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Runtime | Node 24 + pnpm 9 | Node 24 is current stable; pnpm 9 is fast, strict, workspace-ready |
| Build | Vite + TypeScript | Standard, fast HMR, simple prod build |
| UI | React 19 | User requirement |
| Routing | React Router v7 | Loader pattern, URL-driven state |
| Server state | TanStack Query | Caching, loading/error states, refetch |
| Client state | Zustand | Player/queue state — lightweight, no boilerplate |
| Components | shadcn/ui | Pre-built components on Radix UI, code lives in repo, fully editable |
| Styles | Tailwind CSS v4 | Utility classes, co-located with JSX, works with shadcn/ui |
| Mobile | Capacitor (later) | Single codebase → iOS/Android |
| Desktop | Electron (later) | Native window, local FastAPI subprocess |

---

## Project layout

```
discocs/
  app/           — Python backend (unchanged)
  ui/
    src/
      api/            — fetch client + TanStack Query hooks
      engine/         — AudioEngine singleton
      store/          — Zustand stores
      components/     — shared UI components
        ui/           — shadcn/ui generated components (button, dialog, slider, etc.)
      pages/          — route-level components
    index.html
    vite.config.ts
    tailwind.config.ts
    package.json
    tsconfig.json
  PLAN_UI_REWRITE.md
  PLAN_UI_ARCHITECTURE.md
```

---

## AudioEngine

The `<audio>` element is a singleton that lives outside React — creating it inside a component would risk it being destroyed on unmount. The engine is instantiated once at module load, before React renders.

```
engine/AudioEngine.ts
  - Creates HTMLAudioElement, attaches event listeners
  - load(url)  play()  pause()  seek(fraction)  setVolume(v)  setMuted(b)
  - On timeupdate / ended / error / play / pause → writes to playerStore
  - On ended → calls playerStore.handleTrackEnded() which triggers next-track logic
```

The engine never reads React state — it only writes to the store. Components only interact with the store, never with the engine directly. This keeps the engine testable in isolation.

**Media Session API** is set up inside AudioEngine on `play()`:
```typescript
navigator.mediaSession.metadata = new MediaMetadata({ title, artist, artwork });
navigator.mediaSession.setActionHandler('nexttrack', () => store.skipNext());
// etc.
```
This gives free OS-level controls and lock screen integration on mobile.

---

## Player Store (Zustand)

```typescript
interface PlayerStore {
  // Playback session (server-managed)
  session: PlaybackSession | null;
  queue: PlaybackQueue | null;
  currentTrackId: number | null;
  currentQueueItemId: number | null;

  // Audio element state (written by AudioEngine)
  playbackState: 'idle' | 'loading' | 'playing' | 'paused' | 'error';
  currentTime: number;
  duration: number;
  volume: number;
  muted: boolean;
  error: string | null;

  // Actions (called by components)
  playSource(type: string, id: number, label: string, preferredTrackId?: number): Promise<void>;
  playTrack(id: number, label: string, opts?: { queueItemId?: number }): Promise<void>;
  togglePlay(): void;
  seek(fraction: number): void;
  skipNext(): Promise<void>;
  skipPrevious(): Promise<void>;
  toggleShuffle(): Promise<void>;
  toggleRepeatOne(): Promise<void>;
  toggleAutoplay(): Promise<void>;
  setAutoplayChip(chip: string): Promise<void>;
  setVolume(v: number): void;
  toggleMute(): void;
  refreshQueue(): Promise<void>;
  recordEvent(type: string, extra?: object): Promise<void>;
  handleTrackEnded(): Promise<void>;   // called by AudioEngine
}
```

Volume + mute are persisted to `localStorage` on change (replaces current `PLAYER_STATE_KEY`).

---

## API layer

```
api/
  client.ts      — typed fetch wrapper (mirrors current json() function)
  dashboard.ts   — fetchDashboard()
  search.ts      — fetchSearch()
  artists.ts     — fetchArtist(), fetchArtistDiscography(), fetchArtistTopTracks()
  releases.ts    — fetchRelease(), fetchReleaseTracks(), fetchReleaseRelated()
  mixes.ts       — fetchMix(), fetchMixTracks(), saveMix()
  playback.ts    — createSession(), fetchQueue(), postEvent(), refillAutoplay()
  settings.ts    — fetchNavidromeSettings(), saveNavidromeSettings(), pingNavidrome()
  hooks/
    useDashboard.ts
    useSearch.ts
    useArtist.ts
    useRelease.ts
    useMix.ts
```

All hooks are thin wrappers over `useQuery` / `useMutation`. No data transformation inside hooks — raw API shape flows to components, which format for display.

---

## Routing

```
/                  DashboardPage    — shelves + search bar
/search?q=...      SearchPage       — tabbed: all / artists / releases / tracks
/artists/:id       ArtistPage       — header, top tracks, discography
/releases/:id      ReleasePage      — header, track list, related, recommended
/mixes/:id         MixPage          — header, track list
```

React Router v7 loaders pre-fetch page data before rendering, so Artist/Release/Mix pages don't flash a loading spinner on navigation.

---

## Layout

```
<AppShell>
  <Sidebar>          220px fixed left, nav links
  <div class="content-area">
    <TopBar>          right side: profile button + Navidrome status dot
    <Outlet />        page content scrolls here
  </div>
  <PlayerBar />      fixed bottom, 92px (always mounted, never unmounts)
  <ExpandedPlayer /> overlay, toggled by playerStore.expanded flag
</AppShell>
```

`PlayerBar` and `ExpandedPlayer` are always mounted — they hold the queue UI and must not lose state on page navigation.

---

## Component map

```
components/
  layout/
    AppShell.tsx
    Sidebar.tsx
    TopBar.tsx
  ui/               — shadcn/ui primitives: Button, Dialog, Slider, DropdownMenu,
                      Popover, Tabs, Skeleton, Badge, Tooltip, ScrollArea
  player/
    PlayerBar.tsx        — mini bar: seek, controls, now-playing, volume, expand
    ExpandedPlayer.tsx   — overlay: artwork, queue list, autoplay panel
    QueueItem.tsx
    AutoplayChips.tsx
  media/
    MediaCard.tsx        — artist/release card (shelves + search)
    Shelf.tsx            — horizontal scroll row with header
    TrackRow.tsx         — table row: #, play, cover, title, release, duration, menu
    TrackTable.tsx
    TrackMenu.tsx        — DropdownMenu: go to artist, go to release, play next
    ArtworkImage.tsx     — img with letter placeholder fallback
  search/
    SearchBar.tsx        — input with 300ms debounce + Enter submit
    SearchResults.tsx
  profile/
    ProfileButton.tsx    — avatar button in TopBar
    SettingsDialog.tsx   — shadcn Dialog: Navidrome URL/user/password/test
```

---

## Styles

**Tailwind CSS v4 + shadcn/ui theming.**

shadcn/ui uses CSS variables for its theme. We map those variables to the existing discocs color palette so shadcn components inherit the dark aesthetic without looking generic:

```css
/* index.css — Tailwind base + theme mapping */
@import "tailwindcss";

:root {
  --background: #0b0d0f;          /* --surface-0 */
  --foreground: #eef2f3;          /* --text */
  --card: #171a1d;                /* --surface-2 */
  --border: #2d3033;              /* --stroke */
  --primary: #ff2a6d;             /* --accent */
  --primary-foreground: #07110e;
  --muted: #242629;               /* --surface-3 */
  --muted-foreground: #aeb8bc;    /* --muted text */
  --ring: #ff2a6d;
  --radius: 0.375rem;             /* 6px — matches current border-radius */
}
```

All custom layout (PlayerBar, Shelf, MediaCard) uses Tailwind utility classes. shadcn/ui components (Button, Dialog, Slider, etc.) come pre-styled and match the theme above out of the box.

Scrollbar styling and a few complex layout rules go in `index.css` as regular CSS where Tailwind utilities would be awkward.

---

## Backend integration

During development: Vite proxy forwards `/api`, `/tracks`, `/artists`, `/releases`, `/mixes`, `/settings`, `/stats`, `/jobs`, `/feedback` to `localhost:<backend_port>`.

Production: `main.py` mounts `ui/dist/` as a FastAPI `StaticFiles` app and adds a catch-all that serves `index.html`:

```python
from fastapi.staticfiles import StaticFiles

app.mount("/assets", StaticFiles(directory="ui/dist/assets"), name="ui-assets")

@app.get("/app/{full_path:path}", response_class=HTMLResponse)
def new_ui(full_path: str) -> HTMLResponse:
    return HTMLResponse((Path(__file__).parent.parent / "ui/dist/index.html").read_text())
```

Old UI stays on `/` untouched. New UI lives on `/app` during development. Cutover = swap the routes.

---

## What we're NOT porting

Deliberately excluded from the new UI:

- Operations (scan, analyze, index pipeline)
- Jobs / Workers monitor
- Metrics explorer
- Lost files / Errored files management
- Evaluation / rating sessions
- Browse facets
- Text search section
- Navidrome likes section (likes are exposed via the like button in PlayerBar)
- Settings page (collapsed into Profile panel)

These remain available in the old UI at `/admin`. The old UI gets a persistent warning banner at the top ("Admin panel — operational and debug tools") so it's visually distinct. No login required — admin route is local-only.

---

## Future: Capacitor (iOS/Android)

Once the web app is solid:

1. `npm install @capacitor/core @capacitor/cli @capacitor/ios @capacitor/android`
2. `npx cap init discocs com.discocs.app`
3. `capacitor.config.ts` points `webDir` to `ui/dist`
4. The app hits a configurable backend URL (saved in profile settings)
5. Safe area insets via CSS env variables (`safe-area-inset-*`)
6. Status bar color: `@capacitor/status-bar`

---

## Future: Electron

1. `electron-vite` wraps the existing `ui/` source
2. Main process: either connects to a user-specified backend URL, or launches the FastAPI server as a child process
3. Window chrome: frameless + custom title bar (optional)
4. Auto-update: `electron-updater`
