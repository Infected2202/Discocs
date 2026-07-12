import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import VirtualTrackRow from "./VirtualTrackRow"
import type { ArtistTopTrack, TrackSummary } from "@/api/types"

const playSource = vi.fn()
const togglePlay = vi.fn()
const toggleLike = vi.fn()

const playerState = {
  currentTrackId: null as number | null,
  playbackState: "paused",
  playSource,
  togglePlay,
}

const navidromeState = {
  likedIds: new Set<number>(),
  toggleLike,
}

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: typeof playerState) => unknown) => selector(playerState),
}))

vi.mock("@/store/navidromeStore", () => ({
  useNavidromeStore: (selector: (state: typeof navidromeState) => unknown) => selector(navidromeState),
}))

vi.mock("./ArtworkImage", () => ({
  default: ({ alt }: { alt: string }) => <img alt={alt} />,
}))

vi.mock("./TrackMenu", () => ({
  default: () => <div data-testid="track-menu" />,
}))

function makeTrack(overrides: Partial<TrackSummary> = {}): TrackSummary {
  return {
    id: 7,
    title: "Night Drive",
    duration: 245,
    artists: [{ id: 4, name: "Synth Unit" }],
    release: { id: 11, title: "Neon Lights" },
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
    ...overrides,
  }
}

function makeTopTrack(overrides: Partial<ArtistTopTrack> = {}): ArtistTopTrack {
  return { ...makeTrack(), play_count: 1532, ...overrides }
}

function renderRow(props: Partial<React.ComponentProps<typeof VirtualTrackRow>> = {}) {
  return render(
    <MemoryRouter>
      <VirtualTrackRow track={makeTrack()} index={0} {...props} />
    </MemoryRouter>,
  )
}

describe("VirtualTrackRow", () => {
  beforeEach(() => {
    playerState.currentTrackId = null
    playerState.playbackState = "paused"
    navidromeState.likedIds = new Set<number>()
    playSource.mockReset()
    togglePlay.mockReset()
    toggleLike.mockReset()
  })

  it("starts playback from the track when the row is inactive and has no onPlayTrack", () => {
    renderRow({ sourceLabel: "Search results" })

    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0])

    expect(playSource).toHaveBeenCalledWith("track", 7, "Search results")
    expect(togglePlay).not.toHaveBeenCalled()
  })

  it("plays the whole collection via onPlayTrack, positioned at this track", () => {
    const onPlayTrack = vi.fn()
    renderRow({ onPlayTrack })

    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0])

    expect(onPlayTrack).toHaveBeenCalledWith(7)
    expect(playSource).not.toHaveBeenCalled()
  })

  it("toggles playback instead of restarting the source for the active track", () => {
    playerState.currentTrackId = 7
    playerState.playbackState = "playing"

    renderRow({ onPlayTrack: vi.fn() })

    fireEvent.click(screen.getAllByRole("button", { name: "Pause" })[0])

    expect(togglePlay).toHaveBeenCalledTimes(1)
    expect(playSource).not.toHaveBeenCalled()
  })

  it("renders release + artist links and formatted duration", () => {
    renderRow()

    expect(screen.getByRole("link", { name: "Night Drive" })).toHaveAttribute("href", "/releases/11")
    expect(screen.getByRole("link", { name: "Synth Unit" })).toHaveAttribute("href", "/artists/4")
    expect(screen.getByText("4:05")).toBeInTheDocument()
  })

  it("renders play count for artist top tracks without a release link", () => {
    renderRow({ track: makeTopTrack({ release: null }) })

    expect(screen.queryByRole("link", { name: "Night Drive" })).not.toBeInTheDocument()
    expect(screen.getByText("Night Drive")).toBeInTheDocument()
    expect(screen.getByText(/1,5/)).toBeInTheDocument()
    expect(screen.getByText("прослушиваний")).toBeInTheDocument()
  })

  it("hides the metric for artist top tracks with zero plays", () => {
    renderRow({ track: makeTopTrack({ play_count: 0, release: null }) })

    expect(screen.queryByText("прослушиваний")).not.toBeInTheDocument()
  })
})
