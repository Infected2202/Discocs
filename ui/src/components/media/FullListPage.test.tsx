import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import FullListPage from "./FullListPage"
import { usePagedList } from "@/api/hooks/usePagedList"
import type { PagedList } from "@/lib/shelves"

const navigate = vi.fn()

vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router")>()),
  useNavigate: () => navigate,
}))

// jsdom has no layout: render every item of the grid directly.
vi.mock("./VirtualCardGrid", () => ({
  default: ({ items, getKey, renderItem }: {
    items: unknown[]
    getKey: (item: unknown, index: number) => string
    renderItem: (item: unknown, index: number) => ReactNode
  }) => <div>{items.map((item, index) => <div key={getKey(item, index)}>{renderItem(item, index)}</div>)}</div>,
}))

vi.mock("./MediaCard", () => ({
  default: ({ title }: { title: string }) => <div data-testid="card">{title}</div>,
}))

// jsdom has no IntersectionObserver: keep the callback to fire it by hand.
let intersect: (() => void) | null = null
class IntersectionObserverStub {
  private readonly callback: IntersectionObserverCallback
  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback
  }
  observe() {
    intersect = () => this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this as never)
  }
  disconnect() {}
  unobserve() {}
}
vi.stubGlobal("IntersectionObserver", IntersectionObserverStub)

interface Item {
  id: number
  name: string
}

const fetchPage = vi.fn<(page: { limit: number; offset: number }) => Promise<PagedList<Item>>>()

function List() {
  const source = usePagedList(["test-list"], fetchPage, { pageSize: 2 })
  return (
    <FullListPage<Item>
      title="Similar artists"
      subtitle="Max Cooper"
      source={source}
      getKey={(item) => String(item.id)}
      toCard={(item) => ({ id: item.id, type: "artist", title: item.name })}
    />
  )
}

function renderList() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <List />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("FullListPage", () => {
  beforeEach(() => {
    fetchPage.mockReset()
    navigate.mockReset()
    intersect = null
  })

  it("shows the first page with title, subtitle and the total", async () => {
    fetchPage.mockResolvedValueOnce({ items: [{ id: 1, name: "One" }, { id: 2, name: "Two" }], total: 3, next_offset: 2 })
    renderList()

    expect(await screen.findByText("One")).toBeInTheDocument()
    expect(screen.getByRole("heading", { level: 1, name: "Similar artists" })).toBeInTheDocument()
    expect(screen.getByText("Max Cooper")).toBeInTheDocument()
    expect(screen.getByText("3 items")).toBeInTheDocument()
    expect(fetchPage).toHaveBeenCalledWith({ limit: 2, offset: 0 })
  })

  it("loads the next page when the end of the list scrolls into view, and stops after the last", async () => {
    fetchPage
      .mockResolvedValueOnce({ items: [{ id: 1, name: "One" }, { id: 2, name: "Two" }], total: 3, next_offset: 2 })
      .mockResolvedValueOnce({ items: [{ id: 3, name: "Three" }], total: 3, next_offset: null })
    renderList()
    await screen.findByText("Two")

    await vi.waitFor(() => expect(intersect).not.toBeNull())
    intersect?.()

    expect(await screen.findByText("Three")).toBeInTheDocument()
    expect(fetchPage).toHaveBeenNthCalledWith(2, { limit: 2, offset: 2 })
    expect(screen.getAllByTestId("card").map((card) => card.textContent)).toEqual(["One", "Two", "Three"])

    intersect?.()
    await vi.waitFor(() => expect(screen.getAllByTestId("card")).toHaveLength(3))
    expect(fetchPage).toHaveBeenCalledTimes(2)
  })

  it("goes back like the dashboard shelf page", async () => {
    fetchPage.mockResolvedValueOnce({ items: [], total: 0, next_offset: null })
    renderList()

    fireEvent.click(screen.getByRole("button", { name: "Back" }))

    expect(navigate).toHaveBeenCalledWith(-1)
  })
})
