import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError, apiFetch, apiUrl } from "./client"

// Real Response objects rather than hand-rolled mocks, so this stays honest
// about actual fetch Response semantics (e.g. a body can only be read once).
function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  })
}

describe("apiFetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("resolves a root-relative path to an absolute URL before calling fetch", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal("fetch", fetchMock)
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await apiFetch("/api/v1/auth/session")

    expect(fetchMock).toHaveBeenCalledWith("https://d.plikinson.org/api/v1/auth/session", expect.anything())
  })

  it("leaves an already-absolute URL unchanged", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal("fetch", fetchMock)
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await apiFetch("https://d.plikinson.org/api/v1/mixes")

    expect(fetchMock).toHaveBeenCalledWith("https://d.plikinson.org/api/v1/mixes", expect.anything())
  })

  it("returns the parsed body on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ hello: "world" })))
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await expect(apiFetch("/api/v1/ping")).resolves.toEqual({ hello: "world" })
  })

  it("throws a diagnostic error (url, status, content-type) when a 200 body isn't JSON", async () => {
    const htmlResponse = new Response("<!doctype html><html>...</html>", {
      status: 200,
      headers: { "content-type": "text/html" },
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(htmlResponse))
    vi.stubGlobal("location", new URL("https://d.plikinson.org/"))

    await expect(apiFetch("/api/v1/auth/session")).rejects.toThrow(
      'Non-JSON response from https://d.plikinson.org/api/v1/auth/session (status 200, content-type "text/html")'
    )
  })

  it("throws an ApiError with the server's error body on a non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ error: { code: "not_found", message: "Track not found" } }, { status: 404 })
      )
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
