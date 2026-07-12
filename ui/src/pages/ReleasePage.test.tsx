import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ReleasePage from "./ReleasePage"
import type {
  ReleaseResponse, ReleaseTracksResponse, RelatedDiscographyResponse, ReleaseAvailabilityStub,
} from "@/api/types"

const useRelease = vi.fn()
const useReleaseTracks = vi.fn()
const useReleaseRelated = vi.fn()
const useReleaseRecommendations = vi.fn()

vi.mock("@/api/hooks/useRelease", () => ({
  useRelease: (...args: unknown[]) => useRelease(...args),
  useReleaseTracks: (...args: unknown[]) => useReleaseTracks(...args),
  useReleaseRelated: (...args: unknown[]) => useReleaseRelated(...args),
  useReleaseRecommendations: (...args: unknown[]) => useReleaseRecommendations(...args),
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (s: { playSource: () => void; toggleShuffle: () => void }) => unknown) =>
    selector({ playSource: vi.fn(), toggleShuffle: vi.fn() }),
}))

vi.mock("@/store/navidromeStore", () => ({
  useNavidromeStore: (selector: (s: { toggleAlbumLike: () => void; likedAlbumIds: Set<number> }) => unknown) =>
    selector({ toggleAlbumLike: vi.fn(), likedAlbumIds: new Set() }),
}))

vi.mock("@/components/media/VirtualTrackList", () => ({
  default: () => <div data-testid="track-list" />,
}))

vi.mock("@/components/media/ArtworkImage", () => ({
  default: ({ alt }: { alt: string }) => <img alt={alt} />,
}))

function makeReleaseData(): ReleaseResponse {
  return {
    release: {
      id: 5,
      title: "Neon Lights",
      release_type: "album",
      release_type_label: "Album",
      artists: [{ id: 1, name: "Synth Unit" }],
      release_date: null,
      release_year: 2024,
      track_count: 10,
      duration: 2400,
      artwork: { url: null, source: "placeholder", placeholder: true },
    },
    actions: [],
    links: {},
  }
}

function makeTracksData(): ReleaseTracksResponse {
  return { release: { id: 5, title: "Neon Lights" }, items: [] }
}

function makeRelatedData(): RelatedDiscographyResponse {
  return { release: { id: 5, title: "Neon Lights" }, context_artists: [], items: [] }
}

function makeRecsData(): ReleaseAvailabilityStub {
  return {
    release: { id: 5, title: "Neon Lights" },
    available: true,
    basis: "similarity",
    items: [
      {
        id: "release:9",
        entity_type: "release",
        entity_id: 9,
        title: "Recommended Album",
        subtitle: "Other Artist",
        artwork: { url: null, source: "placeholder", placeholder: true },
        reason: null,
        action: { type: "open", target: "/releases/9" },
        play_action: { type: "play", source_type: "release", source_id: 9 },
      },
    ],
  }
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/releases/5"]}>
        <Routes>
          <Route path="/releases/:id" element={<ReleasePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("ReleasePage — шелф рекомендаций без битого 'More'", () => {
  beforeEach(() => {
    useRelease.mockReturnValue({ data: makeReleaseData(), isLoading: false, error: null })
    useReleaseTracks.mockReturnValue({ data: makeTracksData(), isLoading: false })
    useReleaseRelated.mockReturnValue({ data: makeRelatedData() })
    useReleaseRecommendations.mockReturnValue({ data: makeRecsData() })
  })

  it("рендерит рекомендованные альбомы без кнопки More (у release_recommendations нет dashboard-шелфа)", async () => {
    renderPage()
    await screen.findByText("Recommended Album")

    expect(screen.getByText("Recommended Albums")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "More" })).toBeNull()
  })
})
