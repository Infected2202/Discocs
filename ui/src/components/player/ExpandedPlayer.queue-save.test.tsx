import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ExpandedPlayer from "./ExpandedPlayer"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"
import type { PlaybackQueue, PlaybackSession, QueueItem } from "@/api/types"

vi.mock("@/components/player/QueueItem", () => ({
  default: ({ trackId }: { trackId: number }) => <div data-testid="queue-item">{trackId}</div>,
}))

function makeItem(id: string, trackId: number): QueueItem {
  return {
    id,
    session_id: "s1",
    track_id: trackId,
    track: null,
    position: 0,
    origin: "source",
    source_type: null,
    source_id: null,
    status: "pending",
    locked: false,
    reason: null,
    score: null,
  } as QueueItem
}

function makeQueue(items: QueueItem[]): PlaybackQueue {
  return {
    items,
    current_index: 0,
    current_item: items[0] ?? null,
    upcoming: [],
    played: [],
    source_items: items,
    generated_items: [],
    autoplay_pool: [],
  }
}

function renderPlayer() {
  return render(
    <MemoryRouter>
      <ExpandedPlayer />
    </MemoryRouter>
  )
}

beforeEach(() => {
  usePlayerStore.setState({ queue: null, expanded: true, session: null })
  useUIStore.setState({ addToPlaylistTrackIds: null, addToPlaylistDefaultTitle: null, createPlaylistOptions: null })
})

describe("ExpandedPlayer — сохранение очереди в плейлист", () => {
  it("кнопка открывает AddToPlaylist с дедуплицированными track_id из queue.items", () => {
    usePlayerStore.setState({
      queue: makeQueue([
        makeItem("a", 10),
        makeItem("b", 20),
        makeItem("c", 10), // дубль — должен схлопнуться
        makeItem("d", 30),
      ]),
    })

    renderPlayer()
    fireEvent.click(screen.getByRole("button", { name: "Save queue to playlist" }))

    expect(useUIStore.getState().addToPlaylistTrackIds).toEqual([10, 20, 30])
  })

  it("подсказывает имя плейлиста как source_label текущей сессии", () => {
    usePlayerStore.setState({
      queue: makeQueue([makeItem("a", 10)]),
      session: { source_label: "Instant Mix: Noisia - Exodus" } as PlaybackSession,
    })

    renderPlayer()
    expect(screen.getByRole("button", { name: "Save queue to playlist" })).toHaveTextContent("Save")
    fireEvent.click(screen.getByRole("button", { name: "Save queue to playlist" }))

    expect(useUIStore.getState().addToPlaylistDefaultTitle).toBe("Instant Mix: Noisia - Exodus")
  })

  it("без source_label подсказка пустая", () => {
    usePlayerStore.setState({ queue: makeQueue([makeItem("a", 10)]), session: null })

    renderPlayer()
    fireEvent.click(screen.getByRole("button", { name: "Save queue to playlist" }))

    expect(useUIStore.getState().addToPlaylistDefaultTitle).toBeNull()
  })

  it("кнопки нет, когда очередь пуста", () => {
    usePlayerStore.setState({ queue: makeQueue([]) })

    renderPlayer()
    expect(screen.queryByRole("button", { name: "Save queue to playlist" })).toBeNull()
  })
})
