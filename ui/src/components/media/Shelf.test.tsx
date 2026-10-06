import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import Shelf, { mobileCardFlex } from "./Shelf"

const navigate = vi.fn()

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

let columns = 4
let isMobile = false

vi.mock("@/hooks/useColumns", () => ({
  useColumns: () => ({ cols: columns, isMobile }),
}))

vi.mock("@/lib/animateScroll", () => ({
  animateScroll: (
    _el: Element,
    _from: number,
    _to: number,
    _duration: number,
    _axis: string,
    onDone: () => void
  ) => onDone(),
}))

vi.mock("./MediaCard", () => ({
  default: ({ title }: { title: string }) => <div>{title}</div>,
}))

describe("Shelf", () => {
  beforeEach(() => {
    vi.useRealTimers()
    columns = 4
    isMobile = false
    navigate.mockReset()
  })

  it("keeps native touch momentum while gently snapping mobile shelf cards", () => {
    columns = 2
    isMobile = true
    render(
      <MemoryRouter>
        <Shelf
          items={[
            { id: 1, type: "release", title: "One" },
            { id: 2, type: "release", title: "Two" },
            { id: 3, type: "release", title: "Three" },
          ]}
        />
      </MemoryRouter>
    )

    const cardWrapper = screen.getByText("One").parentElement
    const scroller = cardWrapper?.parentElement
    expect(scroller).toHaveClass("overflow-x-auto")
    expect(scroller).toHaveClass("no-scrollbar")
    expect(scroller).toHaveAttribute(
      "style",
      expect.stringContaining("scroll-snap-type: x proximity")
    )
    // Three cards: two full ones plus a quarter of the next peeking out.
    // Compared through a probe element so jsdom's calc() normalization applies
    // to both sides.
    const probe = document.createElement("div")
    probe.style.flex = mobileCardFlex(3)
    expect(probe.style.flex).not.toBe("")
    expect(cardWrapper?.style.flex).toBe(probe.style.flex)
  })

  it("sizes mobile cards so a quarter of the third peeks out, and plain halves when there is nothing to scroll", () => {
    expect(mobileCardFlex(3)).toBe("0 0 calc((100% - 4px) / 2.25)")
    expect(mobileCardFlex(8)).toBe(mobileCardFlex(3))
    expect(mobileCardFlex(2)).toBe("0 0 calc((100% - 8px) / 2)")
    expect(mobileCardFlex(1)).toBe(mobileCardFlex(2))
  })

  it("smoothly aligns to the nearest card after native momentum settles", () => {
    vi.useFakeTimers()
    isMobile = true
    render(
      <MemoryRouter>
        <Shelf
          items={[
            { id: 1, type: "release", title: "One" },
            { id: 2, type: "release", title: "Two" },
            { id: 3, type: "release", title: "Three" },
          ]}
        />
      </MemoryRouter>
    )

    const scroller = screen.getByText("One").parentElement?.parentElement as HTMLDivElement
    const cardWrappers = ["One", "Two", "Three"].map(
      (title) => screen.getByText(title).parentElement as HTMLDivElement
    )
    const scrollTo = vi.fn()
    Object.defineProperties(scroller, {
      scrollLeft: { configurable: true, value: 130, writable: true },
      scrollWidth: { configurable: true, value: 1000 },
      clientWidth: { configurable: true, value: 400 },
      scrollTo: { configurable: true, value: scrollTo },
    })
    Object.defineProperty(cardWrappers[0], "offsetLeft", { configurable: true, value: 12 })
    Object.defineProperty(cardWrappers[1], "offsetLeft", { configurable: true, value: 220 })
    Object.defineProperty(cardWrappers[2], "offsetLeft", { configurable: true, value: 428 })

    fireEvent.touchStart(scroller)
    expect(scroller.style.scrollSnapType).toBe("none")

    fireEvent.scroll(scroller)
    vi.advanceTimersByTime(200)
    expect(scroller.style.scrollSnapType).toBe("none")

    fireEvent.touchEnd(scroller)
    vi.advanceTimersByTime(100)
    expect(scroller.style.scrollSnapType).toBe("none")

    // Native momentum keeps emitting scroll events, so each one postpones
    // grid alignment until the shelf has actually stopped moving.
    fireEvent.scroll(scroller)
    vi.advanceTimersByTime(139)
    expect(scroller.style.scrollSnapType).toBe("none")

    vi.advanceTimersByTime(1)
    expect(scroller.style.scrollSnapType).toBe("none")
    expect(scrollTo).toHaveBeenCalledWith({ left: 208, behavior: "smooth" })

    // The smooth adjustment itself emits scroll events. Snap is restored
    // only after that short animation has also become idle.
    fireEvent.scroll(scroller)
    vi.advanceTimersByTime(99)
    expect(scroller.style.scrollSnapType).toBe("none")

    vi.advanceTimersByTime(1)
    expect(scroller.style.scrollSnapType).toBe("x proximity")
    vi.useRealTimers()
  })

  it("renders shelf navigation as native buttons and disables previous on first page", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="History"
          shelfKey="history"
          total={40}
          items={[
            { id: 1, type: "release", title: "One" },
            { id: 2, type: "release", title: "Two" },
            { id: 3, type: "release", title: "Three" },
            { id: 4, type: "release", title: "Four" },
            { id: 5, type: "release", title: "Five" },
          ]}
        />
      </MemoryRouter>
    )

    expect(screen.getByRole("button", { name: "History" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "More" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled()
  })

  it("renders an accent-colored divider stretching from the title to the header controls", () => {
    const { container } = render(
      <MemoryRouter>
        <Shelf
          title="Albums"
          shelfKey="albums_for_you"
          items={[{ id: 1, type: "release", title: "One" }]}
        />
      </MemoryRouter>
    )

    const divider = container.querySelector('div[aria-hidden="true"]')
    expect(divider).toBeInTheDocument()
    expect(divider).toHaveClass("flex-1")
    expect(divider?.className).toMatch(/bg-primary\/50/)
  })

  it("omits the divider on a titleless shelf (e.g. the For You row) but still right-aligns its arrows", () => {
    const { container } = render(
      <MemoryRouter>
        <Shelf
          items={Array.from({ length: 9 }, (_, i) => ({
            id: i + 1,
            type: "release" as const,
            title: `Item ${i + 1}`,
          }))}
        />
      </MemoryRouter>
    )

    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument()
    expect(container.querySelector('div[aria-hidden="true"]')).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next" }).parentElement).toHaveClass("ml-auto")
  })

  it("renders every item as a static multi-row grid without pagination when grid is set", () => {
    columns = 4
    const items = Array.from({ length: 12 }, (_, i) => ({
      id: i + 1,
      type: "release" as const,
      title: `Album ${i + 1}`,
    }))

    render(
      <MemoryRouter>
        <Shelf title="Albums" grid items={items} />
      </MemoryRouter>
    )

    // All 12 cards are present, not sliced to cols * 2 (= 8) like the slider.
    for (const item of items) {
      expect(screen.getByText(item.title)).toBeInTheDocument()
    }
    // No horizontal pagination in grid mode.
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Previous" })).not.toBeInTheDocument()
  })

  it("navigates to shelf page from title and more button", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="History"
          shelfKey="history"
          total={3}
          items={[
            { id: 1, type: "release", title: "One" },
            { id: 2, type: "release", title: "Two" },
          ]}
        />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole("button", { name: "History" }))
    fireEvent.click(screen.getByRole("button", { name: "More" }))

    expect(navigate).toHaveBeenNthCalledWith(1, "/shelf/history")
    expect(navigate).toHaveBeenNthCalledWith(2, "/shelf/history")
  })

  it("hides «More» and the title link when the shelf shows the whole list", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="History"
          shelfKey="history"
          total={2}
          items={[
            { id: 1, type: "release", title: "One" },
            { id: 2, type: "release", title: "Two" },
          ]}
        />
      </MemoryRouter>
    )

    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "History" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "History" })).toBeInTheDocument()
  })

  it("links «More» to the given full-list href when the source has more than the preview", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="Top artists"
          moreHref="/u/alice/top/artists?period=7d"
          total={30}
          items={[{ id: 1, type: "artist", title: "Alpha" }]}
        />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole("button", { name: "More" }))
    fireEvent.click(screen.getByRole("button", { name: "Top artists" }))

    expect(navigate).toHaveBeenNthCalledWith(1, "/u/alice/top/artists?period=7d")
    expect(navigate).toHaveBeenNthCalledWith(2, "/u/alice/top/artists?period=7d")
  })

  it("offers «More» without a total when the loaded items do not fit the slider", () => {
    columns = 4
    const items = Array.from({ length: 9 }, (_, i) => ({
      id: i + 1,
      type: "release" as const,
      title: `Album ${i + 1}`,
    }))
    const { unmount } = render(
      <MemoryRouter>
        <Shelf title="Similar" moreHref="/artists/1/similar" items={items} />
      </MemoryRouter>
    )

    // 4 columns × 2 pages = 8 cards on the slider, the 9th only on the full list.
    expect(screen.queryByText("Album 9")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "More" })).toBeInTheDocument()
    unmount()

    render(
      <MemoryRouter>
        <Shelf title="Similar" moreHref="/artists/1/similar" items={items.slice(0, 8)} />
      </MemoryRouter>
    )
    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument()
  })

  it("shows «More» on mobile only when the source has more than was loaded", () => {
    isMobile = true
    columns = 2
    const items = Array.from({ length: 5 }, (_, i) => ({
      id: i + 1,
      type: "release" as const,
      title: `Album ${i + 1}`,
    }))
    const { unmount } = render(
      <MemoryRouter>
        <Shelf title="Likes" moreHref="/u/alice/likes/releases" total={5} items={items} />
      </MemoryRouter>
    )
    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument()
    unmount()

    render(
      <MemoryRouter>
        <Shelf title="Likes" moreHref="/u/alice/likes/releases" total={6} items={items} />
      </MemoryRouter>
    )
    expect(screen.getByRole("button", { name: "More" })).toBeInTheDocument()
  })

  it("unfolds a shelf without a full-list page in place instead of hiding cards", () => {
    columns = 4
    const items = Array.from({ length: 9 }, (_, i) => ({
      id: i + 1,
      type: "user" as const,
      title: `Person ${i + 1}`,
    }))
    render(
      <MemoryRouter>
        <Shelf title="People" items={items} />
      </MemoryRouter>
    )

    expect(screen.queryByText("Person 9")).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "More" }))

    expect(navigate).not.toHaveBeenCalled()
    expect(screen.getByText("Person 9")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Less" }))
    expect(screen.queryByText("Person 9")).not.toBeInTheDocument()
  })

  it("does not offer to unfold a shelf that already shows every card", () => {
    columns = 4
    render(
      <MemoryRouter>
        <Shelf title="People" items={[{ id: 1, type: "user", title: "Solo" }]} />
      </MemoryRouter>
    )
    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument()
  })

  it("never offers «More» on a grid shelf, which already shows everything", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="Albums"
          grid
          moreHref="/somewhere"
          total={50}
          items={[{ id: 1, type: "release", title: "One" }]}
        />
      </MemoryRouter>
    )

    expect(screen.queryByRole("button", { name: "More" })).not.toBeInTheDocument()
  })
})
