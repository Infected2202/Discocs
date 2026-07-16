import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import MixPage from "./MixPage"
import CreatePlaylistDialog from "@/components/playlists/CreatePlaylistDialog"
import { useUIStore } from "@/store/uiStore"
import type { GeneratedMixDetail } from "@/api/types"

const saveMix = vi.fn()
const useMix = vi.fn()

vi.mock("@/api/mixes", () => ({
  saveMix: (...args: unknown[]) => saveMix(...args),
  playMix: vi.fn(),
}))
vi.mock("@/api/hooks/useMix", () => ({
  useMix: (...args: unknown[]) => useMix(...args),
}))
vi.mock("@/components/media/VirtualTrackList", () => ({
  default: () => <div data-testid="track-list" />,
}))

function makeMix(status = "draft"): GeneratedMixDetail {
  return {
    id: "mix-1",
    title: "Evening mix",
    status,
    track_count: 2,
    artwork: null,
    created_at: "2026-07-08T00:00:00Z",
    items: [{
      track_id: 7,
      track: {
        id: 7,
        title: "Track",
        artists: [],
        release: null,
        duration: 120,
        artwork: { url: null, source: "placeholder", placeholder: true },
        explicit: false,
        liked: false,
        actions: [],
      },
      position: 0,
      score: null,
      reason: null,
    }],
  }
}

function renderMixPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/mixes/mix-1"]}>
        <Routes>
          <Route path="/mixes/:id" element={<MixPage />} />
        </Routes>
        <CreatePlaylistDialog />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  saveMix.mockReset()
  useMix.mockReset()
  useUIStore.setState({ addToPlaylistTrackIds: null, createPlaylistOptions: null })
})

describe("MixPage — save-флоу через модалку плейлиста", () => {
  it("Save открывает модалку с названием микса; подтверждение зовёт saveMix с формой", async () => {
    useMix.mockReturnValue({ data: makeMix(), isLoading: false, error: null })
    saveMix.mockResolvedValue(makeMix("saved"))

    renderMixPage()
    fireEvent.click(screen.getByRole("button", { name: /save/i }))

    const nameInput = await screen.findByPlaceholderText("Playlist name")
    expect(nameInput).toHaveValue("Evening mix")

    fireEvent.change(nameInput, { target: { value: "My evening" } })
    fireEvent.change(screen.getByPlaceholderText("Optional description"), {
      target: { value: "chill" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    await waitFor(() =>
      expect(saveMix).toHaveBeenCalledWith("mix-1", { title: "My evening", description: "chill" })
    )
    await waitFor(() => expect(useUIStore.getState().createPlaylistOptions).toBeNull())
  })

  it("у сохранённого микса кнопки Save нет", () => {
    useMix.mockReturnValue({ data: makeMix("saved"), isLoading: false, error: null })

    renderMixPage()
    expect(screen.queryByRole("button", { name: /save/i })).toBeNull()
    const download = screen.getByRole("link", { name: "Download" })
    expect(download).toHaveAttribute(
      "href",
      "/api/v1/mixes/mix-1/download",
    )
    expect(download).not.toHaveTextContent("Download")
  })
})
