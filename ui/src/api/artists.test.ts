import { beforeEach, describe, expect, it, vi } from "vitest"

const apiFetch = vi.fn()

vi.mock("./client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (path: string, params?: Record<string, string | number | boolean | undefined>) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params ?? {})) {
      if (value !== undefined) search.set(key, String(value))
    }
    const query = search.toString()
    return query ? `${path}?${query}` : path
  },
}))

import { fetchArtistSimilar } from "./artists"

describe("fetchArtistSimilar", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockResolvedValue({ available: true, items: [] })
  })

  it("requests 16 similar artists by default", async () => {
    await fetchArtistSimilar(42)
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/artists/42/similar?limit=16")
  })

  it("forwards an explicit limit override", async () => {
    await fetchArtistSimilar(42, 24)
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/artists/42/similar?limit=24")
  })
})
