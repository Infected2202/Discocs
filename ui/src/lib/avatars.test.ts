import { describe, expect, it } from "vitest"
import { AVATAR_KEYS, avatarUrl } from "./avatars"

describe("avatars", () => {
  it("lists every built-in avatar in a fixed mixed order, not by key", () => {
    // Mirrors app/avatars.py AVATAR_KEYS; tests/test_avatars.py checks the files.
    const byKey = Array.from({ length: 19 }, (_, i) => `a${String(i + 1).padStart(2, "0")}`)
    expect([...AVATAR_KEYS].sort()).toEqual(byKey)
    // Pinned: the grid must not reshuffle between releases unless avatars change.
    expect(AVATAR_KEYS).toEqual([
      "a01", "a08", "a03", "a16", "a12", "a11", "a13", "a07", "a19", "a06",
      "a10", "a18", "a15", "a05", "a09", "a17", "a14", "a02", "a04",
    ])
  })

  it("resolves a known key to its bundled image", () => {
    expect(avatarUrl("a03")).toMatch(/a03.*\.webp/)
  })

  it("returns null for unknown or missing keys", () => {
    expect(avatarUrl("a99")).toBeNull()
    expect(avatarUrl("../a01")).toBeNull()
    expect(avatarUrl(null)).toBeNull()
    expect(avatarUrl(undefined)).toBeNull()
    expect(avatarUrl("")).toBeNull()
  })
})
