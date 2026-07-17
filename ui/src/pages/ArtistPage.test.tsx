import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ArtistPage from "./ArtistPage"
import type { ArtistResponse, ArtistDiscographyResponse } from "@/api/types"

const useArtist = vi.fn()
const useArtistDiscography = vi.fn()

vi.mock("@/api/hooks/useArtist", () => ({
  useArtist: (...args: unknown[]) => useArtist(...args),
  useArtistDiscography: (...args: unknown[]) => useArtistDiscography(...args),
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
  default: () => <div data-testid="shelf" />,
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
})
