import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import SharedPlayerPage from "./SharedPlayerPage"

const fetchPublicShare = vi.fn()

vi.mock("@/api/shares", () => ({
  fetchPublicShare: (...args: unknown[]) => fetchPublicShare(...args),
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
    expect(screen.getByText("Shared album")).toBeInTheDocument()
    expect(screen.queryByText("Like")).not.toBeInTheDocument()
    expect(screen.queryByText("Download")).not.toBeInTheDocument()
    expect(screen.queryByText("Add to playlist")).not.toBeInTheDocument()
  })

  it("uses the shared release as an ordered local queue", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    fireEvent.click(screen.getByRole("button", { name: /Second/ }))

    await waitFor(() => expect(screen.getByRole("heading", { name: "Second" })).toBeInTheDocument())
  })

  it("shows one generic unavailable state for a rejected token", async () => {
    fetchPublicShare.mockRejectedValueOnce(new Error("404"))
    renderPage()

    expect(await screen.findByRole("heading", { name: "This link is unavailable" })).toBeInTheDocument()
  })
})
