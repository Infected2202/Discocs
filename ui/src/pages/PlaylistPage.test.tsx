import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import PlaylistPage from "./PlaylistPage"
import { useUIStore } from "@/store/uiStore"
import { ApiError } from "@/api/client"
import type { PlaylistDetail, TrackSummary } from "@/api/types"

const fetchPlaylist = vi.fn()
const fetchLikesPlaylist = vi.fn()
const deletePlaylist = vi.fn()
const removePlaylistTracks = vi.fn()
const reorderPlaylistTracks = vi.fn()
const playPlaylist = vi.fn()
const playLikes = vi.fn()
const playFromEnvelope = vi.fn()

vi.mock("@/components/common/DownloadMenu", () => ({
  default: ({ href }: { href: string }) => <a href={href} aria-label="Download" />,
}))

vi.mock("@/api/playlists", () => ({
  fetchPlaylist: (...args: unknown[]) => fetchPlaylist(...args),
  fetchLikesPlaylist: (...args: unknown[]) => fetchLikesPlaylist(...args),
  deletePlaylist: (...args: unknown[]) => deletePlaylist(...args),
  removePlaylistTracks: (...args: unknown[]) => removePlaylistTracks(...args),
  reorderPlaylistTracks: (...args: unknown[]) => reorderPlaylistTracks(...args),
  playPlaylist: (...args: unknown[]) => playPlaylist(...args),
  playLikes: (...args: unknown[]) => playLikes(...args),
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ playFromEnvelope }),
}))

// The page is tested through a stub list that surfaces the selection wiring.
vi.mock("@/components/media/VirtualTrackList", () => ({
  default: ({ tracks, selectable, onToggleSelect, onReorder, onPlayTrack, onRemoveTrack }: {
    tracks: TrackSummary[]
    selectable?: boolean
    onToggleSelect?: (id: number) => void
    onReorder?: (trackIds: number[]) => void
    onPlayTrack?: (trackId: number) => void
    onRemoveTrack?: (trackId: number) => void
  }) => (
    <div data-testid="track-list">
      {tracks.map((t) => (
        <div key={t.id}>
          <button
            data-testid={`select-${t.id}`}
            disabled={!selectable}
            onClick={() => onToggleSelect?.(t.id)}
          >
            {t.title}
          </button>
          <button data-testid={`play-${t.id}`} onClick={() => onPlayTrack?.(t.id)}>
            play {t.id}
          </button>
          {onRemoveTrack && (
            <button data-testid={`menu-remove-${t.id}`} onClick={() => onRemoveTrack(t.id)}>
              menu remove {t.id}
            </button>
          )}
        </div>
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

function renderPage(
  path: string,
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
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
  playPlaylist.mockReset().mockResolvedValue({ session: { id: "s1" } })
  playLikes.mockReset().mockResolvedValue({ session: { id: "s1" } })
  playFromEnvelope.mockReset().mockResolvedValue(undefined)
  useUIStore.setState({ addToPlaylistTrackIds: null, addToPlaylistMoveFrom: null, createPlaylistOptions: null })
})

describe("PlaylistPage — пользовательский плейлист", () => {
  it("рендерит детали, Edit открывает модалку в режиме редактирования", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())

    renderPage("/playlists/5")
    await screen.findByText("Road trip")
    expect(screen.getByText("Long drives")).toBeInTheDocument()
    expect(fetchPlaylist).toHaveBeenCalledWith(5)
    const download = screen.getByRole("link", { name: "Download" })
    expect(download).toHaveAttribute(
      "href",
      "/api/v1/playlists/5/download",
    )
    expect(download).not.toHaveTextContent("Download")

    fireEvent.click(screen.getByRole("button", { name: /edit/i }))
    expect(useUIStore.getState().createPlaylistOptions?.playlist?.id).toBe(5)
  })

  it("selection-бар: удаление треков только после подтверждения в модалке", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    removePlaylistTracks.mockResolvedValue({ removed: 2, track_count: 0 })

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("select-1"))
    fireEvent.click(screen.getByTestId("select-2"))
    expect(screen.getByText("2 selected")).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Remove from playlist" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Remove 2 tracks from playlist?")
    expect(removePlaylistTracks).not.toHaveBeenCalled()

    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }))
    await waitFor(() => expect(removePlaylistTracks).toHaveBeenCalledWith(5, [1, 2]))
    await waitFor(() => expect(screen.queryByText("2 selected")).toBeNull())
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("модалка удаления треков: Cancel ничего не удаляет и сохраняет выделение", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("select-1"))
    fireEvent.click(screen.getByRole("button", { name: "Remove from playlist" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(removePlaylistTracks).not.toHaveBeenCalled()
    expect(screen.getByText("1 selected")).toBeInTheDocument()
  })

  it("модалка удаления треков: ошибка API видна, модалка не закрывается", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    removePlaylistTracks.mockRejectedValue(new ApiError(500, "api_error", "boom"))

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("select-1"))
    fireEvent.click(screen.getByRole("button", { name: "Remove from playlist" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }))

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("That didn't work")
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("пункт меню трека убирает один трек через ту же модалку", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    removePlaylistTracks.mockResolvedValue({ removed: 1, track_count: 1 })

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("menu-remove-2"))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Remove track from playlist?")
    expect(dialog).toHaveTextContent('"Track 2" will be removed from "Road trip"')

    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }))
    await waitFor(() => expect(removePlaylistTracks).toHaveBeenCalledWith(5, [2]))
  })

  it("selection-бар: «Добавить» и «Перенести» открывают диалог выбора плейлиста", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("select-2"))

    fireEvent.click(screen.getByRole("button", { name: "Add to playlist" }))
    expect(useUIStore.getState().addToPlaylistTrackIds).toEqual([2])
    expect(useUIStore.getState().addToPlaylistMoveFrom).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Move to playlist" }))
    expect(useUIStore.getState().addToPlaylistTrackIds).toEqual([2])
    expect(useUIStore.getState().addToPlaylistMoveFrom).toBe(5)
    expect(removePlaylistTracks).not.toHaveBeenCalled()
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

  it("Delete плейлиста: без подтверждения в модалке ничего не удаляется", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent('The whole playlist "Road trip" (2 tracks) will be deleted')
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(deletePlaylist).not.toHaveBeenCalled()
    expect(screen.getByText("Road trip")).toBeInTheDocument()
  })

  it("Delete с подтверждением удаляет, уводит на дашборд и выкидывает плейлист из кэша", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    deletePlaylist.mockResolvedValue(undefined)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    renderPage("/playlists/5", queryClient)
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete playlist" }))

    await waitFor(() => expect(deletePlaylist).toHaveBeenCalledWith(5))
    await screen.findByTestId("dashboard")
    // Иначе «Назад» рисует удалённый плейлист из кэша, и все его кнопки молча падают в 404.
    expect(queryClient.getQueryData(["playlist", 5])).toBeUndefined()
  })

  it("Delete уже удалённого плейлиста (404) тоже уводит на дашборд, а не молчит", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    deletePlaylist.mockRejectedValue(new ApiError(404, "not_found", "Playlist not found"))

    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    const dialog = await screen.findByRole("dialog")
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete playlist" }))

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
    expect(screen.queryByTestId("menu-remove-1")).toBeNull()
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

describe("PlaylistPage — shuffle", () => {
  it("плейлист: просит у сервера уже перемешанную сессию", async () => {
    // Патч shuffle_enabled после старта не трогал порядок очереди: играл первый
    // трек списка, а перемешанным выглядел только значок в плеере. Сессия должна
    // создаваться перемешанной, тогда и первый трек случайный.
    fetchPlaylist.mockResolvedValue(makeDetail())
    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByRole("button", { name: "Shuffle" }))

    await waitFor(() => expect(playPlaylist).toHaveBeenCalledWith(5, { shuffle: true }))
    await waitFor(() => expect(playFromEnvelope).toHaveBeenCalled())
  })

  it("лайки: просит перемешанную сессию у своего эндпоинта", async () => {
    fetchLikesPlaylist.mockResolvedValue({
      id: "likes",
      title: "Liked Tracks",
      track_count: 1,
      tracks: [makeTrack(1)],
    })
    renderPage("/playlists/likes")
    await screen.findByText("Liked Tracks")

    fireEvent.click(screen.getByRole("button", { name: "Shuffle" }))

    await waitFor(() => expect(playLikes).toHaveBeenCalledWith({ shuffle: true }))
    expect(playPlaylist).not.toHaveBeenCalled()
  })

  it("Play остаётся линейным", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByRole("button", { name: "Play" }))

    await waitFor(() => expect(playPlaylist).toHaveBeenCalledWith(5, undefined))
  })

  it("клик по треку играет с него и не перемешивает", async () => {
    fetchPlaylist.mockResolvedValue(makeDetail())
    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    fireEvent.click(screen.getByTestId("play-2"))

    await waitFor(() => expect(playPlaylist).toHaveBeenCalledWith(5, undefined))
    await waitFor(() => expect(playFromEnvelope).toHaveBeenCalledWith(expect.anything(), 2))
  })

  it("нечего перемешивать в пустом списке — кнопки нет", async () => {
    fetchPlaylist.mockResolvedValue({ ...makeDetail(), tracks: [], track_count: 0 })
    renderPage("/playlists/5")
    await screen.findByText("Road trip")

    expect(screen.queryByRole("button", { name: "Shuffle" })).toBeNull()
  })
})

describe("PlaylistPage — состояния ошибки", () => {
  it("показывает 'not found' для настоящей 404-ошибки", async () => {
    fetchPlaylist.mockRejectedValue(new ApiError(404, "not_found", "no such playlist"))

    renderPage("/playlists/5")

    expect(await screen.findByText("Playlist not found.")).toBeInTheDocument()
  })

  it("показывает сообщение о переподключении для сетевой ошибки, а не 'not found'", async () => {
    fetchPlaylist.mockRejectedValue(new TypeError("Failed to fetch"))

    renderPage("/playlists/5")

    expect(await screen.findByText("Can't reach the server. Retrying automatically…")).toBeInTheDocument()
    expect(screen.queryByText("Playlist not found.")).toBeNull()
  })
})
