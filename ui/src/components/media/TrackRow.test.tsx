import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import TrackRow from "./TrackRow"
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
  return {
    ...makeTrack(),
    play_count: 1532,
    ...overrides,
  }
}

function renderRow(track: TrackSummary | ArtistTopTrack, props: Partial<React.ComponentProps<typeof TrackRow>> = {}) {
  return render(
    <MemoryRouter>
      <table>
        <tbody>
          <TrackRow track={track} {...props} />
        </tbody>
      </table>
    </MemoryRouter>,
  )
}

describe("TrackRow", () => {
  beforeEach(() => {
    playerState.currentTrackId = null
    playerState.playbackState = "paused"
    navidromeState.likedIds = new Set<number>()
    playSource.mockReset()
    togglePlay.mockReset()
    toggleLike.mockReset()
  })

  it("starts playback from the track when the row is inactive", () => {
    renderRow(makeTrack(), { sourceLabel: "Search results" })

    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0])

    expect(playSource).toHaveBeenCalledWith("track", 7, "Search results")
    expect(togglePlay).not.toHaveBeenCalled()
  })

  it("queues the whole release starting at this track when releaseId is set", () => {
    renderRow(makeTrack(), { sourceLabel: "Neon Lights", releaseId: 11 })

    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0])

    expect(playSource).toHaveBeenCalledWith("release", 11, "Neon Lights", 7)
  })

  it("toggles playback instead of restarting the source for the active track", () => {
    playerState.currentTrackId = 7
    playerState.playbackState = "playing"

    renderRow(makeTrack())

    fireEvent.click(screen.getAllByRole("button", { name: "Pause" })[0])

    expect(togglePlay).toHaveBeenCalledTimes(1)
    expect(playSource).not.toHaveBeenCalled()
  })

  it("renders release links for regular tracks and formatted duration", () => {
    renderRow(makeTrack(), { index: 2 })

    expect(screen.getByRole("link", { name: "Night Drive" })).toHaveAttribute("href", "/releases/11")
    expect(screen.getByRole("link", { name: "Neon Lights" })).toHaveAttribute("href", "/releases/11")
    expect(screen.getByRole("link", { name: "Synth Unit" })).toHaveAttribute("href", "/artists/4")
    expect(screen.getByText("4:05")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
  })

  it("renders play count text for artist top tracks without a release link", () => {
    renderRow(makeTopTrack({ release: null }))

    expect(screen.queryByRole("link", { name: "Night Drive" })).not.toBeInTheDocument()
    expect(screen.getByText("Night Drive")).toBeInTheDocument()
    expect(screen.getByText(/1,5/i)).toBeInTheDocument()
    expect(screen.getByText("прослушиваний")).toBeInTheDocument()
  })
})
