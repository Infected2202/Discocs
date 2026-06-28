import { create } from "zustand"
import { apiFetch } from "@/api/client"
import { usePlayerStore } from "./playerStore"

interface NavidromeStore {
  likedIds: Set<number>
  isLiked(trackId: number): boolean
  toggleLike(trackId: number): Promise<void>
  fetchLikedIds(): Promise<void>
}

export const useNavidromeStore = create<NavidromeStore>((set, get) => ({
  likedIds: new Set(),

  isLiked(trackId) {
    return get().likedIds.has(trackId)
  },

  async toggleLike(trackId) {
    const liked = get().likedIds.has(trackId)

    // Optimistic update
    const next = new Set(get().likedIds)
    if (liked) {
      next.delete(trackId)
    } else {
      next.add(trackId)
    }
    set({ likedIds: next })

    try {
      await apiFetch(`/tracks/${trackId}/navidrome-star`, {
        method: "PUT",
        body: JSON.stringify({ starred: !liked }),
      })
      const eventType = liked ? "disliked" : "liked"
      await usePlayerStore.getState().recordEvent(eventType)
    } catch {
      // Revert optimistic update
      const reverted = new Set(get().likedIds)
      if (liked) {
        reverted.add(trackId)
      } else {
        reverted.delete(trackId)
      }
      set({ likedIds: reverted })
    }
  },

  async fetchLikedIds() {
    try {
      const data = await apiFetch<{ track_ids: number[] }>("/navidrome/starred/ids")
      set({ likedIds: new Set(data.track_ids) })
    } catch {
      // Navidrome may not be connected — silently ignore
    }
  },
}))
