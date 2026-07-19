import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import CreateShareDialog from "./CreateShareDialog"

const createShare = vi.fn()

vi.mock("@/api/shares", () => ({
  createShare: (...args: unknown[]) => createShare(...args),
}))

describe("CreateShareDialog", () => {
  it("creates a seven-day release link by default and shows the secret once", async () => {
    createShare.mockResolvedValueOnce({ share: { id: "s1" }, url: "https://music.example/share/secret" })
    render(
      <CreateShareDialog
        open
        onOpenChange={vi.fn()}
        sourceType="release"
        sourceId={42}
        sourceTitle="Album"
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "Create link" }))

    await waitFor(() => expect(createShare).toHaveBeenCalledWith(expect.objectContaining({
      source_type: "release",
      source_id: 42,
      expires_at: expect.any(String),
    })))
    expect(await screen.findByDisplayValue("https://music.example/share/secret")).toBeInTheDocument()
    expect(screen.getByText(/shown only now/i)).toBeInTheDocument()
  })

  it("requires explicit confirmation for a non-expiring link", () => {
    render(
      <CreateShareDialog
        open
        onOpenChange={vi.fn()}
        sourceType="track"
        sourceId={7}
        sourceTitle="Track"
      />,
    )

    fireEvent.change(screen.getByLabelText("Expires"), { target: { value: "never" } })

    expect(screen.getByRole("button", { name: "Create link" })).toBeDisabled()
    fireEvent.click(screen.getByRole("checkbox"))
    expect(screen.getByRole("button", { name: "Create link" })).toBeEnabled()
  })
})
