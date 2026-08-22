import { create } from "zustand"
import { apiFetch } from "@/api/client"
import { queryClient } from "@/api/queryClient"
import { DEFAULT_RETRY_INTERVAL_MS, cancelAllBackgroundRetries, scheduleBackgroundRetry } from "@/lib/backgroundRetry"
import { isNetworkError } from "@/lib/apiErrorKind"
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
    const desired = !liked

    const next = new Set(get().likedIds)
    if (liked) next.delete(trackId); else next.add(trackId)
    set({ likedIds: next })

    const setStar = () => apiFetch(`/api/v1/tracks/${trackId}/navidrome-star`, {
      method: "PUT",
      body: JSON.stringify({ starred: desired }),
    })

    try {
      await setStar()
      // Telemetry reflects "this happened right now" — only fire it for an
      // immediate success, not a later background retry (see below), which
      // could land against a since-changed currently-playing track.
      const eventType = desired ? "liked" : "disliked"
      await usePlayerStore.getState().recordEvent(eventType)
      queryClient.invalidateQueries({ queryKey: ["playlist", "likes"] })
      invalidateLikeDerivedQueries()
    } catch (err) {
      if (!isNetworkError(err)) {
        const reverted = new Set(get().likedIds)
        if (liked) reverted.add(trackId); else reverted.delete(trackId)
        set({ likedIds: reverted })
        return
      }
      // A network-style failure (e.g. VPN drop) — the optimistic like state
      // already reflects the user's intent and is very likely correct, so
      // keep it and quietly retry the write instead of flickering the icon
      // back and forth.
      // `setStar()` was already attempted (and failed) above — don't fire it
      // again immediately, just start the interval.
      scheduleBackgroundRetry(`navidrome:toggle-like:${trackId}`, async () => {
        await setStar()
        queryClient.invalidateQueries({ queryKey: ["playlist", "likes"] })
        invalidateLikeDerivedQueries()
      }, DEFAULT_RETRY_INTERVAL_MS, true)
    }
  },

  async toggleAlbumLike(releaseId) {
    const liked = get().likedAlbumIds.has(releaseId)
    const desired = !liked

    const next = new Set(get().likedAlbumIds)
    if (liked) next.delete(releaseId); else next.add(releaseId)
    set({ likedAlbumIds: next })

    const setStar = () => apiFetch(`/api/v1/releases/${releaseId}/navidrome-star`, {
      method: "PUT",
      body: JSON.stringify({ starred: desired }),
    })

    try {
      await setStar()
      invalidateLikeDerivedQueries()
    } catch (err) {
      if (!isNetworkError(err)) {
        const reverted = new Set(get().likedAlbumIds)
        if (liked) reverted.add(releaseId); else reverted.delete(releaseId)
        set({ likedAlbumIds: reverted })
        return
      }
      scheduleBackgroundRetry(`navidrome:toggle-album-like:${releaseId}`, async () => {
        await setStar()
        invalidateLikeDerivedQueries()
      }, DEFAULT_RETRY_INTERVAL_MS, true)
    }
  },

  async toggleArtistLike(artistId) {
    const liked = get().likedArtistIds.has(artistId)
    const desired = !liked

    const next = new Set(get().likedArtistIds)
    if (liked) next.delete(artistId); else next.add(artistId)
    set({ likedArtistIds: next })

    const setStar = () => apiFetch(`/api/v1/artists/${artistId}/navidrome-star`, {
      method: "PUT",
      body: JSON.stringify({ starred: desired }),
    })

    try {
      await setStar()
      invalidateLikeDerivedQueries()
    } catch (err) {
      if (!isNetworkError(err)) {
        const reverted = new Set(get().likedArtistIds)
        if (liked) reverted.add(artistId); else reverted.delete(artistId)
        set({ likedArtistIds: reverted })
        return
      }
      scheduleBackgroundRetry(`navidrome:toggle-artist-like:${artistId}`, async () => {
        await setStar()
        invalidateLikeDerivedQueries()
      }, DEFAULT_RETRY_INTERVAL_MS, true)
    }
  },

  resetForLogout() {
    cancelAllBackgroundRetries()
    set({
      likedIds: new Set(),
      likedAlbumIds: new Set(),
      likedArtistIds: new Set(),
    })
  },

  async fetchLikedIds() {
    const sync = async () => {
      const data = await apiFetch<{ track_ids: number[]; album_ids?: number[]; artist_ids?: number[] }>("/api/v1/navidrome/starred/ids")
      set({
        likedIds: new Set(data.track_ids),
        likedAlbumIds: new Set(data.album_ids ?? []),
        likedArtistIds: new Set(data.artist_ids ?? []),
      })
      // This request is also what writes the local like mirror, so anything
      // read from that mirror is only now correct.
      invalidateLikeDerivedQueries()
    }
    try {
      await sync()
    } catch (err) {
      if (!isNetworkError(err)) return // Navidrome not connected — nothing to retry into.
      // A transient failure (e.g. VPN drop) — "latest fetch wins" is correct
      // whenever it finally lands, so keep quietly retrying instead of
      // leaving the like mirror stuck stale. sync() was already attempted
      // above, so skip the redundant immediate re-attempt.
      scheduleBackgroundRetry("navidrome:fetch-liked-ids", sync, DEFAULT_RETRY_INTERVAL_MS, true)
    }
  },
}))
