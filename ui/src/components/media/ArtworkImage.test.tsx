import { fireEvent, render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import ArtworkImage from "./ArtworkImage"

describe("ArtworkImage", () => {
  it("renders a plain image when not expandable", () => {
    render(<ArtworkImage src="/art/1.jpg" alt="Cover" />)
    expect(screen.getByRole("img", { name: "Cover" })).toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("renders the fallback letter when there is no src, even if expandable", () => {
    render(<ArtworkImage src={null} alt="Some Artist" expandable />)
    expect(screen.getByText("S")).toBeInTheDocument()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("opens a full-size modal on click when expandable, without stretching the image", async () => {
    render(<ArtworkImage src="/art/1.jpg" alt="Big Cover" size={144} expandable />)

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: /view full size/i }))

    const dialog = await screen.findByRole("dialog")
    const fullImage = screen.getByRole("img", { name: "Big Cover" })
    expect(dialog).toContainElement(fullImage)
    // Fit within the viewport, but never forced to a fixed/stretched size.
    expect(fullImage).toHaveClass("object-contain")
    expect(fullImage.getAttribute("style") ?? "").not.toMatch(/width/)
  })

  it("closes the modal when dismissed", async () => {
    render(<ArtworkImage src="/art/1.jpg" alt="Big Cover" expandable />)
    fireEvent.click(screen.getByRole("button", { name: /view full size/i }))
    await screen.findByRole("dialog")

    fireEvent.click(screen.getByRole("button", { name: /close/i }))

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})
