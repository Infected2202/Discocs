import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import LabelPage from "./LabelPage"
import { ApiError } from "@/api/client"
import type { LabelReleasesResponse, LabelResponse, ReleaseSummary } from "@/api/types"

const useLabel = vi.fn()
const useLabelReleases = vi.fn()
const toggleLikeMutate = vi.fn()

vi.mock("@/api/hooks/useLabel", () => ({
  useLabel: (...args: unknown[]) => useLabel(...args),
  useLabelReleases: (...args: unknown[]) => useLabelReleases(...args),
  useToggleLabelLike: () => ({ mutate: toggleLikeMutate }),
}))

const playSource = vi.fn()
const playerState = { playSource }

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: typeof playerState) => unknown) => selector(playerState),
}))

vi.mock("@/components/media/Shelf", () => ({
  default: ({ title, items }: { title: string; items: Array<{ title: string; subtitle?: string | null }> }) => (
    <section data-testid="releases" aria-label={title}>
      {items.map((item) => <span key={item.title}>{item.title} — {item.subtitle}</span>)}
    </section>
  ),
}))

vi.mock("@/components/media/ArtworkImage", () => ({
  default: ({ alt, src }: { alt: string; src?: string | null }) => <img alt={alt} src={src ?? undefined} />,
}))

function release(id: number, title: string, year: number | null): ReleaseSummary {
  return {
    id,
    title,
    release_type: "album",
    release_type_label: "Album",
    artists: [{ id: 7, name: "Nina Kraviz" }],
    release_date: year ? `${year}` : null,
    release_year: year,
    track_count: 8,
    duration: null,
    artwork: { url: null, source: "none", placeholder: true },
  }
}

function makeLabel(overrides: Partial<LabelResponse["label"]> = {}): LabelResponse {
  return {
    label: {
      id: 5,
      name: "Trip",
      release_count: 2,
      liked: false,
      artwork: { url: "/api/v1/labels/5/image?v=1", source: "beatport", placeholder: false },
      description: {
        source: "discogs",
        segments: [
          { type: "text", text: "Label of " },
          { type: "artist", text: "Nina Kraviz", artist_id: 7 },
          { type: "text", text: " and " },
          { type: "artist", text: "Somebody Else", artist_id: null },
          { type: "text", text: "." },
        ],
      },
      links: [
        { url: "https://www.trip.bandcamp.com/", title: null },
        { url: "https://soundcloud.com/trip", title: "SoundCloud" },
      ],
      ...overrides,
    },
    links: {},
  }
}

function makeReleases(
  items: ReleaseSummary[],
  groups: LabelReleasesResponse["groups"] = items.length ? [{ key: "albums", items }] : [],
): LabelReleasesResponse {
  return { label: { id: 5, name: "Trip" }, sort: "release_date_desc", groups, items }
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/labels/5"]}>
        <Routes>
          <Route path="/labels/:id" element={<LabelPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("LabelPage", () => {
  beforeEach(() => {
    useLabel.mockReset()
    useLabelReleases.mockReset()
    toggleLikeMutate.mockReset()
    useLabel.mockReturnValue({ data: makeLabel(), isLoading: false, error: null })
    useLabelReleases.mockReturnValue({
      data: makeReleases([release(1, "New One", 2020), release(2, "Old One", 2001)]),
      isLoading: false,
    })
  })

  it("shows the label header with image, release count and its releases newest first", () => {
    renderPage()

    expect(screen.getByRole("heading", { name: "Trip" })).toBeInTheDocument()
    expect(screen.getByAltText("Trip")).toHaveAttribute("src", "/api/v1/labels/5/image?v=1")
    expect(screen.getByText("2 releases")).toBeInTheDocument()
    expect(useLabelReleases).toHaveBeenCalledWith(5, "release_date_desc")
    expect(screen.getByTestId("releases")).toHaveTextContent("New One — Nina Kraviz · 2020")
    expect(screen.getByTestId("releases")).toHaveTextContent("Old One — Nina Kraviz · 2001")
  })

  it("switches release sorting to oldest first", () => {
    renderPage()

    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), {
      target: { value: "release_date_asc" },
    })

    expect(useLabelReleases).toHaveBeenLastCalledWith(5, "release_date_asc")
  })

  it("links artists known to the library and leaves unknown ones as plain text", () => {
    renderPage()

    const description = screen.getByTestId("label-description")
    expect(description).toHaveTextContent("Label of Nina Kraviz and Somebody Else.")
    expect(screen.getByRole("link", { name: "Nina Kraviz" })).toHaveAttribute("href", "/artists/7")
    expect(screen.queryByRole("link", { name: "Somebody Else" })).not.toBeInTheDocument()
    expect(screen.getByText("Source: Discogs")).toBeInTheDocument()
  })

  it("renders label links in a new tab, titled by host when no title is given", () => {
    renderPage()

    const bandcamp = screen.getByRole("link", { name: "trip.bandcamp.com" })
    expect(bandcamp).toHaveAttribute("href", "https://www.trip.bandcamp.com/")
    expect(bandcamp).toHaveAttribute("target", "_blank")
    expect(bandcamp).toHaveAttribute("rel", "noopener noreferrer")
    expect(screen.getByRole("link", { name: "SoundCloud" })).toBeInTheDocument()
  })

  it("omits the description block when the label has no text and no links", () => {
    useLabel.mockReturnValue({
      data: makeLabel({ description: null, links: [] }),
      isLoading: false,
      error: null,
    })

    renderPage()

    expect(screen.queryByTestId("label-description")).not.toBeInTheDocument()
    expect(screen.queryByText(/Source:/)).not.toBeInTheDocument()
  })

  it("says so when the label has no releases left in the library", () => {
    useLabelReleases.mockReturnValue({ data: makeReleases([]), isLoading: false })

    renderPage()

    expect(screen.getByText("This label has no releases in the library.")).toBeInTheDocument()
    expect(screen.queryByTestId("releases")).not.toBeInTheDocument()
  })

  it("splits releases into release-type groups in the order the API returns them", () => {
    const ep = { ...release(3, "Some EP", 2019), release_type: "ep", release_type_label: "EP" }
    const single = { ...release(4, "A Single", 2018), release_type: "single", release_type_label: "Single" }
    useLabelReleases.mockReturnValue({
      data: makeReleases([], [
        { key: "albums", items: [release(1, "New One", 2020)] },
        { key: "eps", items: [ep] },
        { key: "singles", items: [single] },
        { key: "releases", items: [release(5, "Soundtrack", 2010)] },
      ]),
      isLoading: false,
    })

    renderPage()

    const shelves = screen.getAllByTestId("releases")
    expect(shelves.map((shelf) => shelf.getAttribute("aria-label"))).toEqual([
      "Albums", "EPs", "Singles", "Other releases",
    ])
    // Над группами нет общего заголовка «Releases» — он выглядел пустой группой.
    expect(screen.queryByRole("heading", { name: "Releases" })).not.toBeInTheDocument()
    expect(shelves[1]).toHaveTextContent("Some EP")
    expect(shelves[1]).not.toHaveTextContent("New One")
  })

  it("likes a label that is not liked yet", () => {
    renderPage()

    fireEvent.click(screen.getByRole("button", { name: "Add to favourites" }))

    expect(toggleLikeMutate).toHaveBeenCalledWith(true)
  })

  it("unlikes an already liked label", () => {
    useLabel.mockReturnValue({ data: makeLabel({ liked: true }), isLoading: false, error: null })

    renderPage()

    const button = screen.getByRole("button", { name: "Add to favourites" })
    expect(button).toHaveClass("text-primary")
    fireEvent.click(button)

    expect(toggleLikeMutate).toHaveBeenCalledWith(false)
  })

  it("shows not found for a missing label", () => {
    useLabel.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiError(404, "not_found", "Label not found"),
    })

    renderPage()

    expect(screen.getByText("Label not found.")).toBeInTheDocument()
  })
})
