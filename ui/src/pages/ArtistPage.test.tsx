import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ArtistPage from "./ArtistPage"
import { ApiError } from "@/api/client"
import type { ArtistResponse, ArtistDiscographyResponse, ArtistLabelsResponse, ArtistSimilarResponse } from "@/api/types"

const useArtist = vi.fn()
const useArtistDiscography = vi.fn()
const useArtistSimilar = vi.fn()
// Без лейблов по умолчанию — полка не мешает тестам, которые ищут единственную полку.
const useArtistLabels = vi.fn().mockReturnValue({ data: undefined })

vi.mock("@/api/hooks/useArtist", () => ({
  useArtist: (...args: unknown[]) => useArtist(...args),
  useArtistDiscography: (...args: unknown[]) => useArtistDiscography(...args),
  useArtistSimilar: (...args: unknown[]) => useArtistSimilar(...args),
  useArtistLabels: (...args: unknown[]) => useArtistLabels(...args),
}))

const playSource = vi.fn()
const toggleShuffle = vi.fn()
const toggleArtistLike = vi.fn()

const playerState = { playSource, toggleShuffle }
const navidromeState = { toggleArtistLike, likedArtistIds: new Set<number>() }

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: typeof playerState) => unknown) => selector(playerState),
}))

vi.mock("@/store/navidromeStore", () => ({
  useNavidromeStore: (selector: (state: typeof navidromeState) => unknown) => selector(navidromeState),
}))

vi.mock("@/components/media/PopularTracks", () => ({
  default: () => <div data-testid="popular-tracks" />,
}))

vi.mock("@/components/media/Shelf", () => ({
  default: ({ title, items, moreHref, total, grid }: {
    title: string
    items: Array<{ title: string; subtitle?: string | null }>
    moreHref?: string
    total?: number
    grid?: boolean
  }) => (
    <div data-testid="shelf" data-more-href={moreHref} data-total={total} data-grid={grid ? "true" : undefined}>
      <span>{title}</span>
      {items.map((item) => <span key={item.title} data-subtitle={item.subtitle ?? undefined}>{item.title}</span>)}
    </div>
  ),
}))

vi.mock("@/components/media/ArtworkImage", () => ({
  default: ({ alt }: { alt: string }) => <img alt={alt} />,
}))

function makeArtistData(): ArtistResponse {
  return {
    artist: {
      id: 3,
      name: "Max Cooper",
      sort_name: null,
      image: { url: null, source: "placeholder", placeholder: true },
      library_stats: { tracks: 86, releases: 22, liked_tracks: 0, plays: 0 },
    },
    actions: [],
    links: {},
    top_tracks: [],
  }
}

function makeDiscoData(): ArtistDiscographyResponse {
  return { artist: { id: 3, name: "Max Cooper" }, groups: [] }
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/artists/3"]}>
        <Routes>
          <Route path="/artists/:id" element={<ArtistPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("ArtistPage — кнопка Shuffle", () => {
  beforeEach(() => {
    useArtistLabels.mockReturnValue({ data: undefined })
    playSource.mockReset()
    toggleShuffle.mockReset()
    toggleArtistLike.mockReset()
    useArtist.mockReturnValue({ data: makeArtistData(), isLoading: false, error: null })
    useArtistDiscography.mockReturnValue({ data: makeDiscoData(), isLoading: false })
    useArtistSimilar.mockReturnValue({ data: { artist: { id: 3, name: "Max Cooper" }, items: [], available: true, basis: "artist_similarity" } satisfies ArtistSimilarResponse })
  })

  it("просит сразу перемешанную сессию, а не патчит флаг после старта", async () => {
    // Патч флага задним числом ничего не переупорядочивал: очередь оставалась
    // в исходном порядке, первым играл первый трек, и менялась только подсветка
    // иконки в плеере.
    renderPage()
    await screen.findByText("Max Cooper")

    fireEvent.click(screen.getByRole("button", { name: /shuffle/i }))

    expect(playSource).toHaveBeenCalledWith("artist", 3, "Max Cooper", undefined, { shuffle: true })
    expect(toggleShuffle).not.toHaveBeenCalled()
  })

  it("Play запускает обычное воспроизведение без шафла", () => {
    renderPage()

    fireEvent.click(screen.getByRole("button", { name: "Play" }))

    expect(playSource).toHaveBeenCalledWith("artist", 3, "Max Cooper")
    expect(toggleShuffle).not.toHaveBeenCalled()
  })

  it("показывает Shuffle только иконкой на любой ширине", () => {
    renderPage()

    const shuffle = screen.getByRole("button", { name: "Shuffle" })
    expect(shuffle).toHaveAttribute("data-size", "icon-sm")
    expect(shuffle).not.toHaveTextContent("Shuffle")
  })

  it("показывает жанры артиста с числом его релизов", () => {
    useArtist.mockReturnValue({
      data: { ...makeArtistData(), genres: [{ name: "Techno", release_count: 4 }, { name: "IDM", release_count: 2 }] },
      isLoading: false,
      error: null,
    })

    renderPage()

    const genres = screen.getByRole("list", { name: "Genres" })
    expect(within(genres).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["Techno4", "IDM2"])
  })

  it("рендерит полку похожих артистов внизу страницы", () => {
    useArtistSimilar.mockReturnValue({
      data: {
        artist: { id: 3, name: "Max Cooper" },
        available: true,
        basis: "artist_similarity",
        items: [{
          id: 9,
          name: "Jon Hopkins",
          sort_name: null,
          image: { url: null, source: "none", placeholder: true },
          library_stats: { tracks: 12, releases: 3, liked_tracks: 0, plays: 0 },
        }],
      } satisfies ArtistSimilarResponse,
    })

    renderPage()

    expect(screen.getByTestId("shelf")).toHaveTextContent("Similar artists")
    expect(screen.getByTestId("shelf")).toHaveTextContent("Jon Hopkins")
  })

  it("ведёт полку похожих артистов на их полный список и передаёт его длину", () => {
    useArtistSimilar.mockReturnValue({
      data: {
        artist: { id: 3, name: "Max Cooper" },
        available: true,
        basis: "artist_similarity",
        items: [{
          id: 9,
          name: "Jon Hopkins",
          sort_name: null,
          image: { url: null, source: "none", placeholder: true },
          library_stats: { tracks: 12, releases: 3, liked_tracks: 0, plays: 0 },
        }],
        total: 120,
        limit: 16,
        offset: 0,
        next_offset: 16,
      },
    })

    renderPage()

    expect(screen.getByTestId("shelf")).toHaveAttribute("data-more-href", "/artists/3/similar")
    expect(screen.getByTestId("shelf")).toHaveAttribute("data-total", "120")
  })

  it("показывает лейблы артиста последней полкой страницы", () => {
    useArtistSimilar.mockReturnValue({
      data: {
        artist: { id: 3, name: "Max Cooper" },
        available: true,
        basis: "artist_similarity",
        items: [{
          id: 9,
          name: "Jon Hopkins",
          sort_name: null,
          image: { url: null, source: "none", placeholder: true },
          library_stats: { tracks: 12, releases: 3, liked_tracks: 0, plays: 0 },
        }],
      } satisfies ArtistSimilarResponse,
    })
    useArtistLabels.mockReturnValue({
      data: {
        artist: { id: 3, name: "Max Cooper" },
        total: 30,
        limit: 16,
        offset: 0,
        next_offset: 16,
        items: [
          { id: 7, name: "Mesh", release_count: 9, liked: false,
            artwork: { url: "/api/v1/labels/7/image?v=1", source: "discogs", placeholder: false } },
          { id: 8, name: "Traum Schallplatten", release_count: 2, liked: false,
            artwork: { url: null, source: "placeholder", placeholder: true } },
        ],
      } satisfies ArtistLabelsResponse,
    })

    renderPage()

    expect(useArtistLabels).toHaveBeenCalledWith(3)
    const shelves = screen.getAllByTestId("shelf")
    const last = shelves[shelves.length - 1]
    expect(last).toHaveTextContent("Artist's labels")
    expect(within(last).getByText("Mesh")).toBeInTheDocument()
    expect(within(last).getByText("Traum Schallplatten")).toBeInTheDocument()
    expect(shelves[shelves.length - 2]).toHaveTextContent("Similar artists")
    // Как полка лейблов на главной: слайдер, подпись — только число релизов лейбла,
    // «Ещё» ведёт на полный список лейблов артиста.
    expect(last).not.toHaveAttribute("data-grid")
    expect(within(last).getByText("Mesh")).toHaveAttribute("data-subtitle", "9 releases")
    expect(last).toHaveAttribute("data-more-href", "/artists/3/labels")
    expect(last).toHaveAttribute("data-total", "30")
  })
})

describe("ArtistPage — состояния ошибки", () => {
  beforeEach(() => {
    useArtistDiscography.mockReturnValue({ data: makeDiscoData(), isLoading: false })
    useArtistSimilar.mockReturnValue({ data: { artist: { id: 3, name: "Max Cooper" }, items: [], available: true, basis: "artist_similarity" } satisfies ArtistSimilarResponse })
  })

  it("показывает 'not found' для настоящей 404-ошибки", async () => {
    useArtist.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiError(404, "not_found", "no such artist"),
    })

    renderPage()

    expect(await screen.findByText("Artist not found.")).toBeInTheDocument()
  })

  it("показывает сообщение о переподключении для сетевой ошибки, а не 'not found'", async () => {
    useArtist.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new TypeError("Failed to fetch"),
    })

    renderPage()

    expect(await screen.findByText("Can't reach the server. Retrying automatically…")).toBeInTheDocument()
    expect(screen.queryByText("Artist not found.")).toBeNull()
  })
})
