import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import QueueItem from "./QueueItem"
import type { TrackSummary } from "@/api/types"

const jumpToQueueItem = vi.fn()
const jumpToAutoplayItem = vi.fn()
const refreshQueue = vi.fn()
const patchQueue = vi.fn().mockResolvedValue({})

const playerState = {
  session: { id: "sess-1" },
  currentTime: 30,
  duration: 200,
  playSource: vi.fn(),
  refreshQueue,
  playFromEnvelope: vi.fn(),
  jumpToQueueItem,
  jumpToAutoplayItem,
}

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (sel: (s: typeof playerState) => unknown) => sel(playerState),
}))

vi.mock("@/store/navidromeStore", () => ({
  useNavidromeStore: (sel: (s: object) => unknown) =>
    sel({ likedIds: new Set(), toggleLike: vi.fn() }),
}))

vi.mock("@/api/playback", () => ({
  patchQueue: (...args: unknown[]) => patchQueue(...args),
}))

// TrackMenu itself owns a Radix DropdownMenu (Play/Add to playlist/etc.) — that's
// covered by consolidating onto it, not re-tested here. This stub only surfaces
// the one prop QueueItem is actually responsible for: onRemoveFromQueue.
vi.mock("@/components/media/TrackMenu", () => ({
  default: ({ onRemoveFromQueue }: { onRemoveFromQueue?: () => void }) =>
    onRemoveFromQueue ? (
      <button onClick={onRemoveFromQueue}>Remove from queue</button>
    ) : (
      <div data-testid="track-menu-no-remove" />
    ),
}))

function makeTrack(id: number): TrackSummary {
  return {
    id,
    title: `Track ${id}`,
    duration: 100,
    artists: [{ id: 1, name: "Artist" }],
    release: { id: 1, title: "Album" },
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function renderRow(props: Partial<React.ComponentProps<typeof QueueItem>>) {
  return render(
    <MemoryRouter>
      <QueueItem track={makeTrack(1)} trackId={1} {...props} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  jumpToQueueItem.mockClear()
  jumpToAutoplayItem.mockClear()
  refreshQueue.mockClear()
  patchQueue.mockClear()
})

describe("QueueItem", () => {
  it("текущий трек показывает живое время (currentTime / duration)", () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: true })
    // currentTime=30, duration=200 → 0:30 / 3:20
    expect(screen.getByText("0:30 / 3:20")).toBeInTheDocument()
  })

  it("не-текущий трек показывает статичную длительность трека", () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: false })
    // track.duration=100 → 1:40, и НЕ живое время
    expect(screen.getByText("1:40")).toBeInTheDocument()
    expect(screen.queryByText("0:30 / 3:20")).not.toBeInTheDocument()
  })

  it("клик по строке очереди зовёт jumpToQueueItem с itemId", () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: false })
    fireEvent.click(screen.getByText("Track 1"))
    expect(jumpToQueueItem).toHaveBeenCalledWith("q1")
    expect(jumpToAutoplayItem).not.toHaveBeenCalled()
  })

  it("клик по строке autoplay зовёт jumpToAutoplayItem с itemId", () => {
    renderRow({ itemId: "p1", variant: "autoplay", dimmed: true })
    fireEvent.click(screen.getByText("Track 1"))
    expect(jumpToAutoplayItem).toHaveBeenCalledWith("p1")
    expect(jumpToQueueItem).not.toHaveBeenCalled()
  })

  it("текущий трек не кликается (jump не вызывается)", () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: true })
    fireEvent.click(screen.getByText("Track 1"))
    expect(jumpToQueueItem).not.toHaveBeenCalled()
  })

  it("строка без itemId не вызывает jump", () => {
    renderRow({ itemId: undefined, variant: "queue", isCurrent: false })
    fireEvent.click(screen.getByText("Track 1"))
    expect(jumpToQueueItem).not.toHaveBeenCalled()
    expect(jumpToAutoplayItem).not.toHaveBeenCalled()
  })

  it("обычная строка очереди даёт TrackMenu возможность удалить из очереди", () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: false })
    expect(screen.getByText("Remove from queue")).toBeInTheDocument()
  })

  it("текущий трек нельзя удалить из очереди", () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: true })
    expect(screen.queryByText("Remove from queue")).not.toBeInTheDocument()
    expect(screen.getByTestId("track-menu-no-remove")).toBeInTheDocument()
  })

  it("автоплей-превью нельзя удалить из очереди (там ещё нет)", () => {
    renderRow({ itemId: "p1", variant: "autoplay" })
    expect(screen.queryByText("Remove from queue")).not.toBeInTheDocument()
  })

  it("удаление шлёт patchQueue(remove, itemId) и обновляет очередь", async () => {
    renderRow({ itemId: "q1", variant: "queue", isCurrent: false })

    fireEvent.click(screen.getByText("Remove from queue"))

    await waitFor(() => expect(refreshQueue).toHaveBeenCalledTimes(1))
    expect(patchQueue).toHaveBeenCalledWith("sess-1", { operation: "remove", queue_item_id: "q1" })
  })
})
