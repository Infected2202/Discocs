import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError, apiFetch, apiUrl } from "./client"

describe("apiFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("resolves a root-relative path to an absolute URL before calling fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) })
    vi.stubGlobal("fetch", fetchMock)
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await apiFetch("/api/v1/auth/session")

    expect(fetchMock).toHaveBeenCalledWith("https://d.plikinson.org/api/v1/auth/session", expect.anything())
  })

  it("leaves an already-absolute URL unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ ok: true }) })
    vi.stubGlobal("fetch", fetchMock)
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await apiFetch("https://d.plikinson.org/api/v1/mixes")

    expect(fetchMock).toHaveBeenCalledWith("https://d.plikinson.org/api/v1/mixes", expect.anything())
  })

  it("returns the parsed body on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({ hello: "world" }) }))
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await expect(apiFetch("/api/v1/ping")).resolves.toEqual({ hello: "world" })
  })

  it("throws an ApiError with the server's error body on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ error: { code: "not_found", message: "Track not found" } }),
      })
    )
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await expect(apiFetch("/api/v1/tracks/999")).rejects.toMatchObject(
      new ApiError(404, "not_found", "Track not found")
    )
  })
})

describe("apiUrl", () => {
  it("builds a root-relative path with query params, without the origin", () => {
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))
    expect(apiUrl("/api/v1/search", { q: "aphex", limit: 10 })).toBe("/api/v1/search?q=aphex&limit=10")
    vi.unstubAllGlobals()
  })
})
