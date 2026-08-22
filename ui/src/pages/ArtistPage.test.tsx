import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ArtistPage from "./ArtistPage"
import { ApiError } from "@/api/client"
import type { ArtistResponse, ArtistDiscographyResponse, ArtistSimilarResponse } from "@/api/types"

const useArtist = vi.fn()
const useArtistDiscography = vi.fn()
const useArtistSimilar = vi.fn()

vi.mock("@/api/hooks/useArtist", () => ({
  useArtist: (...args: unknown[]) => useArtist(...args),
  useArtistDiscography: (...args: unknown[]) => useArtistDiscography(...args),
  useArtistSimilar: (...args: unknown[]) => useArtistSimilar(...args),
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
  default: ({ title, items }: { title: string; items: Array<{ title: string }> }) => (
    <div data-testid="shelf"><span>{title}</span>{items.map((item) => <span key={item.title}>{item.title}</span>)}</div>
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
    playSource.mockReset()
    toggleShuffle.mockReset()
    toggleArtistLike.mockReset()
    useArtist.mockReturnValue({ data: makeArtistData(), isLoading: false, error: null })
    useArtistDiscography.mockReturnValue({ data: makeDiscoData(), isLoading: false })
    useArtistSimilar.mockReturnValue({ data: { artist: { id: 3, name: "Max Cooper" }, items: [], available: true, basis: "artist_similarity" } satisfies ArtistSimilarResponse })
  })

  it("запускает воспроизведение артиста и включает шафл", async () => {
    renderPage()
    await screen.findByText("Max Cooper")

    fireEvent.click(screen.getByRole("button", { name: /shuffle/i }))

    expect(playSource).toHaveBeenCalledWith("artist", 3, "Max Cooper")
    await waitFor(() => expect(toggleShuffle).toHaveBeenCalledTimes(1))
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
