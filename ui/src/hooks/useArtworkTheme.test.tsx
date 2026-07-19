import { renderHook, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { useArtworkTheme } from "./useArtworkTheme"

const mocks = vi.hoisted(() => ({
  extractArtworkPalette: vi.fn(),
}))

vi.mock("@/lib/artworkPalette", () => ({
  DEFAULT_ARTWORK_PALETTE: { accent: "#ff2a6d", foreground: "#07110e" },
  extractArtworkPalette: (...args: unknown[]) => mocks.extractArtworkPalette(...args),
}))

vi.mock("@/lib/playerLogger", () => ({ playerLog: vi.fn() }))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: { currentTrack: { artwork: { url: string } } }) => unknown) =>
    selector({ currentTrack: { artwork: { url: "/private-player-cover" } } }),
}))

describe("useArtworkTheme", () => {
  beforeEach(() => {
    mocks.extractArtworkPalette.mockReset()
    document.documentElement.removeAttribute("style")
    delete document.documentElement.dataset.trackAccentArtwork
    delete document.documentElement.dataset.trackAccentColor
  })

  it("uses an explicit public artwork URL instead of the private player track", async () => {
    mocks.extractArtworkPalette.mockResolvedValue({
      accent: "rgb(12 34 56)",
      foreground: "#ffffff",
    })

    renderHook(() => useArtworkTheme("/api/v1/public/shares/token/cover"))

    await waitFor(() => expect(mocks.extractArtworkPalette).toHaveBeenCalledWith(
      "/api/v1/public/shares/token/cover",
      expect.any(AbortSignal),
    ))
    await waitFor(() => expect(document.documentElement.dataset.trackAccentArtwork).toBe(
      "/api/v1/public/shares/token/cover",
    ))
    expect(document.documentElement.style.getPropertyValue("--track-accent")).toBe("rgb(12 34 56)")
  })
})
