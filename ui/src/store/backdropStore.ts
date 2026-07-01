import { create } from "zustand"

interface BackdropStore {
  artworkUrl: string | null
  setArtworkUrl: (url: string | null) => void
}

export const useBackdropStore = create<BackdropStore>((set) => ({
  artworkUrl: null,
  setArtworkUrl: (url) => set({ artworkUrl: url }),
}))
