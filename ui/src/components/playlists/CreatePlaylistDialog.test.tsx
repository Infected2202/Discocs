import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import CreatePlaylistDialog from "./CreatePlaylistDialog"
import { useUIStore } from "@/store/uiStore"
import type { PlaylistSummary } from "@/api/types"

const createPlaylist = vi.fn()
const updatePlaylist = vi.fn()

vi.mock("@/api/playlists", () => ({
  createPlaylist: (...args: unknown[]) => createPlaylist(...args),
  updatePlaylist: (...args: unknown[]) => updatePlaylist(...args),
}))

function makePlaylist(id: number): PlaylistSummary {
  return {
    id,
    title: `Playlist ${id}`,
    kind: "manual",
    description: "Old description",
    track_count: 3,
    artwork: { url: null, source: "placeholder", placeholder: true },
    source: { visibility: "public" },
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
      <CreatePlaylistDialog />
    </QueryClientProvider>
  )
}

beforeEach(() => {
  createPlaylist.mockReset()
  updatePlaylist.mockReset()
  useUIStore.setState({ addToPlaylistTrackIds: null, createPlaylistOptions: null })
})

describe("CreatePlaylistDialog", () => {
  it("создаёт плейлист с предзаполнением, visibility и треками; закрывается после", async () => {
    createPlaylist.mockResolvedValue(makePlaylist(1))

    renderDialog()
    useUIStore.getState().openCreatePlaylist({
      defaultTitle: "Mix title",
      defaultDescription: "From the mix",
      trackIds: [5, 6],
    })

    const nameInput = await screen.findByPlaceholderText("Playlist name")
    expect(nameInput).toHaveValue("Mix title")
    fireEvent.change(nameInput, { target: { value: "  My playlist  " } })
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "public" } })
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    await waitFor(() =>
      expect(createPlaylist).toHaveBeenCalledWith({
        title: "My playlist",
        description: "From the mix",
        visibility: "public",
        track_ids: [5, 6],
      })
    )
    await waitFor(() => expect(useUIStore.getState().createPlaylistOptions).toBeNull())
  })

  it("кнопка Create заблокирована при пустом названии", async () => {
    renderDialog()
    useUIStore.getState().openCreatePlaylist()

    const nameInput = await screen.findByPlaceholderText("Playlist name")
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled()

    fireEvent.change(nameInput, { target: { value: "   " } })
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled()

    fireEvent.change(nameInput, { target: { value: "Ok" } })
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled()
    expect(createPlaylist).not.toHaveBeenCalled()
  })

  it("режим редактирования: PATCH вместо создания, без селекта видимости", async () => {
    updatePlaylist.mockResolvedValue(makePlaylist(9))

    renderDialog()
    useUIStore.getState().openCreatePlaylist({ playlist: makePlaylist(9) })

    const nameInput = await screen.findByPlaceholderText("Playlist name")
    expect(nameInput).toHaveValue("Playlist 9")
    expect(screen.queryByRole("combobox")).toBeNull()

    fireEvent.change(nameInput, { target: { value: "Renamed" } })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() =>
      expect(updatePlaylist).toHaveBeenCalledWith(9, {
        title: "Renamed",
        description: "Old description",
      })
    )
    expect(createPlaylist).not.toHaveBeenCalled()
    await waitFor(() => expect(useUIStore.getState().createPlaylistOptions).toBeNull())
  })

  it("onSubmit-переопределение заменяет создание (сценарий сохранения микса)", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)

    renderDialog()
    useUIStore.getState().openCreatePlaylist({ defaultTitle: "Mix", onSubmit })

    await screen.findByPlaceholderText("Playlist name")
    fireEvent.click(screen.getByRole("button", { name: "Create" }))

    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ title: "Mix", description: "", visibility: "private" })
    )
    expect(createPlaylist).not.toHaveBeenCalled()
    await waitFor(() => expect(useUIStore.getState().createPlaylistOptions).toBeNull())
  })
})
