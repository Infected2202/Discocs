import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import AddToPlaylistDialog from "./AddToPlaylistDialog"
import { useUIStore } from "@/store/uiStore"
import type { PlaylistSummary } from "@/api/types"

const fetchPlaylists = vi.fn()
const addTracksToPlaylist = vi.fn()
const removePlaylistTracks = vi.fn()

vi.mock("@/api/playlists", () => ({
  fetchPlaylists: (...args: unknown[]) => fetchPlaylists(...args),
  addTracksToPlaylist: (...args: unknown[]) => addTracksToPlaylist(...args),
  removePlaylistTracks: (...args: unknown[]) => removePlaylistTracks(...args),
}))

function makePlaylist(id: number): PlaylistSummary {
  return {
    id,
    title: `Playlist ${id}`,
    kind: "manual",
    description: null,
    track_count: id,
    artwork: { url: null, source: "placeholder", placeholder: true },
    source: null,
    created_at: "2026-07-08T00:00:00Z",
    updated_at: "2026-07-08T00:00:00Z",
    action: { type: "open", target: `/playlists/${id}` },
    play_action: { type: "post", endpoint: `/api/v1/playlists/${id}/play` },
  }
}

function renderDialog() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AddToPlaylistDialog />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  fetchPlaylists.mockReset()
  addTracksToPlaylist.mockReset()
  removePlaylistTracks.mockReset()
  useUIStore.setState({ addToPlaylistTrackIds: null, addToPlaylistMoveFrom: null, createPlaylistOptions: null })
})

describe("AddToPlaylistDialog", () => {
  it("показывает максимум 4 недавних и полный список; клик добавляет треки и закрывает", async () => {
    const items = [1, 2, 3, 4, 5].map(makePlaylist)
    fetchPlaylists.mockResolvedValue({ items, total: 5, limit: 200, offset: 0, next_offset: null })
    addTracksToPlaylist.mockResolvedValue({ added: 1, track_count: 4 })

    renderDialog()
    useUIStore.getState().openAddToPlaylist([7])

    // Longer timeout: under parallel CI workers the React Query fetch +
    // re-render occasionally exceeds findByText's default 1000ms window
    // (the code path itself is fine — this only absorbs scheduling jitter).
    await screen.findByText("Recent", undefined, { timeout: 5000 })
    // Playlist 1–4 в «Недавних» и в общем списке, Playlist 5 — только в общем.
    expect(screen.getAllByText("Playlist 1")).toHaveLength(2)
    expect(screen.getAllByText("Playlist 5")).toHaveLength(1)

    fireEvent.click(screen.getAllByText("Playlist 3")[0])

    await waitFor(() => expect(addTracksToPlaylist).toHaveBeenCalledWith(3, [7]))
    await waitFor(() => expect(useUIStore.getState().addToPlaylistTrackIds).toBeNull())
    // Plain add never touches the source playlist.
    expect(removePlaylistTracks).not.toHaveBeenCalled()
  })

  it("не предлагает чужой public playlist как цель для записи", async () => {
    const own = { ...makePlaylist(1), editable: true }
    const foreign = { ...makePlaylist(2), editable: false, visibility: "public" as const }
    fetchPlaylists.mockResolvedValue({ items: [own, foreign], total: 2, limit: 200, offset: 0, next_offset: null })

    renderDialog()
    useUIStore.getState().openAddToPlaylist([7])

    expect(await screen.findAllByText("Playlist 1", undefined, { timeout: 5000 })).toHaveLength(2)
    expect(screen.queryByText("Playlist 2")).toBeNull()
  })

  it("кнопка «New playlist» открывает модалку создания с теми же треками", async () => {
    fetchPlaylists.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, next_offset: null })

    renderDialog()
    useUIStore.getState().openAddToPlaylist([11, 12])

    fireEvent.click(await screen.findByRole("button", { name: /new playlist/i }))

    expect(useUIStore.getState().addToPlaylistTrackIds).toBeNull()
    expect(useUIStore.getState().createPlaylistOptions).toEqual({ trackIds: [11, 12] })
  })

  it("«New playlist» переносит предложенное имя (например источник воспроизведения) в форму создания", async () => {
    fetchPlaylists.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, next_offset: null })

    renderDialog()
    useUIStore.getState().openAddToPlaylist([11, 12], "Instant Mix: Noisia - Exodus")

    fireEvent.click(await screen.findByRole("button", { name: /new playlist/i }))

    expect(useUIStore.getState().createPlaylistOptions).toEqual({
      trackIds: [11, 12],
      defaultTitle: "Instant Mix: Noisia - Exodus",
    })
  })

  it("не запрашивает плейлисты, пока модалка закрыта", () => {
    renderDialog()
    expect(fetchPlaylists).not.toHaveBeenCalled()
  })

  it("перенос: добавляет в выбранный плейлист, потом убирает из исходного; исходный в списке не предлагается", async () => {
    const items = [1, 2, 3].map(makePlaylist)
    fetchPlaylists.mockResolvedValue({ items, total: 3, limit: 200, offset: 0, next_offset: null })
    addTracksToPlaylist.mockResolvedValue({ added: 2, track_count: 5 })
    removePlaylistTracks.mockResolvedValue({ removed: 2, track_count: 0 })

    renderDialog()
    useUIStore.getState().openAddToPlaylist([7, 8], "Source", { moveFrom: 2 })

    expect(await screen.findByText("Move to playlist", undefined, { timeout: 5000 })).toBeInTheDocument()
    await screen.findAllByText("Playlist 1", undefined, { timeout: 5000 })
    expect(screen.queryByText("Playlist 2")).toBeNull()

    fireEvent.click(screen.getAllByText("Playlist 3")[0])

    await waitFor(() => expect(removePlaylistTracks).toHaveBeenCalledWith(2, [7, 8]))
    expect(addTracksToPlaylist).toHaveBeenCalledWith(3, [7, 8])
    expect(addTracksToPlaylist.mock.invocationCallOrder[0])
      .toBeLessThan(removePlaylistTracks.mock.invocationCallOrder[0])
    await waitFor(() => expect(useUIStore.getState().addToPlaylistTrackIds).toBeNull())
    expect(useUIStore.getState().addToPlaylistMoveFrom).toBeNull()
  })

  it("перенос: если добавить не удалось — из исходного плейлиста ничего не убирается", async () => {
    fetchPlaylists.mockResolvedValue({ items: [makePlaylist(3)], total: 1, limit: 200, offset: 0, next_offset: null })
    addTracksToPlaylist.mockRejectedValue(new Error("boom"))

    renderDialog()
    useUIStore.getState().openAddToPlaylist([7], undefined, { moveFrom: 2 })

    fireEvent.click((await screen.findAllByText("Playlist 3", undefined, { timeout: 5000 }))[0])

    await waitFor(() => expect(addTracksToPlaylist).toHaveBeenCalled())
    expect(removePlaylistTracks).not.toHaveBeenCalled()
    expect(useUIStore.getState().addToPlaylistTrackIds).toEqual([7])
  })

  it("перенос в новый плейлист передаёт исходный плейлист в модалку создания", async () => {
    fetchPlaylists.mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0, next_offset: null })

    renderDialog()
    useUIStore.getState().openAddToPlaylist([11], "Source", { moveFrom: 2 })

    fireEvent.click(await screen.findByRole("button", { name: /new playlist/i }))

    expect(useUIStore.getState().createPlaylistOptions).toEqual({
      trackIds: [11],
      defaultTitle: "Source",
      moveFromPlaylistId: 2,
    })
  })
})
