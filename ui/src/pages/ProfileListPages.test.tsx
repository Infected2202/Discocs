import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ProfileLikesPage, ProfilePlaylistsPage, ProfileTopPage } from "./ProfileListPages"
import type { MediaCardProps } from "@/components/media/MediaCard"

const useUserTopList = vi.fn()
const useUserLikesList = vi.fn()
const useUserPlaylistsList = vi.fn()
const useUserTopTracksList = vi.fn()

vi.mock("@/api/hooks/useProfile", () => ({
  useUserTopList: (...args: unknown[]) => useUserTopList(...args),
  useUserTopTracksList: (...args: unknown[]) => useUserTopTracksList(...args),
  useUserLikesList: (...args: unknown[]) => useUserLikesList(...args),
  useUserPlaylistsList: (...args: unknown[]) => useUserPlaylistsList(...args),
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ playSource: vi.fn(), playFromEnvelope: vi.fn() }),
}))

vi.mock("@/components/media/VirtualTrackList", () => ({
  default: ({ tracks, sourceLabel }: {
    tracks: Array<{ id: number; title: string; play_count: number }>
    sourceLabel?: string
  }) => (
    <div data-testid="track-list" data-source-label={sourceLabel}>
      {tracks.map((track) => (
        <span key={track.id} data-testid="track-row">{track.title}: {track.play_count}</span>
      ))}
    </div>
  ),
}))

vi.mock("@/components/media/FullListPage", () => ({
  FullListHeader: ({ title, subtitle, total }: { title: string; subtitle?: string; total?: number }) => (
    <div>
      <h1>{title}</h1>
      <p data-testid="subtitle">{subtitle}</p>
      <p data-testid="total">{total}</p>
    </div>
  ),
  useInfiniteScrollSentinel: () => ({ current: null }),
  default: ({ title, subtitle, source, toCard }: {
    title: string
    subtitle?: string
    source: { data?: { pages: Array<{ items: unknown[] }> } }
    toCard: (item: unknown) => MediaCardProps
  }) => (
    <div>
      <h1>{title}</h1>
      <p data-testid="subtitle">{subtitle}</p>
      {(source.data?.pages ?? []).flatMap((page) => page.items).map((item) => {
        const card = toCard(item)
        return <span key={String(card.id)} data-testid="card">{card.title} / {card.subtitle}</span>
      })}
    </div>
  ),
}))

const artwork = { url: null, source: "none" as const, placeholder: true }

function page<T>(items: T[]) {
  return { data: { pages: [{ items, total: items.length, next_offset: null }] } }
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/u/:username/top/:kind" element={<ProfileTopPage />} />
        <Route path="/u/:username/likes/:kind" element={<ProfileLikesPage />} />
        <Route path="/u/:username/playlists" element={<ProfilePlaylistsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("profile full lists", () => {
  beforeEach(() => {
    useUserTopTracksList.mockReset()
    useUserTopList.mockReset().mockReturnValue(page([{
      id: "release:4", entity_type: "release", entity_id: 4, title: "LP", subtitle: "Beta",
      artwork, reason: null, play_action: null, listens: 3,
    }]))
    useUserLikesList.mockReset().mockReturnValue(page([{
      id: "artist:2", entity_type: "artist", entity_id: 2, title: "Alpha", subtitle: null,
      artwork, reason: null, play_action: null,
    }]))
    useUserPlaylistsList.mockReset().mockReturnValue(page([{
      id: 8, title: "Night drive", kind: "manual", description: null, track_count: 3, artwork,
      source: null, visibility: "public", editable: false, created_at: "", updated_at: "",
      action: { type: "open", target: "/playlists/8" },
      play_action: { type: "post", endpoint: "/api/v1/playlists/8/play" },
    }]))
  })

  it("lists a top for the period from the URL, titled like its shelf", () => {
    renderAt("/u/alice/top/releases?period=7d")

    expect(useUserTopList).toHaveBeenCalledWith("alice", "releases", "7d")
    expect(screen.getByRole("heading", { name: "Top releases (7 days)" })).toBeInTheDocument()
    expect(screen.getByTestId("subtitle")).toHaveTextContent("alice")
    expect(screen.getByTestId("card")).toHaveTextContent("LP / Beta · 3 listens")
  })

  it("falls back to the default period without a valid one in the URL", () => {
    renderAt("/u/alice/top/artists?period=14d")

    expect(useUserTopList).toHaveBeenCalledWith("alice", "artists", "30d")
    expect(screen.getByRole("heading", { name: "Top artists (30 days)" })).toBeInTheDocument()
  })

  it("lists the period's top tracks as rows with their listens", () => {
    useUserTopTracksList.mockReturnValue({
      ...page([
        { id: 9, title: "Signals", listens: 7 },
        { id: 4, title: "Drift", listens: 2 },
      ]),
      isLoading: false,
      isFetchingNextPage: false,
      hasNextPage: false,
      fetchNextPage: vi.fn(),
    })

    renderAt("/u/alice/top/tracks?period=90d")

    expect(useUserTopTracksList).toHaveBeenCalledWith("alice", "90d")
    expect(useUserTopList).not.toHaveBeenCalled()
    expect(screen.getByRole("heading", { name: "Top tracks (90 days)" })).toBeInTheDocument()
    expect(screen.getByTestId("subtitle")).toHaveTextContent("alice")
    expect(screen.getByTestId("total")).toHaveTextContent("2")
    expect(screen.getAllByTestId("track-row").map((row) => row.textContent)).toEqual(["Signals: 7", "Drift: 2"])
  })

  it("rejects an unknown list kind without requesting anything", () => {
    renderAt("/u/alice/top/labels")
    expect(screen.getByText("No such list.")).toBeInTheDocument()
    expect(useUserTopList).not.toHaveBeenCalled()
    expect(useUserTopTracksList).not.toHaveBeenCalled()
  })

  it("lists one kind of likes", () => {
    renderAt("/u/alice/likes/artists")

    expect(useUserLikesList).toHaveBeenCalledWith("alice", "artists")
    expect(screen.getByRole("heading", { name: "Liked artists" })).toBeInTheDocument()
    expect(screen.getByTestId("card")).toHaveTextContent("Alpha")
  })

  it("rejects an unknown likes kind", () => {
    renderAt("/u/alice/likes/playlists")
    expect(screen.getByText("No such list.")).toBeInTheDocument()
    expect(useUserLikesList).not.toHaveBeenCalled()
  })

  it("lists the playlists", () => {
    renderAt("/u/alice/playlists")

    expect(useUserPlaylistsList).toHaveBeenCalledWith("alice")
    expect(screen.getByRole("heading", { name: "Playlists" })).toBeInTheDocument()
    expect(screen.getByTestId("card")).toHaveTextContent("Night drive / 3 tracks")
  })
})
