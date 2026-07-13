import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import Shelf from "./Shelf"

const navigate = vi.fn()

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

let columns = 4

vi.mock("@/hooks/useColumns", () => ({
  useColumns: () => columns,
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
    columns = 4
    navigate.mockReset()
  })

  it("keeps native touch momentum while gently snapping mobile shelf cards", () => {
    columns = 2
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

    const scroller = screen.getByText("One").parentElement?.parentElement
    expect(scroller).toHaveClass("overflow-x-auto")
    expect(scroller).toHaveAttribute(
      "style",
      expect.stringContaining("scroll-snap-type: x proximity")
    )
  })

  it("renders shelf navigation as native buttons and disables previous on first page", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="History"
          shelfKey="history"
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

  it("navigates to shelf page from title and more button", () => {
    render(
      <MemoryRouter>
        <Shelf
          title="History"
          shelfKey="history"
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
})
