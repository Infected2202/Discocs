import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import CollectionHeader from "./CollectionHeader"

describe("CollectionHeader", () => {
  it("renders the title as an h1", () => {
    render(<CollectionHeader artwork={<div />} title="Neon Lights" />)
    const heading = screen.getByRole("heading", { level: 1 })
    expect(heading).toHaveTextContent("Neon Lights")
  })

  it("renders the artwork, kicker, meta, and actions slots", () => {
    render(
      <CollectionHeader
        artwork={<img alt="cover" />}
        kicker="Generated mix"
        title="Mix 1"
        meta={<span>12 tracks</span>}
        actions={<button type="button">Play</button>}
      />,
    )
    expect(screen.getByAltText("cover")).toBeInTheDocument()
    expect(screen.getByText("Generated mix")).toBeInTheDocument()
    expect(screen.getByText("12 tracks")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument()
  })

  it("omits kicker, meta, and actions when not provided", () => {
    const { container } = render(<CollectionHeader artwork={<div />} title="Just a title" />)
    // Only the title paragraph/heading — no kicker <p>, no action cluster.
    expect(container.querySelectorAll("p")).toHaveLength(0)
    expect(container.querySelector("button")).toBeNull()
  })

  it("renders the above slot (e.g. a Back button) before the header row", () => {
    render(
      <CollectionHeader
        above={<button type="button">Back</button>}
        artwork={<div />}
        title="Playlist"
      />,
    )
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument()
  })

  it("wraps long titles by default and can explicitly truncate them", () => {
    const { rerender } = render(<CollectionHeader artwork={<div />} title="Long" />)
    const heading = screen.getByRole("heading", { level: 1 })
    expect(heading.className).not.toContain("truncate")
    expect(heading.className).toContain("[overflow-wrap:anywhere]")

    rerender(<CollectionHeader artwork={<div />} title="Long" truncateTitle />)
    expect(screen.getByRole("heading", { level: 1 }).className).toContain("truncate")
  })
})
