import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { afterEach, describe, expect, it, vi } from "vitest"
import SearchPage from "./SearchPage"
import type { ArtistSummary, LabelSummary, ReleaseSummary, SearchResponse } from "@/api/types"

const fetchSearch = vi.fn()

vi.mock("@/api/search", () => ({
  fetchSearch: (...args: unknown[]) => fetchSearch(...args),
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (s: { playSource: () => void }) => unknown) => selector({ playSource: vi.fn() }),
}))

function artist(id: number): ArtistSummary {
  return { id, name: `Artist ${id}`, sort_name: null, image: { url: null, source: "placeholder", placeholder: true }, library_stats: {} } as unknown as ArtistSummary
}

function release(id: number): ReleaseSummary {
  return {
    id,
    title: `Release ${id}`,
    release_type: "album",
    release_type_label: "Album",
    artists: [],
    release_date: null,
    release_year: null,
    track_count: 1,
    duration: null,
    artwork: { url: null, source: "placeholder", placeholder: true },
  } as unknown as ReleaseSummary
}

function label(id: number): LabelSummary {
  return {
    id,
    name: `Label ${id}`,
    release_count: id,
    liked: false,
    artwork: { url: null, source: "placeholder", placeholder: true },
  } as unknown as LabelSummary
}

const ARTISTS = [artist(1), artist(2)]
// 49 releases match the query, but the "all"-tab overview only ever gets a
// 12-item preview from the backend — release 49 only exists in the paginated
// releases-tab response, so its presence proves the tab fetched everything
// instead of reusing the capped preview list.
const RELEASES_PREVIEW = Array.from({ length: 10 }, (_, i) => release(i + 1))
const RELEASES_FULL = Array.from({ length: 49 }, (_, i) => release(i + 1))

function emptyGroup(type: string, title: string) {
  return { type, title, items: [], total: 0, next_offset: null }
}

function mockFetchSearch(_query: string, type: string, limit: number, offset: number): Promise<SearchResponse> {
  if (type === "all") {
    return Promise.resolve({
      query: "fabric",
      top_result: null,
      groups: [
        { type: "artists", title: "Artists", items: ARTISTS, total: 2, next_offset: null },
        emptyGroup("tracks", "Tracks"),
        { type: "releases", title: "Releases", items: RELEASES_PREVIEW, total: 49, next_offset: 12 },
      ],
    } as unknown as SearchResponse)
  }
  if (type === "release") {
    return Promise.resolve({
      query: "fabric",
      top_result: null,
      groups: [
        emptyGroup("artists", "Artists"),
        emptyGroup("tracks", "Tracks"),
        {
          type: "releases",
          title: "Releases",
          items: RELEASES_FULL,
          total: 49,
          next_offset: offset + limit < 49 ? offset + limit : null,
        },
      ],
    } as unknown as SearchResponse)
  }
  return Promise.resolve({
    query: "fabric",
    top_result: null,
    groups: [emptyGroup("artists", "Artists"), emptyGroup("tracks", "Tracks"), emptyGroup("releases", "Releases")],
  } as unknown as SearchResponse)
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/search?q=fabric"]}>
        <Routes>
          <Route path="/search" element={<SearchPage />} />
          <Route path="/labels/:id" element={<p>label page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("SearchPage", () => {
  afterEach(() => {
    fetchSearch.mockReset()
  })

  it("shows the true total on the tab, not the capped preview length", async () => {
    fetchSearch.mockImplementation(mockFetchSearch)

    renderPage()

    expect(await screen.findByRole("tab", { name: "Releases (49)" })).toBeInTheDocument()
    // The "All" tab overview is a capped preview — release 49 isn't in it.
    expect(screen.getByText("Release 1")).toBeInTheDocument()
    expect(screen.queryByText("Release 49")).toBeNull()
  })

  it("the Releases tab fetches and shows every match, not just the all-tab preview", async () => {
    fetchSearch.mockImplementation(mockFetchSearch)

    renderPage()
    await screen.findByRole("tab", { name: "Releases (49)" })

    // Radix's TabsTrigger activates on mousedown, not click — see
    // @radix-ui/react-tabs's Trigger implementation.
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Releases (49)" }), { button: 0 })

    await waitFor(() => expect(fetchSearch).toHaveBeenCalledWith("fabric", "release", 50, 0))
    // Proves the tab loaded the full paginated result, not the 10-item preview.
    expect(await screen.findByText("Release 49")).toBeInTheDocument()
    expect(screen.getByText("Release 1")).toBeInTheDocument()
  })

  it("'Show all' under the All tab jumps straight to the full Releases tab", async () => {
    fetchSearch.mockImplementation(mockFetchSearch)

    renderPage()
    await screen.findByRole("tab", { name: "Releases (49)" })

    fireEvent.click(screen.getByRole("button", { name: /show all/i }))

    // Split the wait the same way the test above does. One click has to
    // activate the tab, fire a fresh paginated request and render 49 rows;
    // folding all of that into a single findBy* makes the render share one
    // budget with the request — and findBy*'s budget is 1s regardless of
    // testTimeout, which ran out under the full parallel run in build #390.
    await waitFor(() => expect(fetchSearch).toHaveBeenCalledWith("fabric", "release", 50, 0))
    expect(await screen.findByText("Release 49")).toBeInTheDocument()
  })

  it("finds labels: a card with the release count opens the label, the Labels tab pages through all of them", async () => {
    const labelsGroup = (items: LabelSummary[], nextOffset: number | null) => ({
      type: "labels", title: "Labels", items, total: 8, next_offset: nextOffset,
    })
    fetchSearch.mockImplementation((_query: string, type: string) => Promise.resolve({
      query: "trip",
      top_result: null,
      groups: [
        emptyGroup("artists", "Artists"),
        emptyGroup("tracks", "Tracks"),
        emptyGroup("releases", "Releases"),
        type === "label"
          ? labelsGroup(Array.from({ length: 8 }, (_, i) => label(i + 1)), null)
          : labelsGroup([label(1), label(2)], 12),
      ],
    } as unknown as SearchResponse))

    renderPage()

    // Only labels matched — the page shows results instead of «No results».
    const tab = await screen.findByRole("tab", { name: "Labels (8)" })
    expect(screen.getByText("2 releases")).toBeInTheDocument()
    expect(screen.queryByText("Label 8")).toBeNull()

    fireEvent.mouseDown(tab, { button: 0 })
    await waitFor(() => expect(fetchSearch).toHaveBeenCalledWith("fabric", "label", 50, 0))
    expect(await screen.findByText("Label 8")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Label 8"))
    expect(await screen.findByText("label page")).toBeInTheDocument()
  })
})
