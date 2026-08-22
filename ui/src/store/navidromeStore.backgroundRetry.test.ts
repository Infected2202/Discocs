import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

/**
 * A VPN drop (or any transient network failure) must not flicker the like
 * icon back to its pre-click state — the optimistic update already reflects
 * what the user asked for, and the write is very likely to succeed once
 * connectivity returns. These cover the "keep optimistic state, retry
 * quietly in the background" contract described in playerStore's sibling
 * background-retry work.
 */
vi.mock("./playerStore", () => ({
  usePlayerStore: {
    getState: () => ({ recordEvent: vi.fn().mockResolvedValue(undefined) }),
  },
}))

describe("navidromeStore background retry", () => {
  beforeEach(() => {
    vi.resetModules()
    vi.restoreAllMocks()
  })

  afterEach(async () => {
    // resetModules() in the next beforeEach hasn't run yet, so this still
    // targets the same backgroundRetry module instance the test's dynamic
    // import of navidromeStore used — clear any pending retry it scheduled
    // before switching timer implementations.
    const { cancelAllBackgroundRetries } = await import("@/lib/backgroundRetry")
    cancelAllBackgroundRetries()
    vi.useRealTimers()
  })

  // `makeImpl` receives the *actual* (unmocked) "@/api/client" module so a
  // test can throw a real `ApiError` instance built from the exact same
  // class reference navidromeStore's `isNetworkError` check will compare
  // against in this same resetModules() cycle — importing ApiError any
  // other way risks a stale class reference from a prior cycle, which would
  // make `instanceof` silently false.
  async function setup(makeImpl: (actual: typeof import("@/api/client")) => () => Promise<unknown>) {
    vi.doMock("@/api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("@/api/client")>()
      return {
        ...actual,
        apiFetch: vi.fn(makeImpl(actual)),
      }
    })
    vi.doMock("@/api/queryClient", () => ({
      queryClient: { invalidateQueries: vi.fn() },
    }))
    const { apiFetch } = await import("@/api/client")
    const { useNavidromeStore } = await import("./navidromeStore")
    return { useNavidromeStore, apiFetch }
  }

  it("keeps the optimistic like state and retries in the background on a network failure", async () => {
    vi.useFakeTimers()
    let calls = 0
    const { useNavidromeStore, apiFetch } = await setup(() => async () => {
      calls += 1
      if (calls === 1) throw new TypeError("Failed to fetch")
      return {}
    })

    await useNavidromeStore.getState().toggleLike(7)

    // Optimistic update stays — no revert-then-reapply flicker.
    expect(useNavidromeStore.getState().isLiked(7)).toBe(true)
    expect(apiFetch).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(15_000)

    expect(apiFetch).toHaveBeenCalledTimes(2)
    expect(useNavidromeStore.getState().isLiked(7)).toBe(true)
  })

  it("reverts the optimistic like state on a real 4xx error instead of retrying", async () => {
    const { useNavidromeStore, apiFetch } = await setup((actual) => async () => {
      throw new actual.ApiError(404, "not_found", "track not found")
    })

    await useNavidromeStore.getState().toggleLike(7)

    expect(useNavidromeStore.getState().isLiked(7)).toBe(false)

    // A genuine 4xx must not be retried — advancing time changes nothing.
    vi.useFakeTimers()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(apiFetch).toHaveBeenCalledTimes(1)
  })

  it("keeps the optimistic album-like state and retries a queue-sync-style network failure", async () => {
    vi.useFakeTimers()
    let calls = 0
    const { useNavidromeStore, apiFetch } = await setup(() => async () => {
      calls += 1
      if (calls === 1) throw new TypeError("Failed to fetch")
      return {}
    })

    await useNavidromeStore.getState().toggleAlbumLike(3)
    expect(useNavidromeStore.getState().isAlbumLiked(3)).toBe(true)

    await vi.advanceTimersByTimeAsync(15_000)

    expect(apiFetch).toHaveBeenCalledTimes(2)
    expect(useNavidromeStore.getState().isAlbumLiked(3)).toBe(true)
  })

  it("retries fetchLikedIds in the background instead of leaving the mirror stale forever", async () => {
    vi.useFakeTimers()
    let calls = 0
    const { useNavidromeStore, apiFetch } = await setup(() => async () => {
      calls += 1
      if (calls === 1) throw new TypeError("Failed to fetch")
      return { track_ids: [42], album_ids: [], artist_ids: [] }
    })

    await useNavidromeStore.getState().fetchLikedIds()
    expect(useNavidromeStore.getState().isLiked(42)).toBe(false)

    await vi.advanceTimersByTimeAsync(15_000)

    expect(apiFetch).toHaveBeenCalledTimes(2)
    expect(useNavidromeStore.getState().isLiked(42)).toBe(true)
  })

  it("does not retry fetchLikedIds forever when Navidrome itself rejects the request", async () => {
    const { useNavidromeStore, apiFetch } = await setup((actual) => async () => {
      throw new actual.ApiError(502, "navidrome_error", "Navidrome starred failed")
    })

    await useNavidromeStore.getState().fetchLikedIds()

    // A real HTTP error (Navidrome not connected/configured) must not be
    // retried indefinitely — advancing time changes nothing.
    vi.useFakeTimers()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(apiFetch).toHaveBeenCalledTimes(1)
  })
})
