import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import CreateShareDialog from "./CreateShareDialog"

const createShare = vi.fn()
const writeText = vi.fn()

vi.mock("@/api/shares", () => ({
  createShare: (...args: unknown[]) => createShare(...args),
}))

function renderDialog() {
  return render(
    <CreateShareDialog
      open
      onOpenChange={vi.fn()}
      sourceType="release"
      sourceId={42}
      sourceTitle="Album"
    />,
  )
}

describe("CreateShareDialog", () => {
  beforeEach(() => {
    createShare.mockReset()
    writeText.mockReset().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    })
  })

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

  it("opens without a caret in the label so no keyboard covers the dialog", () => {
    // The field only offers to override a title that already defaults to the
    // source, and on a phone the keyboard it summons hides half the dialog.
    renderDialog()

    expect(screen.getByLabelText("Link label (optional)")).not.toHaveFocus()
    expect(document.activeElement?.tagName).not.toBe("INPUT")
  })

  it("copies the new link without waiting to be asked", async () => {
    createShare.mockResolvedValueOnce({ share: { id: "s1" }, url: "https://music.example/share/secret" })
    renderDialog()

    fireEvent.click(screen.getByRole("button", { name: "Create link" }))

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("https://music.example/share/secret"))
    expect(await screen.findByText("Copied to clipboard")).toBeInTheDocument()
  })

  it("still shows the link when the clipboard refuses the write", async () => {
    // Insecure origins, denied permissions and stale user activation all
    // reject. None of them may cost the user the one showing of the secret.
    writeText.mockRejectedValueOnce(new Error("denied"))
    createShare.mockResolvedValueOnce({ share: { id: "s1" }, url: "https://music.example/share/secret" })
    renderDialog()

    fireEvent.click(screen.getByRole("button", { name: "Create link" }))

    expect(await screen.findByDisplayValue("https://music.example/share/secret")).toBeInTheDocument()
    expect(screen.queryByText("Copied to clipboard")).not.toBeInTheDocument()
    expect(screen.queryByText(/could not create/i)).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Copy link" })).toBeInTheDocument()
  })

  it("keeps the copy button working after an auto-copy failure", async () => {
    writeText.mockRejectedValueOnce(new Error("denied"))
    createShare.mockResolvedValueOnce({ share: { id: "s1" }, url: "https://music.example/share/secret" })
    renderDialog()

    fireEvent.click(screen.getByRole("button", { name: "Create link" }))
    await screen.findByDisplayValue("https://music.example/share/secret")

    fireEvent.click(screen.getByRole("button", { name: "Copy link" }))

    expect(await screen.findByText("Copied to clipboard")).toBeInTheDocument()
    expect(writeText).toHaveBeenLastCalledWith("https://music.example/share/secret")
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
