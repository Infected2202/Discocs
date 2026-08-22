import { act, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ExpandedPlayer from "./ExpandedPlayer"
import { usePlayerStore } from "@/store/playerStore"
import type { TrackSummary } from "@/api/types"

vi.mock("@/components/player/QueueItem", () => ({
  default: () => <div data-testid="queue-item" />,
}))

vi.mock("@/api/shares", () => ({
  useShareCapabilities: () => ({ data: { enabled: false, can_create: false } }),
  createShare: vi.fn(),
}))

function makeTrack(id: number, artworkUrl: string): TrackSummary {
  return {
    id,
    title: `Track ${id}`,
    duration: 100,
    artists: [{ id, name: "Artist" }],
    release: null,
    artwork: { url: artworkUrl, source: "release", placeholder: false },
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

describe("ExpandedPlayer — большая обложка трека", () => {
  beforeEach(() => {
    usePlayerStore.setState({
      expanded: true,
      currentTrack: makeTrack(1, "/art/1.jpg"),
      currentTrackId: 1,
      currentQueueItemId: null,
      session: null,
      queue: null,
    })
  })

  it("грузится eager, а не lazy — плеер смонтирован всегда и просто сдвинут за экран", () => {
    renderPlayer()
    const cover = screen.getByRole("img", { name: "Track 1" })
    expect(cover).toHaveAttribute("loading", "eager")
  })

  it("не залипает на fallback после сбоя загрузки — при смене трека обложка восстанавливается", () => {
    renderPlayer()
    const cover = screen.getByRole("img", { name: "Track 1" })

    fireEvent.error(cover)

    // Provoke the stuck-fallback bug: without a reset-on-src-change + remount key,
    // this fallback would still be showing for every later track too.
    expect(screen.queryByRole("img", { name: "Track 1" })).not.toBeInTheDocument()
    expect(screen.getByLabelText("Track 1")).toBeInTheDocument()

    act(() => {
      usePlayerStore.setState({
        currentTrack: makeTrack(2, "/art/2.jpg"),
        currentTrackId: 2,
      })
    })

    const nextCover = screen.getByRole("img", { name: "Track 2" })
    expect(nextCover).toHaveAttribute("src", expect.stringContaining("/art/2.jpg"))
    expect(nextCover).toHaveAttribute("loading", "eager")
  })
})
