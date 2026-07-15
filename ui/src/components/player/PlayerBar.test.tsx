import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import PlayerBar from "./PlayerBar"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import type { PlaybackQueue, QueueItem, TrackSummary } from "@/api/types"

const postEvent = vi.fn().mockResolvedValue({ accepted: true, duplicate: false, event_id: "e1", preference_delta: {} })

vi.mock("@/api/playback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playback")>()
  return { ...actual, postEvent: (...args: unknown[]) => postEvent(...args) }
})

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
    // queue.items на бэкенде содержит только текущий + предстоящие треки —
    // прошедший трек живёт только в playedHistory (клиентский стек).
    const prev = makeItem("a", makeTrack(1, "Previous Song"))
    const current = makeItem("b", makeTrack(2, "Current Song"))
    const next = makeItem("c", makeTrack(3, "Next Song"))

    usePlayerStore.setState({
      currentTrack: current.track,
      currentTrackId: 2,
      currentQueueItemId: "b",
      playbackState: "playing",
      queue: makeQueue([current, next]),
      playedHistory: [prev],
    })
  })

  it("превью скрыто, пока курсор не наведён на кнопку", () => {
    renderBar()
    expect(screen.queryByText("Next Song")).toBeNull()
    expect(screen.queryByText("Previous Song")).toBeNull()
  })

  it("фокус на Next track показывает обложку/тайтл/артиста следующего трека", () => {
    renderBar()
    const nextBtn = screen.getByRole("button", { name: "Next track" })

    fireEvent.focus(nextBtn)
    // Radix Tooltip renders content twice — a visible popper-positioned copy
    // plus a visually-hidden one for screen readers — hence getAllByText.
    expect(screen.getAllByText("Next Song").length).toBeGreaterThan(0)
    expect(screen.getAllByText("Artist 3").length).toBeGreaterThan(0)

    fireEvent.blur(nextBtn)
    expect(screen.queryByText("Next Song")).toBeNull()
  })

  it("фокус на Previous track показывает превью предыдущего трека", () => {
    renderBar()
    const prevBtn = screen.getByRole("button", { name: "Previous track" })

    fireEvent.focus(prevBtn)
    expect(screen.getAllByText("Previous Song").length).toBeGreaterThan(0)
  })

  it("нет превью, если следующего трека в очереди нет", () => {
    usePlayerStore.setState({
      queue: makeQueue([makeItem("b", makeTrack(2, "Current Song"))]),
      currentQueueItemId: "b",
    })
    renderBar()
    const nextBtn = screen.getByRole("button", { name: "Next track" })

    fireEvent.focus(nextBtn)
    expect(screen.queryByText("Next Song")).toBeNull()
  })

  it("нет превью Previous, если история ещё пуста (самый первый трек сессии)", () => {
    usePlayerStore.setState({ playedHistory: [] })
    renderBar()
    const prevBtn = screen.getByRole("button", { name: "Previous track" })

    fireEvent.focus(prevBtn)
    expect(screen.queryByText("Previous Song")).toBeNull()
  })
})

describe("PlayerBar — заливка иконки Like для лайкнутого трека", () => {
  beforeEach(() => {
    const current = makeItem("b", makeTrack(2, "Current Song"))
    usePlayerStore.setState({
      currentTrack: current.track,
      currentTrackId: 2,
      currentQueueItemId: "b",
      queue: makeQueue([current]),
    })
    useNavidromeStore.setState({ likedIds: new Set() })
  })

  it("иконка не залита, пока трек не лайкнут", () => {
    renderBar()
    const likeBtn = screen.getByTitle("Like")
    expect(likeBtn.querySelector("svg")).toHaveAttribute("fill", "none")
  })

  it("иконка заливается цветом после лайка", () => {
    useNavidromeStore.setState({ likedIds: new Set([2]) })
    renderBar()
    const likeBtn = screen.getByTitle("Like")
    expect(likeBtn.querySelector("svg")).toHaveAttribute("fill", "currentColor")
  })
})

describe("PlayerBar — кнопка Dislike", () => {
  beforeEach(() => {
    postEvent.mockClear()
    const current = makeItem("b", makeTrack(2, "Current Song"))
    usePlayerStore.setState({
      currentTrack: current.track,
      currentTrackId: 2,
      currentQueueItemId: "b",
      session: null,
      queue: makeQueue([current]),
    })
    useNavidromeStore.setState({ likedIds: new Set() })
  })

  it("отправляет событие 'disliked' для трека, а не переключает лайк", () => {
    renderBar()
    const dislikeBtn = screen.getByTitle("Dislike")

    fireEvent.click(dislikeBtn)

    expect(postEvent).toHaveBeenCalledWith(
      expect.objectContaining({ track_id: 2, event_type: "disliked" }),
    )
    expect(useNavidromeStore.getState().likedIds.has(2)).toBe(false)
  })
})

describe("PlayerBar — контекстное меню трека", () => {
  beforeEach(() => {
    const current = makeItem("b", makeTrack(2, "Current Song"))
    usePlayerStore.setState({
      currentTrack: current.track,
      currentTrackId: 2,
      currentQueueItemId: "b",
      session: null,
      queue: makeQueue([current]),
    })
  })

  it("показывает общий набор действий с треком", async () => {
    renderBar()

    fireEvent.pointerDown(screen.getByRole("button", { name: "Track options" }), {
      button: 0,
      ctrlKey: false,
    })

    expect(await screen.findByRole("menuitem", { name: "Play" })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Play next" })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Instant mix" })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Add to playlist" })).toBeInTheDocument()
    expect(screen.getByRole("menuitem", { name: "Go to Artist 2" })).toBeInTheDocument()
  })
})
