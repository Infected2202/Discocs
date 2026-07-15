import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import PlaylistPage from "./PlaylistPage"
import { useUIStore } from "@/store/uiStore"
import type { PlaylistDetail, TrackSummary } from "@/api/types"

const fetchPlaylist = vi.fn()
const fetchLikesPlaylist = vi.fn()
const deletePlaylist = vi.fn()
const removePlaylistTracks = vi.fn()
const reorderPlaylistTracks = vi.fn()

vi.mock("@/api/playlists", () => ({
  fetchPlaylist: (...args: unknown[]) => fetchPlaylist(...args),
  fetchLikesPlaylist: (...args: unknown[]) => fetchLikesPlaylist(...args),
  deletePlaylist: (...args: unknown[]) => deletePlaylist(...args),
  removePlaylistTracks: (...args: unknown[]) => removePlaylistTracks(...args),
  reorderPlaylistTracks: (...args: unknown[]) => reorderPlaylistTracks(...args),
  playPlaylist: vi.fn(),
  playLikes: vi.fn(),
}))

// The page is tested through a stub list that surfaces the selection wiring.
vi.mock("@/components/media/VirtualTrackList", () => ({
  default: ({ tracks, selectable, onToggleSelect, onReorder }: {
    tracks: TrackSummary[]
    selectable?: boolean
    onToggleSelect?: (id: number) => void
    onReorder?: (trackIds: number[]) => void
  }) => (
    <div data-testid="track-list">
      {tracks.map((t) => (
        <button
          key={t.id}
          data-testid={`select-${t.id}`}
          disabled={!selectable}
          onClick={() => onToggleSelect?.(t.id)}
        >
          {t.title}
        </button>
      ))}
      {onReorder && (
        <button
          data-testid="reorder"
          onClick={() => onReorder([...tracks.map((t) => t.id)].reverse())}
        >
          reorder
        </button>
      )}
    </div>
  ),
}))

function makeTrack(id: number): TrackSummary {
  return {
    id,
    title: `Track ${id}`,
    artists: [],
    release: null,
    duration: 100,
    artwork: { url: null, source: "placeholder", placeholder: true },
  } as unknown as TrackSummary
}

function makeDetail(): PlaylistDetail {
  return {
    id: 5,
    title: "Road trip",
    kind: "manual",
    description: "Long drives",
    track_count: 2,
    artwork: { url: null, source: "placeholder", placeholder: true },
    source: null,
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    action: { type: "open", target: "/playlists/5" },
    play_action: { type: "post", endpoint: "/api/v1/playlists/5/play" },
    tracks: [makeTrack(1), makeTrack(2)],
  }
}

function renderPage(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/playlists/:id" element={<PlaylistPage />} />
          <Route path="/" element={<div data-testid="dashboard" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  fetchPlaylist.mockReset()
  fetchLikesPlaylist.mockReset()
  deletePlaylist.mockReset()
  removePlaylistTracks.mockReset()
  reorderPlaylistTracks.mockReset()
  useUIStore.setState({ addToPlaylistTrackIds: null, createPlaylistOptions: null })
})

describe("PlaylistPage — пользовательский плейлист", () => {
  it("рендерит детали, Edit открывает модалку в режиме редактирования", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())

    renderPage("/playlists/5")
    await screen.findByText("Road trip")
    expect(screen.getByText("Long drives")).toBeInTheDocument()
    expect(fetchPlaylist).toHaveBeenCalledWith(5)
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/v1/playlists/5/download",
    )

    fireEvent.click(screen.getByRole("button", { name: /edit/i }))
    expect(useUIStore.getState().createPlaylistOptions?.playlist?.id).toBe(5)
  })

  it("selection-бар: удаляет выбранные треки батчем и сбрасывает выделение", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    removePlaylistTracks.mockResolvedValue({ removed: 2, track_count: 0 })

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("select-1"))
    fireEvent.click(screen.getByTestId("select-2"))
    expect(screen.getByText("2 selected")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Remove selected tracks" }))
    await waitFor(() => expect(removePlaylistTracks).toHaveBeenCalledWith(5, [1, 2]))
    await waitFor(() => expect(screen.queryByText("2 selected")).toBeNull())
  })

  it("Cancel сбрасывает выделение без удаления", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("select-1"))
    expect(screen.getByText("1 selected")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }))
    expect(screen.queryByText("1 selected")).toBeNull()
    expect(removePlaylistTracks).not.toHaveBeenCalled()
  })

  it("Delete с подтверждением удаляет и уводит на дашборд", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    deletePlaylist.mockResolvedValue(undefined)
    vi.spyOn(globalThis, "confirm").mockReturnValue(true)

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByRole("button", { name: /delete/i }))
    await waitFor(() => expect(deletePlaylist).toHaveBeenCalledWith(5))
    await screen.findByTestId("dashboard")
  })

  it("reorder уходит в API с новым порядком", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    reorderPlaylistTracks.mockResolvedValue({ track_ids: [2, 1] })

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("reorder"))
    await waitFor(() => expect(reorderPlaylistTracks).toHaveBeenCalledWith(5, [2, 1]))
  })

  it("чужой public playlist доступен только для чтения", async () => {
    fetchPlaylist.mockResolvedValue({ ...makeDetail(), editable: false, visibility: "public" })

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull()
    expect(screen.getByTestId("select-1")).toBeDisabled()
    expect(screen.queryByTestId("reorder")).toBeNull()
  })

  it("likes: без Edit/Delete и без selection", async () => {
    fetchLikesPlaylist.mockResolvedValue({
      id: "likes",
      title: "Liked Tracks",
      track_count: 1,
      tracks: [makeTrack(1)],
    })

    renderPage("/playlists/likes")
    await screen.findByText("Liked Tracks")

    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull()
    expect(screen.queryByRole("button", { name: /delete/i })).toBeNull()
    expect(screen.getByTestId("select-1")).toBeDisabled()
    expect(fetchPlaylist).not.toHaveBeenCalled()
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/v1/playlists/likes/download",
    )
  })
})
