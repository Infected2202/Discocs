import { beforeEach, describe, expect, it, vi } from "vitest"

const apiFetch = vi.fn()

vi.mock("./client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  // Minimal apiUrl stand-in that mirrors query-string building.
  apiUrl: (path: string, params?: Record<string, string | number | boolean | undefined>) => {
    if (!params) return path
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) search.set(key, String(value))
    }
    const qs = search.toString()
    return qs ? `${path}?${qs}` : path
  },
}))

import { fetchReleaseRecommendations, fetchReleaseRelated } from "./releases"

describe("fetchReleaseRecommendations", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockResolvedValue({ available: true, items: [] })
  })

  it("requests 16 recommendations by default so the shelf fills two full rows", async () => {
    await fetchReleaseRecommendations(42)

    expect(apiFetch).toHaveBeenCalledWith("/api/v1/releases/42/recommendations?limit=16")
  })

  it("forwards an explicit limit override", async () => {
    await fetchReleaseRecommendations(42, 30)

    expect(apiFetch).toHaveBeenCalledWith("/api/v1/releases/42/recommendations?limit=30")
  })

  it("pages the full list by offset", async () => {
    await fetchReleaseRecommendations(42, 48, 48)

    expect(apiFetch).toHaveBeenCalledWith("/api/v1/releases/42/recommendations?limit=48&offset=48")
  })
})

describe("fetchReleaseRelated", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockResolvedValue({ items: [] })
  })

  it("previews the common shelf size and pages the full list by offset", async () => {
    await fetchReleaseRelated(7)
    expect(apiFetch).toHaveBeenLastCalledWith("/api/v1/releases/7/related-discography?limit=16")

    await fetchReleaseRelated(7, 48, 48)
    expect(apiFetch).toHaveBeenLastCalledWith("/api/v1/releases/7/related-discography?limit=48&offset=48")
  })
})
