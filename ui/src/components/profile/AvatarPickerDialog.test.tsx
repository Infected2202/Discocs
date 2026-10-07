import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { AVATAR_KEYS } from "@/lib/avatars"
import AvatarPickerDialog from "./AvatarPickerDialog"

vi.mock("@/api/hooks/useProfile", () => ({
  useSetMyAvatar: () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false }),
}))

describe("AvatarPickerDialog", () => {
  // jsdom has no layout, so the guard is on the classes that keep the centred
  // dialog inside the screen: capped height, only the grid scrolls.
  it("caps its height to the viewport and scrolls the avatar grid, not the dialog", () => {
    render(<AvatarPickerDialog open onOpenChange={vi.fn()} current="" />)

    const dialog = screen.getByRole("dialog")
    expect(dialog.className).toContain("max-h-[calc(100dvh-2rem)]")

    const scroll = screen.getByTestId("avatar-picker-scroll")
    expect(scroll.className).toContain("overflow-y-auto")
    expect(scroll.className).toContain("min-h-0")
    expect(scroll.querySelectorAll("button")).toHaveLength(AVATAR_KEYS.length)

    // The close button must stay outside the scrolling part, or it scrolls away.
    const closeButton = dialog.querySelector('[data-slot="dialog-close"]')
    expect(closeButton).not.toBeNull()
    expect(scroll.contains(closeButton)).toBe(false)
  })
})
