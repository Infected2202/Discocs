---
name: ui-rewrite-phases
description: Auditing and implementing the phased UI rewrite plan — tracking which phases are committed, finding uncommitted work, detecting inlined code, and completing remaining phases
source: auto-skill
extracted_at: '2026-06-28T06:25:32.407Z'
---

# UI Rewrite Phase Tracking and Implementation

## Context

The discocs project has a multi-phase UI rewrite plan documented in `PLAN_UI_REWRITE.md` (phased checklist) and `PLAN_UI_ARCHITECTURE.md` (architecture decisions, component map, stack rationale). The new UI lives in `ui/` (Vite + React 19 + TypeScript + shadcn/ui + Tailwind v4 + Zustand + TanStack Query).

Phases 0–11 are defined. Commits follow the convention: `feat(ui): phase N — <description>`.

## How to audit which phases are implemented

1. **Read both plan documents** — `PLAN_UI_REWRITE.md` has the per-phase checklists; `PLAN_UI_ARCHITECTURE.md` has the component map showing what files each phase should produce.

2. **Check git log for committed phases:**
   ```
   git log --oneline -20
   ```
   Look for `feat(ui): phase N` commits. Each committed phase is done.

3. **Check working tree for uncommitted phase work:**
   ```
   git diff --stat HEAD
   ```
   Large diffs in `ui/src/` indicate in-progress phases not yet committed.

4. **Verify actual implementation vs plan checklist** — the plan says which files should exist per phase. Cross-reference:
   - `ls ui/src/components/<dir>/` to see which component files exist
   - Read the actual source files to check if components are real implementations or stubs
   - Check if the plan called for separate files but code was **inlined** instead (e.g., plan says `QueueItem.tsx` but the component was inlined inside `ExpandedPlayer.tsx`)

5. **Identify partial phases** — a phase may have all pages/components present but with gaps:
   - Missing sub-components (plan lists them as separate files but they're inlined or absent)
   - Plan deviations (e.g., SettingsPage as a route instead of a dialog from ProfileButton)
   - Stub components not yet wired up (e.g., TopBar with empty placeholder instead of ProfileButton)

## Fixing inlined components

When the plan calls for a separate component file but the code is inlined:

1. Read the host file to find the inlined component
2. Create the separate file with the component's full implementation, including all its imports
3. In the host file:
   - Remove the inlined component definition
   - Add an import for the new separate component
   - Ensure any newly-unused imports are removed (e.g., `apiFetch`, `patchQueue`, icon imports that were only used by the inlined component)
   - Add back imports the host file still needs (e.g., `Link`, icons used by the host's own JSX)
4. Replace all usages of the old inlined name with the imported component name
5. Verify with `grep` that no stale references remain and no imports are unused

## Phase status (as of 2026-06-28)

| Phase | Status | Commit |
|---|---|---|
| 0 — Project setup | ✅ Done | `02cf0ca` |
| 1 — API client + Query hooks | ✅ Done | `075e2e2` |
| 2 — AudioEngine + Zustand stores | ✅ Done | `841396d` |
| 3 — Layout shell | ✅ Done | `278d5b4` |
| 4 — Shared media components | ✅ Done | `cb47e1d` |
| 5 — Pages | ✅ Done | `25f23ad` |
| 6 — Player bar (full) | ✅ Done | `25f23ad` + `af695e1` (QueueItem extracted) |
| 7 — Profile & Navidrome settings | ✅ Done | `25f23ad` (SettingsPage) + `efd2249` (ProfileButton) + `a007bd1` (sidebar cleanup) |
| 8 — Polish & responsive | ✅ Done (uncommitted) | Implemented in working tree |
| 9 — Containerised deployment | ❌ Not started |
| 10 — Capacitor | 🔄 In progress (analysis complete, install blocked by classifier) |
| 11 — Electron | ❌ Not started |

### Phase 8 implementation details

Phase 8 was implemented in a single working-tree pass (not yet committed). Some plan items were already done by earlier phases; the remaining items were completed as follows:

**Already done before Phase 8:**
- Skeleton screens — every page (`DashboardPage`, `SearchPage`, `ArtistPage`, `ReleasePage`, `MixPage`) already had `*Skeleton` components using shadcn `<Skeleton>`.
- Scrollbar styling — already in `ui/src/index.css` (6px webkit scrollbar, `#2d3033` thumb).
- Collapsible sidebar — done in post-Phase-7 commits (`285bfa2`, `9c9fbac`, `b665992`).

**New files created in Phase 8:**
- `ui/src/components/common/ErrorBoundary.tsx` — React class error boundary with "Something went wrong" + retry button (resets `hasError`). Wrapped around `<Outlet>` inside AppShell.
- `ui/src/components/common/PageTransition.tsx` — Fade transition on route change. Uses `useLocation` to detect path changes, toggles opacity 0→1 with `transition-opacity duration-200 ease-out` and an 80ms timeout.
- `ui/src/components/layout/MobileTabBar.tsx` — Bottom tab bar shown only on `md:hidden` viewports. Contains Home, Search NavLinks + `<ProfileButton mobile>`. Fixed at `bottom-[92px]` (above PlayerBar height).
- `ui/src/hooks/useKeyboardShortcuts.ts` — Global keyboard handler. Space=play/pause, ArrowLeft/Right=seek ±10s, KeyM=mute. Ignores keys when focus is in input/textarea/select/contentEditable via `isTypingTarget()` helper. Uses `audioEngine.seekToSeconds()` directly (not store `seek()` which takes a fraction).
- `ui/src/hooks/useTrackTitle.ts` — Updates `document.title` to `"Title · Artist — discocs"` when a track is playing, falls back to `"discocs"` when idle.
- `ui/public/favicon.svg` — Custom SVG favicon (dark rounded rect with pink record/vinyl disc motif matching the `--primary` color `#ff2a6d`).

**Modified files in Phase 8:**
- `ui/index.html` — favicon link → `/favicon.svg`, added `<meta name="theme-color" content="#0b0d0f">`, title → `"discocs"`.
- `ui/src/components/layout/AppShell.tsx` — major restructure for responsive + Phase 8 hooks:
  - Imports and calls `useKeyboardShortcuts()` and `useTrackTitle()` at top level.
  - TopBar wrapped in `<div className="hidden md:block shrink-0">` — hidden on mobile.
  - Sidebar wrapped in `<div className="hidden md:block h-full">` — hidden on mobile.
  - `<MobileTabBar />` added between content area and PlayerBar.
  - `<ErrorBoundary>` wraps `<PageTransition>` wraps `<Outlet>`.
  - Main content `pb` adjusted: `pb-[92px] md:pb-[92px] max-md:pb-[148px]` — extra bottom padding on mobile to account for tab bar (56px) + player bar (92px).
- `ui/src/components/player/ExpandedPlayer.tsx` — always mounted (removed `if (!expanded) return null`), uses `cn()` with `translate-y-full pointer-events-none` when collapsed and `translate-y-0` when expanded, with `transition-transform duration-300 ease-out will-change-transform`. Content renders in both states; only CSS transform animates.
- `ui/src/components/profile/ProfileButton.tsx` — added optional `{ mobile }` prop to control Popover alignment (`"center"` on mobile, `"end"` on desktop).
- `ui/src/router.tsx` — unchanged structure (ErrorBoundary is in AppShell, not per-route).

**Verification approach for Phase 8:**
1. `npx tsc --noEmit` — 0 type errors.
2. `npx vite build` — successful production build (warnings about chunk size and ineffective dynamic imports are pre-existing, not from Phase 8).
3. Playwright MCP verification:
   - `browser_navigate` to `http://localhost:4173` (Vite preview) — title shows "discocs".
   - Desktop (1920×1080): TopBar + Sidebar visible, content renders, 0 console errors.
   - Mobile (375×812 via `browser_resize`): TopBar + Sidebar hidden, MobileTabBar with Home/Search/Profile visible, 0 console errors.

### Phase 8 mobile responsive fixes (second pass)

After initial Phase 8 verification, the user reported "мобильный вид весьма кривой" (mobile view is very broken) and "у полок должны быть скроллбары" (shelves must have scrollbars). A second pass fixed these issues:

**Root cause — Radix ScrollArea failing on mobile:**
The `Shelf` component used Radix `ScrollArea` + `ScrollBar` for horizontal scrolling. On mobile (and even desktop), Radix ScrollArea's Viewport did not properly constrain width — the inner flex container expanded to `scrollWidth` (e.g., 2180px) instead of the parent's `offsetWidth` (375px), causing the entire shelf row to overflow the viewport and break the page layout. The native scrollbar was also not reliably visible.

**Fix — replace Radix ScrollArea with native overflow:**
- `ui/src/components/media/Shelf.tsx` rewritten: replaced `<ScrollArea>`/`<ScrollBar>` with a plain `<div className="shelf-scroll overflow-x-auto overflow-y-hidden">` wrapper. Inner container uses `w-max` so it sizes to content.
- `ui/src/index.css` — added `.shelf-scroll` CSS class with custom scrollbar styling: `scrollbar-width: thin`, `scrollbar-color: #3d4347 transparent`, and `::-webkit-scrollbar` overrides (4px height, `#3d4347` thumb, semi-transparent track). This ensures the scrollbar is always visible and styled consistently across browsers.

**Adaptive card sizes:**
- `ui/src/components/media/MediaCard.tsx` — card width changed from fixed `w-44` to `w-36 sm:w-44` (smaller on mobile, normal on desktop).

**Responsive page padding:**
- All pages (`DashboardPage`, `SearchPage`, `ArtistPage`, `ReleasePage`, `MixPage`, `SettingsPage`) — `px-6` replaced with `px-4 sm:px-6` for narrower mobile margins.

**Responsive page headers (Artist/Release/Mix):**
- Header flex containers changed from `flex gap-6 items-end` to `flex flex-col sm:flex-row gap-4 sm:gap-6 items-start sm:items-end` — stacks artwork above text on mobile, horizontal layout on desktop.
- Padding adjusted from `pb-2` to `pb-0 sm:pb-2`.

**TrackRow like button on mobile:**
- `ui/src/components/media/TrackRow.tsx` — like button opacity changed from `opacity-0 group-hover/row:opacity-100` to `opacity-100 md:opacity-0 md:group-hover/row:opacity-100` — always visible on mobile (no hover state on touch devices).

**PlayerBar mobile cleanup:**
- `ui/src/components/player/PlayerBar.tsx` — secondary controls hidden on mobile:
  - Time display: `hidden md:inline`
  - Dislike button: `hidden md:flex`
  - TrackMoreMenu: wrapped in `<div className="hidden md:block">`
  - VolumeControl: wrapped in `<div className="hidden md:flex items-center">`
  - Repeat/Shuffle/Autoplay: each given `hidden md:flex`
  - Only prev/play/next + like + expand remain on mobile.

**Vite proxy bypass for SPA routing:**
- `ui/vite.config.ts` — added `bypass` function to each proxy entry: if the request's `Accept` header includes `text/html` (browser navigation), return `/index.html` instead of proxying to the backend. Without this, navigating to `/artists/1` on the dev server would proxy to the FastAPI backend and return the old admin UI instead of the React SPA.
```typescript
bypass: (req) => {
  const accept = req.headers.accept ?? ""
  if (accept.includes("text/html")) return "/index.html"
},
```

**Verification approach for Phase 8 mobile fixes:**
1. `npx tsc --noEmit` + `npx vite build` — 0 errors, successful build.
2. Playwright MCP with `vite dev` server (port 5173, proxies to backend on 8711):
   - Mobile (375×812): shelves scroll properly (`offsetWidth: 375, scrollWidth: 1056, canScroll: true, scrollbarWidth: "thin"`), page headers stack vertically (`flexDirection: "column"`), MobileTabBar visible, TopBar/Sidebar hidden, PlayerBar shows only essential controls.
   - Desktop (1920×1080): Sidebar 220px visible, TopBar 56px visible, MobileTabBar `display: none`, shelves scroll.
   - 0 console errors on all pages tested (/, /artists/1, /releases/1, /search?q=seba).

### Pattern: Use native overflow-x-auto for horizontal scroll shelves

**Problem:** Radix `ScrollArea` does not reliably constrain width on mobile. The Viewport expands to content width, breaking page layout. The custom scrollbar is also not guaranteed to be visible.

**Fix:** Use native `overflow-x-auto` with a custom scrollbar CSS class:
```tsx
<div className="shelf-scroll overflow-x-auto overflow-y-hidden">
  <div className="flex gap-1 px-3 pb-3 w-max">
    {items.map(...)}
  </div>
</div>
```
```css
.shelf-scroll {
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: #3d4347 transparent;
}
.shelf-scroll::-webkit-scrollbar { height: 4px; }
.shelf-scroll::-webkit-scrollbar-thumb { background: #3d4347; border-radius: 2px; }
```

**Key takeaway:** Avoid Radix ScrollArea for horizontal shelf scrolling. Native `overflow-x-auto` is more reliable across devices and easier to style.

### Pattern: Vite proxy bypass for SPA routes

When the Vite dev server proxies API paths (e.g., `/artists`, `/releases`) to a backend, SPA client-side routes with the same path prefix (e.g., `/artists/1`) get intercepted by the proxy and return the backend's HTML instead of `index.html`. Fix by adding a `bypass` function that checks the `Accept` header — browser navigation requests include `text/html`, API/XHR requests do not.

### Post-Phase-7 layout improvements (beyond plan)

After Phase 7, the layout was restructured for a better UX:

1. **Collapsible sidebar** (`285bfa2`) — sidebar collapses from 220px to 64px (icon-only mode with Radix Tooltips). State persisted in `uiStore.ts` via `localStorage` key `discocs.uiState.v1`. Toggle button (`PanelLeftClose`/`PanelLeftOpen` from lucide) placed in TopBar.
2. **Full-width TopBar** — TopBar now spans the entire width (was inside the right column). Logo moved from Sidebar to TopBar. AppShell restructured: vertical flex (TopBar on top → horizontal flex of Sidebar + content below).
3. **Settings link removed from sidebar** (`a007bd1`) — settings is now only accessible via ProfileButton popover in TopBar.

### Pattern: Zustand store with localStorage persist

When adding new persistent UI state (e.g., sidebar collapse), follow the pattern from `playerStore.ts`:
- Store key: `discocs.<storeName>.v1`
- `loadPersisted()` reads and validates JSON from localStorage with try/catch fallback
- `persist()` writes JSON to localStorage with try/catch (ignore errors)
- Store created with `create<State>()`, initial state from `loadPersisted()`, action calls `persist()` after `set()`

### Pattern: Radix Slot + NavLink className function bug

**Problem:** When a React Router `<NavLink>` with function-form `className={({ isActive }) => ...}` is wrapped inside a Radix `asChild` component (e.g., `<TooltipTrigger asChild>`, `<PopoverTrigger asChild>`, `<DialogTrigger asChild>`), Radix Slot intercepts the `className` prop and converts the function to a string via `.toString()`. The result: the function body becomes the literal class string, all Tailwind classes are lost, `display: flex` silently degrades to `display: inline`, and padding/gap/justify all disappear.

**Symptoms:** Nav links render with `display: inline` instead of `flex`, icons are glued to the left edge, spacing classes have no effect, and the rendered class attribute contains the raw function source code.

**Detection:** Use Playwright `browser_evaluate` to inspect computed styles:
```js
() => {
  const links = document.querySelectorAll('aside nav a');
  return Array.from(links).map(a => ({
    text: a.textContent,
    display: getComputedStyle(a).display,  // "inline" instead of "flex"
    className: a.className,                 // contains function source, not class names
  }));
}
```

**Fix:** Replace the function-form `className` with a pre-computed string using `useLocation()`:
```tsx
import { NavLink, useLocation } from "react-router"
const location = useLocation()
function isActive(to: string, end: boolean) {
  return end ? location.pathname === to : location.pathname.startsWith(to)
}
const cls = cn("flex items-center ...", isActive(to, end) ? "bg-accent" : "text-muted")
// Pass cls as a plain string, not a function:
<NavLink to={to} end={end} className={cls}>...</NavLink>
```

**Key takeaway:** Never pass a function to `className` on any component that will be a child of Radix `asChild`/Slot. Always pre-compute the class string.

### Phase 7 deviations from plan (accepted)

- **SettingsPage** is a standalone route at `/settings` instead of a Dialog opened from ProfileButton (plan called for `SettingsModal.tsx` as a Radix Dialog)
- **ProfileButton** is a Popover (not a separate ProfilePanel component) showing Navidrome connection status dot + "Open settings" link → navigates to `/settings`
- **Settings link removed from Sidebar** — originally added as a bottom nav link, removed after ProfileButton was created; settings is only in TopBar popover now
- **AutoplayChips** component was dropped — autoplay is a simple toggle switch, `setAutoplayChip` exists in store but has no chip UI (user explicitly confirmed chips are not needed)

### Phase 6 deviations from plan (resolved)

- **QueueItem.tsx** was originally inlined as `QueueTrackRow` inside `ExpandedPlayer.tsx` — extracted to `ui/src/components/player/QueueItem.tsx` in commit `af695e1`

### Phase 10 — Capacitor (in progress, analysis complete)

**Core challenge:** The UI uses relative paths for all API calls (`apiFetch("/api/...")`, `trackAudioUrl()` returns `/tracks/${id}/audio`). This works in the browser because the Vite dev server proxies API paths to the backend, and in production nginx serves static files + reverse-proxies API paths. In Capacitor, the WebView loads from `capacitor://localhost` (iOS) or `http://localhost` (Android), so relative paths resolve to the local WebView origin — all API calls fail.

**Required files to modify for Capacitor:**

1. **`ui/src/api/client.ts`** — `apiFetch()` and `apiUrl()` must prepend a configurable backend base URL when running inside Capacitor. Detection: `Capacitor.isNativePlatform()`. The base URL is stored at runtime (not build-time) in Capacitor Preferences.

2. **`ui/src/api/playback.ts`** — `trackAudioUrl()` currently returns `/tracks/${trackId}/audio` (relative). Inside Capacitor, this must return an absolute URL to the backend. The AudioEngine's `HTMLAudioElement.src` needs an absolute URL to stream from the backend server.

3. **`ui/src/index.css`** — Add `safe-area-inset-*` CSS for PlayerBar (bottom) and MobileTabBar (bottom). The PlayerBar is `fixed bottom-0` and MobileTabBar is `fixed bottom-[92px]`. On notched devices, content under the home indicator is inaccessible without safe area padding.

4. **`ui/src/pages/SettingsPage.tsx`** — Add a "Server" section for configuring the backend URL. Currently only has Navidrome settings. The backend URL field must use `@capacitor/preferences` on native and `localStorage` on web.

5. **`ui/src/main.tsx`** — Initialize `@capacitor/status-bar` plugin: set background color to `#0b0d0f` (`--background` / `--surface-0`), style to dark. Call `StatusBar.setBackgroundColor({ color: '#0b0d0f' })` and `StatusBar.setStyle({ style: Style.Dark })` guarded by `Capacitor.isNativePlatform()`.

6. **`capacitor.config.ts`** (new) — `webDir: 'dist'`, appId `com.discocs.app`, appName `discocs`. No `server.url` needed for production builds.

**Capacitor dependency installation (blocked by classifier):**

The `pnpm add` command for Capacitor packages was blocked by the auto-mode safety classifier. The command to run:
```bash
cd ui
pnpm add @capacitor/core @capacitor/status-bar @capacitor/preferences
pnpm add -D @capacitor/cli @capacitor/android @capacitor/ios
```

Per feedback memory: when the classifier blocks a command, retry once or ask the user for manual approval. Do not try indirect workarounds.

**Runtime config pattern for backend URL:**

Create a `lib/runtimeConfig.ts` module:
- On native: read from `@capacitor/preferences` (async API — `Preferences.get({ key: 'backendUrl' })`)
- On web: read from `localStorage` (`localStorage.getItem('discocs.backendUrl')`)
- Default: `http://localhost:8711` (matches `vite.config.ts` BACKEND constant)
- The `apiFetch` and `trackAudioUrl` functions must resolve the base URL before making requests. Since Capacitor Preferences is async, either:
  - Eagerly load the backend URL on app startup (before React renders), store in a module variable
  - Or use a synchronous accessor that falls back to a cached value loaded at init

**Safe area insets CSS:**
```css
/* PlayerBar — bottom safe area */
.player-bar-safe {
  padding-bottom: env(safe-area-inset-bottom);
}
/* MobileTabBar — above player, needs bottom safe area too */
.mobile-tab-bar-safe {
  padding-bottom: env(safe-area-inset-bottom);
}
```
The PlayerBar height (72px) + safe area inset must be accounted for in AppShell's `pb` calculation. Currently `max-md:pb-[148px]` (92px player + 56px tab bar). With safe area, this needs to be dynamic or use `calc()`.

**Platform-specific considerations:**
- iOS: WebView origin is `capacitor://localhost`, no CORS issues (Capacitor handles native HTTP)
- Android: WebView origin is `http://localhost`, may need CORS or `capacitor.config` server settings
- `@capacitor/status-bar` is iOS/Android only — guard with `Capacitor.isNativePlatform()`
- Audio playback: `HTMLAudioElement` works in WebView but may need `WKWebView` configuration on iOS for background audio

## Key file locations

- Plans: `PLAN_UI_REWRITE.md`, `PLAN_UI_ARCHITECTURE.md` (repo root)
- UI source: `ui/src/`
- Player store: `ui/src/store/playerStore.ts`
- Navidrome store: `ui/src/store/navidromeStore.ts`
- UI store: `ui/src/store/uiStore.ts` (sidebar collapse state)
- Audio engine: `ui/src/engine/AudioEngine.ts`
- API hooks: `ui/src/api/hooks/`
- shadcn components: `ui/src/components/ui/`
- Layout: `ui/src/components/layout/` (AppShell, Sidebar, TopBar, MobileTabBar)
- Player: `ui/src/components/player/` (PlayerBar, ExpandedPlayer, QueueItem)
- Media: `ui/src/components/media/` (MediaCard, Shelf, TrackRow, TrackTable, TrackMenu, ArtworkImage)
- Common: `ui/src/components/common/` (ErrorBoundary, PageTransition)
- Hooks: `ui/src/hooks/` (useKeyboardShortcuts, useTrackTitle)
- Profile: `ui/src/components/profile/` (ProfileButton)
- Pages: `ui/src/pages/` (DashboardPage, SearchPage, ArtistPage, ReleasePage, MixPage, SettingsPage)
- Router: `ui/src/router.tsx`
