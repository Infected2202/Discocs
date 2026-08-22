import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

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

  afterEach(async () => {
    // A failed toggle schedules a real background retry timer. resetModules()
    // in the *next* beforeEach hasn't run yet, so a dynamic import here still
    // resolves to the same backgroundRetry module instance this test's
    // dynamic import of navidromeStore used — a static top-level import
    // would instead target the module graph from before the first
    // resetModules() call and clear nothing.
    const { cancelAllBackgroundRetries } = await import("@/lib/backgroundRetry")
    cancelAllBackgroundRetries()
  })

  type InvalidateSpy = { mock: { calls: unknown[][] } }

  async function setup(response: unknown) {
    vi.doMock("@/api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("@/api/client")>()
      return {
        ...actual,
        apiFetch: vi.fn().mockResolvedValue(response),
      }
    })
    const { queryClient } = await import("@/api/queryClient")
    const { useNavidromeStore } = await import("./navidromeStore")
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")
    return { useNavidromeStore, invalidate }
  }

  function invalidatedKeys(invalidate: InvalidateSpy): string[] {
    return invalidate.mock.calls.map((call) => {
      const filters = call[0] as { queryKey?: unknown[] } | undefined
      return JSON.stringify(filters?.queryKey)
    })
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
    vi.doMock("@/api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("@/api/client")>()
      return {
        ...actual,
        apiFetch: vi.fn().mockRejectedValue(new Error("navidrome down")),
      }
    })
    const { queryClient } = await import("@/api/queryClient")
    const { useNavidromeStore } = await import("./navidromeStore")
    const invalidate = vi.spyOn(queryClient, "invalidateQueries")

    await useNavidromeStore.getState().toggleArtistLike(3)

    expect(invalidatedKeys(invalidate)).not.toContain(
      JSON.stringify(["shelf", "liked_artists"]),
    )
  })
})
