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

  describe("user card", () => {
    const avatar = { url: "/avatars/a02.webp", source: "local", placeholder: false }

    it("shows a round avatar, no play button, and opens the user's profile", () => {
      const onPlay = vi.fn()
      render(
        <MemoryRouter>
          <MediaCard id="bob smith" type="user" title="bob smith" artwork={avatar} onPlay={onPlay} />
        </MemoryRouter>
      )

      const img = screen.getByRole("img", { name: "bob smith" })
      expect(img).toHaveAttribute("src", "/avatars/a02.webp")
      expect(img).toHaveClass("rounded-full")
      expect(screen.queryByRole("button", { name: /^Play / })).not.toBeInTheDocument()

      fireEvent.click(screen.getByText("bob smith"))
      expect(navigate).toHaveBeenCalledWith("/u/bob%20smith")
    })

    it("shows the second line with a live dot while playing", () => {
      render(
        <MemoryRouter>
          <MediaCard id="bob" type="user" title="bob" artwork={avatar} subtitle="Signals — Alpha" live />
        </MemoryRouter>
      )

      expect(screen.getByText("Signals — Alpha")).toBeInTheDocument()
      expect(screen.getByTestId("live-dot")).toHaveTextContent("Playing now")
    })

    it("shows only the login when not playing", () => {
      const { container } = render(
        <MemoryRouter>
          <MediaCard id="bob" type="user" title="bob" artwork={avatar} />
        </MemoryRouter>
      )

      expect(screen.queryByTestId("live-dot")).not.toBeInTheDocument()
      expect(container.querySelectorAll("p")).toHaveLength(1)
    })
  })

  it("appends meta to the subtitle line", () => {
    render(
      <MemoryRouter>
        <MediaCard
          id={42}
          type="track"
          title="Selected Track"
          subtitleLinks={[{ href: "/artists/7", label: "Artist Seven" }]}
          meta="3 hr. ago"
        />
      </MemoryRouter>
    )

    const artistLink = screen.getByRole("button", { name: "Artist Seven" })
    expect(artistLink.closest("p")).toHaveTextContent("Artist Seven · 3 hr. ago")
    expect(screen.queryByTestId("live-dot")).not.toBeInTheDocument()
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
