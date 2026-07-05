import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import QueueItem from "./QueueItem"
import type { TrackSummary } from "@/api/types"

const jumpToQueueItem = vi.fn()
const jumpToAutoplayItem = vi.fn()

const playerState = {
  session: { id: "sess-1" },
  currentTime: 30,
  duration: 200,
  playSource: vi.fn(),
  refreshQueue: vi.fn(),
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
})
