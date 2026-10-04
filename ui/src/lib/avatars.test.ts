import { describe, expect, it } from "vitest"
import { AVATAR_KEYS, avatarUrl } from "./avatars"

describe("avatars", () => {
  it("lists the built-in avatars in key order", () => {
    // Mirrors app/avatars.py AVATAR_KEYS; tests/test_avatars.py checks the files.
    expect(AVATAR_KEYS).toEqual(["a01", "a02", "a03", "a04", "a05", "a06"])
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
