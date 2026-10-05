import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ReleasePage from "./ReleasePage"
import { ApiError } from "@/api/client"
import type {
  ReleaseResponse, ReleaseTracksResponse, RelatedDiscographyResponse, ReleaseAvailabilityStub,
} from "@/api/types"

const useRelease = vi.fn()
const useReleaseTracks = vi.fn()
const useReleaseRelated = vi.fn()
const useReleaseRecommendations = vi.fn()
const useShareCapabilities = vi.fn()
const useLabelReleases = vi.fn()

vi.mock("@/api/hooks/useRelease", () => ({
  useRelease: (...args: unknown[]) => useRelease(...args),
  useReleaseTracks: (...args: unknown[]) => useReleaseTracks(...args),
  useReleaseRelated: (...args: unknown[]) => useReleaseRelated(...args),
  useReleaseRecommendations: (...args: unknown[]) => useReleaseRecommendations(...args),
}))

vi.mock("@/api/hooks/useLabel", () => ({
  useLabelReleases: (...args: unknown[]) => useLabelReleases(...args),
}))

vi.mock("@/api/shares", () => ({
  useShareCapabilities: () => useShareCapabilities(),
  createShare: vi.fn(),
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
          <Route path="/releases/:id/recommendations" element={<p>recommendations list</p>} />
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
    useShareCapabilities.mockReturnValue({ data: { enabled: true, can_create: true } })
  })

  it("рендерит рекомендованные альбомы без кнопки More, когда полка показывает весь список", async () => {
    useReleaseRecommendations.mockReturnValue({ data: { ...makeRecsData(), total: 1 } })
    renderPage()
    await screen.findByText("Recommended Album")

    expect(screen.getByText("Recommended Albums")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "More" })).toBeNull()
  })

  it("ведёт «More» рекомендаций на их полный список, когда альбомов больше, чем на полке", async () => {
    useReleaseRecommendations.mockReturnValue({ data: { ...makeRecsData(), total: 40 } })
    renderPage()
    await screen.findByText("Recommended Album")

    fireEvent.click(screen.getByRole("button", { name: "More" }))

    expect(await screen.findByText("recommendations list")).toBeInTheDocument()
  })

  it("показывает download и share иконками перед завершающим лайком", () => {
    useReleaseTracks.mockReturnValue({
      data: {
        ...makeTracksData(),
        items: [{ id: 7, title: "Track", artists: [], release: null }],
      },
      isLoading: false,
    })

    renderPage()

    const download = screen.getByRole("link", { name: "Download" })
    const share = screen.getByRole("button", { name: "Share" })
    const like = screen.getByRole("button", { name: "Like" })

    expect(download).toHaveAttribute(
      "href",
      "/api/v1/releases/5/download",
    )
    expect(download).not.toHaveTextContent("Download")
    expect(share).toHaveAttribute("data-size", "icon-sm")
    expect(share).not.toHaveTextContent("Share")
    expect(share.querySelector("svg.lucide-share-2")).toBeTruthy()
    expect(download.compareDocumentPosition(share)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(share.compareDocumentPosition(like)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(download.compareDocumentPosition(like)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it("показывает Shuffle только иконкой и не выводит тип релиза", () => {
    renderPage()

    const shuffle = screen.getByRole("button", { name: "Shuffle" })
    expect(shuffle).toHaveAttribute("data-size", "icon-sm")
    expect(shuffle).not.toHaveTextContent("Shuffle")
    expect(screen.queryByText("Album")).toBeNull()
  })
})

describe("ReleasePage — состояния ошибки", () => {
  beforeEach(() => {
    useReleaseTracks.mockReturnValue({ data: makeTracksData(), isLoading: false })
    useReleaseRelated.mockReturnValue({ data: makeRelatedData() })
    useReleaseRecommendations.mockReturnValue({ data: makeRecsData() })
    useShareCapabilities.mockReturnValue({ data: { enabled: true, can_create: true } })
  })

  it("показывает 'not found' для настоящей 404-ошибки", async () => {
    useRelease.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiError(404, "not_found", "no such release"),
    })

    renderPage()

    expect(await screen.findByText("Release not found.")).toBeInTheDocument()
  })

  it("показывает сообщение о переподключении для сетевой ошибки, а не 'not found'", async () => {
    useRelease.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new TypeError("Failed to fetch"),
    })

    renderPage()

    expect(await screen.findByText("Can't reach the server. Retrying automatically…")).toBeInTheDocument()
    expect(screen.queryByText("Release not found.")).toBeNull()
  })
})

describe("ReleasePage — лейбл релиза", () => {
  beforeEach(() => {
    useLabelReleases.mockReturnValue({ data: undefined })
    useReleaseTracks.mockReturnValue({ data: makeTracksData(), isLoading: false })
    useReleaseRelated.mockReturnValue({ data: makeRelatedData() })
    useReleaseRecommendations.mockReturnValue({ data: makeRecsData() })
    useShareCapabilities.mockReturnValue({ data: { enabled: true, can_create: true } })
  })

  it("показывает лейблы ссылками на страницы лейблов", async () => {
    const data = makeReleaseData()
    data.release.labels = [{ id: 7, name: "Warp" }, { id: 8, name: "Bleep" }]
    useRelease.mockReturnValue({ data, isLoading: false, error: null })

    renderPage()

    expect(await screen.findByRole("link", { name: "Warp" })).toHaveAttribute("href", "/labels/7")
    expect(screen.getByRole("link", { name: "Bleep" })).toHaveAttribute("href", "/labels/8")
    expect(screen.getByLabelText("Labels")).toHaveTextContent("· Warp, Bleep")
  })

  it("показывает жанры релиза под строкой артистов", async () => {
    const data = makeReleaseData()
    data.release.genres = ["Techno", "Schranz", "Hard Techno"]
    useRelease.mockReturnValue({ data, isLoading: false, error: null })

    renderPage()

    const genres = await screen.findByRole("list", { name: "Genres" })
    expect(within(genres).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Techno", "Schranz", "Hard Techno",
    ])
  })

  it("ничего не показывает, если у релиза нет лейбла", async () => {
    useRelease.mockReturnValue({ data: makeReleaseData(), isLoading: false, error: null })

    renderPage()

    await screen.findByRole("heading", { name: "Neon Lights" })
    expect(screen.queryByLabelText("Labels")).toBeNull()
    expect(screen.queryByRole("list", { name: "Genres" })).toBeNull()
  })
})

function labelRelease(id: number, title: string, artistId: number, artist: string) {
  return {
    id, title, release_type: "album", artists: [{ id: artistId, name: artist }],
    release_year: 2020, track_count: 8, artwork: { url: null, source: "placeholder", placeholder: true },
  }
}

describe("ReleasePage — полка «От этого лейбла»", () => {
  beforeEach(() => {
    useReleaseTracks.mockReturnValue({ data: makeTracksData(), isLoading: false })
    useReleaseRelated.mockReturnValue({ data: makeRelatedData() })
    useReleaseRecommendations.mockReturnValue({ data: { ...makeRecsData(), items: [] } })
    useShareCapabilities.mockReturnValue({ data: { enabled: true, can_create: true } })
  })

  it("показывает популярные релизы лейбла других артистов без текущего релиза и релизов его артистов", async () => {
    const data = makeReleaseData()
    data.release.labels = [{ id: 7, name: "Warp" }]
    useRelease.mockReturnValue({ data, isLoading: false, error: null })
    useLabelReleases.mockReturnValue({
      data: {
        label: { id: 7, name: "Warp" }, sort: "popularity", groups: [],
        items: [
          labelRelease(5, "Neon Lights", 1, "Synth Unit"),
          labelRelease(11, "Selected Ambient Works", 2, "Aphex Twin"),
          labelRelease(12, "Older Synth Unit Album", 1, "Synth Unit"),
          labelRelease(13, "Geogaddi", 3, "Boards of Canada"),
        ],
      },
    })

    renderPage()

    expect(await screen.findByText("From this label")).toBeInTheDocument()
    expect(useLabelReleases).toHaveBeenCalledWith(7, "popularity")
    expect(screen.getByText("Selected Ambient Works")).toBeInTheDocument()
    expect(screen.getByText("Geogaddi")).toBeInTheDocument()
    expect(screen.queryByText("Older Synth Unit Album")).toBeNull()
  })

  it("называет лейбл в заголовке, когда у релиза их несколько, и прячет полку лейбла самого артиста", async () => {
    const data = makeReleaseData()
    data.release.labels = [{ id: 7, name: "Warp" }, { id: 8, name: "Synth Unit Records" }]
    useRelease.mockReturnValue({ data, isLoading: false, error: null })
    useLabelReleases.mockImplementation((id: number) => ({
      data: {
        label: { id, name: "" }, sort: "popularity", groups: [],
        items: id === 7
          ? [labelRelease(11, "Selected Ambient Works", 2, "Aphex Twin")]
          : [labelRelease(12, "Older Synth Unit Album", 1, "Synth Unit")],
      },
    }))

    renderPage()

    expect(await screen.findByText("From Warp")).toBeInTheDocument()
    expect(screen.queryByText("From Synth Unit Records")).toBeNull()
    expect(screen.queryByText("From this label")).toBeNull()
  })
})
