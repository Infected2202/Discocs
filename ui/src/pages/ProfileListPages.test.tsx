import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ProfileLikesPage, ProfilePlaylistsPage, ProfileTopPage } from "./ProfileListPages"
import type { MediaCardProps } from "@/components/media/MediaCard"

const useUserTopList = vi.fn()
const useUserLikesList = vi.fn()
const useUserPlaylistsList = vi.fn()

vi.mock("@/api/hooks/useProfile", () => ({
  useUserTopList: (...args: unknown[]) => useUserTopList(...args),
  useUserLikesList: (...args: unknown[]) => useUserLikesList(...args),
  useUserPlaylistsList: (...args: unknown[]) => useUserPlaylistsList(...args),
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: Record<string, unknown>) => unknown) =>
    selector({ playSource: vi.fn(), playFromEnvelope: vi.fn() }),
}))

vi.mock("@/components/media/FullListPage", () => ({
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

  it("rejects an unknown list kind without requesting anything", () => {
    renderAt("/u/alice/top/tracks")
    expect(screen.getByText("No such list.")).toBeInTheDocument()
    expect(useUserTopList).not.toHaveBeenCalled()
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
