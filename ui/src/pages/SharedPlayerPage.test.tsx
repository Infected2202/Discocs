import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import SharedPlayerPage from "./SharedPlayerPage"

const fetchPublicShare = vi.fn()
const useArtworkTheme = vi.fn()

vi.mock("@/api/shares", () => ({
  fetchPublicShare: (...args: unknown[]) => fetchPublicShare(...args),
}))

vi.mock("@/hooks/useArtworkTheme", () => ({
  useArtworkTheme: (artworkUrl: string | null) => useArtworkTheme(artworkUrl),
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/share/test-token"]}>
      <Routes><Route path="/share/:token" element={<SharedPlayerPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe("SharedPlayerPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    fetchPublicShare.mockReset()
    useArtworkTheme.mockReset()
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined)
    fetchPublicShare.mockResolvedValue({
      kind: "release",
      title: "Shared album",
      subtitle: "Artist",
      expires_at: null,
      artwork_url: "/cover",
      items: [
        { position: 0, title: "First", artist: "Artist", duration: 60, available: true, audio_url: "/audio/0" },
        { position: 1, title: "Second", artist: "Artist", duration: 90, available: true, audio_url: "/audio/1" },
      ],
    })
  })

  it("loads the capability directly without auth UI or personal actions", async () => {
    renderPage()

    expect(await screen.findByRole("heading", { name: "First" })).toBeInTheDocument()
    expect(fetchPublicShare).toHaveBeenCalledWith("test-token")
    await waitFor(() => expect(useArtworkTheme).toHaveBeenLastCalledWith("/cover"))
    expect(screen.getByRole("link", { name: "Discocs" })).toHaveAttribute("href", "/")
    expect(screen.queryByText("Shared listening")).not.toBeInTheDocument()
    expect(screen.queryByText("Shared album")).not.toBeInTheDocument()
    expect(screen.queryByText("Like")).not.toBeInTheDocument()
    expect(screen.queryByText("Download")).not.toBeInTheDocument()
    expect(screen.queryByText("Add to playlist")).not.toBeInTheDocument()
  })

  it("uses the shared release as an ordered local queue", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    expect(screen.getByRole("img", { name: "Second" })).toHaveAttribute("src", "/cover")

    fireEvent.click(screen.getByRole("button", { name: /Second/ }))

    await waitFor(() => expect(screen.getByRole("heading", { name: "Second" })).toBeInTheDocument())
  })

  it("renders borderless artwork-colored seek and volume controls", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    const seek = screen.getByRole("slider", { name: "Playback position" })
    const volume = screen.getByRole("slider", { name: "Volume" })
    expect(seek).toHaveClass("touch-none")
    expect(volume).toHaveClass("share-range")
    expect(volume).not.toHaveClass("accent-primary")
    expect(volume).toHaveStyle({ "--share-range-progress": "100%" })
  })

  it("previews a dragged position and seeks only when the pointer is released", async () => {
    const { container } = renderPage()
    await screen.findByRole("heading", { name: "First" })

    const seek = screen.getByRole("slider", { name: "Playback position" })
    const audio = container.querySelector("audio") as HTMLAudioElement
    vi.spyOn(seek, "getBoundingClientRect").mockReturnValue({
      left: 100, width: 200, right: 300, top: 0, bottom: 16,
      height: 16, x: 100, y: 0, toJSON: () => ({}),
    })
    seek.setPointerCapture = vi.fn()

    fireEvent.pointerDown(seek, { pointerId: 4, clientX: 140 })
    fireEvent.pointerMove(window, { pointerId: 4, clientX: 200 })

    expect(audio.currentTime).toBe(0)
    expect(seek).toHaveAttribute("aria-valuenow", "30")

    fireEvent.pointerUp(window, { pointerId: 4, clientX: 260 })

    expect(audio.currentTime).toBe(48)
    expect(seek).toHaveAttribute("aria-valuenow", "48")
  })

  it("shows one generic unavailable state for a rejected token", async () => {
    fetchPublicShare.mockRejectedValueOnce(new Error("404"))
    renderPage()

    expect(await screen.findByRole("heading", { name: "This link is unavailable" })).toBeInTheDocument()
  })
})
