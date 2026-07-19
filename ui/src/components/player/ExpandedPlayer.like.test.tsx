import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ExpandedPlayer from "./ExpandedPlayer"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import type { TrackSummary } from "@/api/types"

vi.mock("@/components/player/QueueItem", () => ({
  default: () => <div data-testid="queue-item" />,
}))

vi.mock("@/api/shares", () => ({
  useShareCapabilities: () => ({ data: { enabled: false, can_create: false } }),
  createShare: vi.fn(),
}))

const postEvent = vi.fn().mockResolvedValue({ accepted: true, duplicate: false, event_id: "e1", preference_delta: {} })

vi.mock("@/api/playback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/playback")>()
  return { ...actual, postEvent: (...args: unknown[]) => postEvent(...args) }
})

function makeTrack(id: number): TrackSummary {
  return {
    id,
    title: "Current Song",
    duration: 100,
    artists: [{ id, name: "Artist" }],
    release: null,
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function renderPlayer() {
  return render(
    <MemoryRouter>
      <ExpandedPlayer />
    </MemoryRouter>,
  )
}

describe("ExpandedPlayer — заливка иконки Like для лайкнутого трека", () => {
  beforeEach(() => {
    usePlayerStore.setState({
      expanded: true,
      currentTrack: makeTrack(2),
      currentTrackId: 2,
      queue: null,
    })
    useNavidromeStore.setState({ likedIds: new Set() })
  })

  it("иконка не залита, пока трек не лайкнут", () => {
    renderPlayer()
    const likeBtn = screen.getAllByRole("button").find((b) => b.querySelector("svg.lucide-thumbs-up"))
    expect(likeBtn?.querySelector("svg")).toHaveAttribute("fill", "none")
  })

  it("иконка заливается цветом после лайка", () => {
    useNavidromeStore.setState({ likedIds: new Set([2]) })
    renderPlayer()
    const likeBtn = screen.getAllByRole("button").find((b) => b.querySelector("svg.lucide-thumbs-up"))
    expect(likeBtn?.querySelector("svg")).toHaveAttribute("fill", "currentColor")
  })
})

describe("ExpandedPlayer — кнопка Dislike", () => {
  beforeEach(() => {
    postEvent.mockClear()
    usePlayerStore.setState({
      expanded: true,
      currentTrack: makeTrack(2),
      currentTrackId: 2,
      currentQueueItemId: null,
      session: null,
      queue: null,
    })
    useNavidromeStore.setState({ likedIds: new Set() })
  })

  it("отправляет событие 'disliked' для текущего трека, а не переключает лайк", () => {
    renderPlayer()
    const dislikeBtn = screen.getByTitle("Dislike")

    fireEvent.click(dislikeBtn)

    expect(postEvent).toHaveBeenCalledWith(
      expect.objectContaining({ track_id: 2, event_type: "disliked" }),
    )
  })
})
