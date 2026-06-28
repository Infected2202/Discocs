import { create } from "zustand"

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

interface UIState {
  sidebarCollapsed: boolean
  toggleSidebar(): void
}

export const useUIStore = create<UIState>((set, get) => ({
  sidebarCollapsed: loadPersisted(),
  toggleSidebar() {
    const next = !get().sidebarCollapsed
    set({ sidebarCollapsed: next })
    persist(next)
  },
}))
