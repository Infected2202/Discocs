import { beforeEach, describe, expect, it, vi } from "vitest"

/**
 * The favourite shelves are served from the backend's like mirror, which is
 * written by the very requests this store makes. Without invalidation the
 * dashboard keeps a cached copy taken before the mirror was correct — and an
 * empty shelf renders as nothing at all, so it looks like the shelf vanished.
 * See plans/likes-unification-plan.md.
 */
describe("navidromeStore like invalidation", () => {
  beforeEach(() => {
    vi.resetModules()
    vi.restoreAllMocks()
  })

  async function setup(response: unknown) {
    vi.doMock("@/api/client", () => ({
      apiFetch: vi.fn().mockResolvedValue(response),
    }))
    const { queryClient } = await import("@/api/queryClient")
    const { useNavidromeStore } = await import("./navidromeStore")
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    return { useNavidromeStore, invalidate }
  }

  function invalidatedKeys(invalidate: ReturnType<typeof vi.spyOn>): string[] {
    return invalidate.mock.calls.map(([arg]) =>
      JSON.stringify((arg as { queryKey: unknown[] }).queryKey),
    )
  }

  it("refreshes the shelves after fetching liked ids", async () => {
    const { useNavidromeStore, invalidate } = await setup({
      track_ids: [1],
      album_ids: [2],
      artist_ids: [3],
    })

    await useNavidromeStore.getState().fetchLikedIds()

    const keys = invalidatedKeys(invalidate)
    expect(keys).toContain(JSON.stringify(["dashboard"]))
    expect(keys).toContain(JSON.stringify(["shelf", "liked_artists"]))
    expect(keys).toContain(JSON.stringify(["shelf", "liked_releases"]))
  })

  it("refreshes the shelves after liking an artist", async () => {
    const { useNavidromeStore, invalidate } = await setup({})

    await useNavidromeStore.getState().toggleArtistLike(3)

    expect(invalidatedKeys(invalidate)).toContain(
      JSON.stringify(["shelf", "liked_artists"]),
    )
  })

  it("refreshes the shelves after liking an album", async () => {
    const { useNavidromeStore, invalidate } = await setup({})

    await useNavidromeStore.getState().toggleAlbumLike(2)

    expect(invalidatedKeys(invalidate)).toContain(
      JSON.stringify(["shelf", "liked_releases"]),
    )
  })

  it("does not refresh the shelves when the request fails", async () => {
    vi.doMock("@/api/client", () => ({
      apiFetch: vi.fn().mockRejectedValue(new Error("navidrome down")),
    }))
    const { queryClient } = await import("@/api/queryClient")
    const { useNavidromeStore } = await import("./navidromeStore")
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")

    await useNavidromeStore.getState().toggleArtistLike(3)

    expect(invalidatedKeys(invalidate)).not.toContain(
      JSON.stringify(["shelf", "liked_artists"]),
    )
  })
})
