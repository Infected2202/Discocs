import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ExpandedPlayer from "./ExpandedPlayer"
import { usePlayerStore } from "@/store/playerStore"
import { useNavidromeStore } from "@/store/navidromeStore"
import type { TrackSummary } from "@/api/types"

vi.mock("@/components/player/QueueItem", () => ({
  default: () => <div data-testid="queue-item" />,
}))

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
