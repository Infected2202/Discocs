import { create } from "zustand"
import type { PlaylistSummary } from "@/api/types"

const STORAGE_KEY = "discocs.uiState.v1"

function loadPersisted(): boolean {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return false
    const parsed = JSON.parse(raw) as { sidebarCollapsed?: boolean }
    return typeof parsed.sidebarCollapsed === "boolean" ? parsed.sidebarCollapsed : false
  } catch {
    return false
  }
}

function persist(collapsed: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ sidebarCollapsed: collapsed }))
  } catch {
    // ignore
  }
}

export interface CreatePlaylistValues {
  title: string
  description: string
  visibility: "public" | "private"
}

export interface CreatePlaylistDialogOptions {
  /** Prefill for the form (e.g. mix title when saving a mix). */
  defaultTitle?: string
  defaultDescription?: string
  /** Tracks to add to the playlist right after creation. */
  trackIds?: number[]
  /** Edit mode: PATCH this playlist instead of creating a new one. */
  playlist?: PlaylistSummary
  /** Replaces the default create call entirely (e.g. mix save flow). */
  onSubmit?: (values: CreatePlaylistValues) => Promise<void>
}

interface UIState {
  sidebarCollapsed: boolean
  toggleSidebar(): void
  /** Tracks pending "add to playlist"; null = dialog closed. */
  addToPlaylistTrackIds: number[] | null
  /** Prefill for the "New playlist" title if the user creates one from here (e.g. current playback source). */
  addToPlaylistDefaultTitle: string | null
  openAddToPlaylist(trackIds: number[], defaultTitle?: string): void
  closeAddToPlaylist(): void
  /** Options for the create/edit playlist dialog; null = dialog closed. */
  createPlaylistOptions: CreatePlaylistDialogOptions | null
  openCreatePlaylist(options?: CreatePlaylistDialogOptions): void
  closeCreatePlaylist(): void
  resetForLogout(): void
}

export const useUIStore = create<UIState>((set, get) => ({
  sidebarCollapsed: loadPersisted(),
  toggleSidebar() {
    const next = !get().sidebarCollapsed
    set({ sidebarCollapsed: next })
    persist(next)
  },
  addToPlaylistTrackIds: null,
  addToPlaylistDefaultTitle: null,
  openAddToPlaylist(trackIds, defaultTitle) {
    set({ addToPlaylistTrackIds: trackIds, addToPlaylistDefaultTitle: defaultTitle ?? null, createPlaylistOptions: null })
  },
  closeAddToPlaylist() {
    set({ addToPlaylistTrackIds: null, addToPlaylistDefaultTitle: null })
  },
  createPlaylistOptions: null,
  openCreatePlaylist(options) {
    set({ createPlaylistOptions: options ?? {}, addToPlaylistTrackIds: null })
  },
  resetForLogout() {
    set({
      addToPlaylistTrackIds: null,
      addToPlaylistDefaultTitle: null,
      createPlaylistOptions: null,
    })
  },
  closeCreatePlaylist() {
    set({ createPlaylistOptions: null })
  },
}))
