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

vi.mock("@/hooks/useColumns", () => ({
  useColumns: () => 4,
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
  beforeEach(() => navigate.mockReset())

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

    const divider = container.querySelector('[aria-hidden="true"]')
    expect(divider).toBeInTheDocument()
    expect(divider).toHaveClass("flex-1")
    expect(divider?.className).toMatch(/from-primary/)
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
