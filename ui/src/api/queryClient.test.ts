import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "./client"
import { queryClient } from "./queryClient"

describe("queryClient unauthorized handling", () => {
  afterEach(() => {
    queryClient.clear()
    localStorage.clear()
    vi.unstubAllGlobals()
  })

  it("clears persisted playback before redirecting on 401", async () => {
    const assign = vi.fn()
    vi.stubGlobal("location", { pathname: "/", assign })
    localStorage.setItem("discocs.sessionId.v1", "session-a")
    localStorage.setItem("discocs.playbackPosition.v1", "{}")

    await expect(queryClient.fetchQuery({
      queryKey: ["unauthorized-cleanup"],
      queryFn: () => Promise.reject(new ApiError(401, "unauthorized", "expired")),
      retry: false,
    })).rejects.toBeInstanceOf(ApiError)

    expect(localStorage.getItem("discocs.sessionId.v1")).toBeNull()
    expect(localStorage.getItem("discocs.playbackPosition.v1")).toBeNull()
    expect(assign).toHaveBeenCalledWith("/login")
  })
})

describe("queryClient refetchInterval — background retry on network failure only", () => {
  function refetchIntervalFor(state: unknown) {
    const fn = queryClient.getDefaultOptions().queries?.refetchInterval
    if (typeof fn !== "function") throw new Error("expected refetchInterval to be a function")
    return fn({ state } as Parameters<typeof fn>[0])
  }

  it("polls every 15s when the query failed with a network error", () => {
    expect(
      refetchIntervalFor({ status: "error", error: new TypeError("Failed to fetch") })
    ).toBe(15_000)
  })

  it("does not poll a genuine HTTP error (e.g. a real 404)", () => {
    expect(
      refetchIntervalFor({ status: "error", error: new ApiError(404, "not_found", "nope") })
    ).toBe(false)
  })

  it("does not poll a healthy query", () => {
    expect(refetchIntervalFor({ status: "success", error: null })).toBe(false)
  })
})
