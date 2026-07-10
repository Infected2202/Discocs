import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it } from "vitest"
import PlayerBar from "./PlayerBar"
import { usePlayerStore } from "@/store/playerStore"
import type { PlaybackQueue, QueueItem, TrackSummary } from "@/api/types"

function makeTrack(id: number, title: string): TrackSummary {
  return {
    id,
    title,
    duration: 100,
    artists: [{ id, name: `Artist ${id}` }],
    release: null,
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function makeItem(id: string, track: TrackSummary | null): QueueItem {
  return {
    id,
    session_id: "s1",
    track_id: track?.id ?? 0,
    track,
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

function renderBar() {
  return render(
    <MemoryRouter>
      <PlayerBar />
    </MemoryRouter>,
  )
}

describe("PlayerBar — превью трека при наведении на кнопки скипа", () => {
  beforeEach(() => {
    const prev = makeItem("a", makeTrack(1, "Previous Song"))
    const current = makeItem("b", makeTrack(2, "Current Song"))
    const next = makeItem("c", makeTrack(3, "Next Song"))

    usePlayerStore.setState({
      currentTrack: current.track,
      currentTrackId: 2,
      currentQueueItemId: "b",
      playbackState: "playing",
      queue: makeQueue([prev, current, next]),
    })
  })

  it("превью скрыто, пока курсор не наведён на кнопку", () => {
    renderBar()
    expect(screen.queryByText("Next Song")).toBeNull()
    expect(screen.queryByText("Previous Song")).toBeNull()
  })

  it("наведение на Next track показывает обложку/тайтл/артиста следующего трека", () => {
    renderBar()
    const nextBtn = screen.getByRole("button", { name: "Next track" })

    fireEvent.mouseEnter(nextBtn.parentElement!)
    expect(screen.getByText("Next Song")).toBeInTheDocument()
    expect(screen.getByText("Artist 3")).toBeInTheDocument()

    fireEvent.mouseLeave(nextBtn.parentElement!)
  })

  it("наведение на Previous track показывает превью предыдущего трека", () => {
    renderBar()
    const prevBtn = screen.getByRole("button", { name: "Previous track" })

    fireEvent.mouseEnter(prevBtn.parentElement!)
    expect(screen.getByText("Previous Song")).toBeInTheDocument()
  })

  it("нет превью, если следующего трека в очереди нет", () => {
    usePlayerStore.setState({
      queue: makeQueue([makeItem("b", makeTrack(2, "Current Song"))]),
      currentQueueItemId: "b",
    })
    renderBar()
    const nextBtn = screen.getByRole("button", { name: "Next track" })

    fireEvent.mouseEnter(nextBtn.parentElement!)
    expect(screen.queryByText("Next Song")).toBeNull()
  })
})
