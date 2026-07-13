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
