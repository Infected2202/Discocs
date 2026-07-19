import { create } from "zustand"
import { apiFetch } from "@/api/client"
import { queryClient } from "@/api/queryClient"
import { usePlayerStore } from "./playerStore"

interface NavidromeStore {
  likedIds: Set<number>
  likedAlbumIds: Set<number>
  likedArtistIds: Set<number>
  isLiked(trackId: number): boolean
  isAlbumLiked(releaseId: number): boolean
  isArtistLiked(artistId: number): boolean
  toggleLike(trackId: number): Promise<void>
  toggleAlbumLike(releaseId: number): Promise<void>
  toggleArtistLike(artistId: number): Promise<void>
  fetchLikedIds(): Promise<void>
  resetForLogout(): void
}

/**
 * Refresh everything that is derived from likes.
 *
 * The favourites shelves are served from the local like mirror, so they go
 * stale the moment a like changes — and with `staleTime` on the shelf queries
 * they stayed stale for up to a minute. Worse, the initial dashboard fetch
 * races `fetchLikedIds()`: on a cold mirror the dashboard resolves first, the
 * shelf comes back empty and hides itself entirely until something refetches.
 * See plans/likes-unification-plan.md.
 */
function invalidateLikeDerivedQueries() {
  queryClient.invalidateQueries({ queryKey: ["dashboard"] })
  queryClient.invalidateQueries({ queryKey: ["shelf", "liked_artists"] })
  queryClient.invalidateQueries({ queryKey: ["shelf", "liked_releases"] })
}

export const useNavidromeStore = create<NavidromeStore>((set, get) => ({
  likedIds: new Set(),
  likedAlbumIds: new Set(),
  likedArtistIds: new Set(),

  isLiked(trackId) {
    return get().likedIds.has(trackId)
  },

  isAlbumLiked(releaseId) {
    return get().likedAlbumIds.has(releaseId)
  },

  isArtistLiked(artistId) {
    return get().likedArtistIds.has(artistId)
  },

  async toggleLike(trackId) {
    const liked = get().likedIds.has(trackId)

    const next = new Set(get().likedIds)
    if (liked) next.delete(trackId); else next.add(trackId)
    set({ likedIds: next })

    try {
      await apiFetch(`/api/v1/tracks/${trackId}/navidrome-star`, {
        method: "PUT",
        body: JSON.stringify({ starred: !liked }),
      })
      const eventType = liked ? "disliked" : "liked"
      await usePlayerStore.getState().recordEvent(eventType)
      queryClient.invalidateQueries({ queryKey: ["playlist", "likes"] })
      invalidateLikeDerivedQueries()
    } catch {
      const reverted = new Set(get().likedIds)
      if (liked) reverted.add(trackId); else reverted.delete(trackId)
      set({ likedIds: reverted })
    }
  },

  async toggleAlbumLike(releaseId) {
    const liked = get().likedAlbumIds.has(releaseId)

    const next = new Set(get().likedAlbumIds)
    if (liked) next.delete(releaseId); else next.add(releaseId)
    set({ likedAlbumIds: next })

    try {
      await apiFetch(`/api/v1/releases/${releaseId}/navidrome-star`, {
        method: "PUT",
        body: JSON.stringify({ starred: !liked }),
      })
      invalidateLikeDerivedQueries()
    } catch {
      const reverted = new Set(get().likedAlbumIds)
      if (liked) reverted.add(releaseId); else reverted.delete(releaseId)
      set({ likedAlbumIds: reverted })
    }
  },

  async toggleArtistLike(artistId) {
    const liked = get().likedArtistIds.has(artistId)

    const next = new Set(get().likedArtistIds)
    if (liked) next.delete(artistId); else next.add(artistId)
    set({ likedArtistIds: next })

    try {
      await apiFetch(`/api/v1/artists/${artistId}/navidrome-star`, {
        method: "PUT",
        body: JSON.stringify({ starred: !liked }),
      })
      invalidateLikeDerivedQueries()
    } catch {
      const reverted = new Set(get().likedArtistIds)
      if (liked) reverted.add(artistId); else reverted.delete(artistId)
      set({ likedArtistIds: reverted })
    }
  },

  resetForLogout() {
    set({
      likedIds: new Set(),
      likedAlbumIds: new Set(),
      likedArtistIds: new Set(),
    })
  },

  async fetchLikedIds() {
    try {
      const data = await apiFetch<{ track_ids: number[]; album_ids?: number[]; artist_ids?: number[] }>("/api/v1/navidrome/starred/ids")
      set({
        likedIds: new Set(data.track_ids),
        likedAlbumIds: new Set(data.album_ids ?? []),
        likedArtistIds: new Set(data.artist_ids ?? []),
      })
      // This request is also what writes the local like mirror, so anything
      // read from that mirror is only now correct.
      invalidateLikeDerivedQueries()
    } catch {
      // Navidrome may not be connected — silently ignore
    }
  },
}))
