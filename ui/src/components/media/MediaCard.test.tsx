import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import MediaCard from "./MediaCard"

const navigate = vi.fn()

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

describe("MediaCard", () => {
  beforeEach(() => navigate.mockReset())

  it("navigates exactly once for Enter and Space keyboard activation", () => {
    const { container } = render(
      <MemoryRouter>
        <MediaCard id={42} type="release" title="Selected Release" />
      </MemoryRouter>
    )

    const card = container.querySelector('[role="button"]')
    expect(card).not.toBeNull()

    fireEvent.keyDown(card!, { key: "Enter" })
    fireEvent.keyDown(card!, { key: " " })

    expect(navigate).toHaveBeenNthCalledWith(1, "/releases/42")
    expect(navigate).toHaveBeenNthCalledWith(2, "/releases/42")
    expect(navigate).toHaveBeenCalledTimes(2)
  })

  it("does not trigger parent navigation when play button is clicked", () => {
    const onPlay = vi.fn()

    render(
      <MemoryRouter>
        <MediaCard id={42} type="release" title="Selected Release" onPlay={onPlay} />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole("button", { name: "Play Selected Release" }))

    expect(onPlay).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
  })

  it("navigates to subtitle link without triggering parent navigation", () => {
    render(
      <MemoryRouter>
        <MediaCard
          id={42}
          type="release"
          title="Selected Release"
          subtitleLinks={[{ href: "/artists/7", label: "Artist Seven" }]}
        />
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole("button", { name: "Artist Seven" }))

    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith("/artists/7")
  })
})
