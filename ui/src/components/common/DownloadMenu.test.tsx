import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import DownloadMenu from "./DownloadMenu"

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, asChild }: { children: ReactNode; asChild?: boolean }) =>
    asChild ? children : <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

describe("DownloadMenu", () => {
  it("offers MP3 192, MP3 320 and the original of the same endpoint", () => {
    render(<DownloadMenu href="/api/v1/releases/5/download" />)

    expect(screen.getByRole("link", { name: "MP3 192" })).toHaveAttribute(
      "href",
      "/api/v1/releases/5/download?format=mp3_192",
    )

    expect(screen.getByRole("link", { name: "Original" })).toHaveAttribute(
      "href",
      "/api/v1/releases/5/download",
    )
    expect(screen.getByRole("link", { name: "MP3 320" })).toHaveAttribute(
      "href",
      "/api/v1/releases/5/download?format=mp3_320",
    )
  })

  it("keeps the icon-only trigger labelled for assistive tech", () => {
    render(<DownloadMenu href="/api/v1/mixes/m/download" />)

    const trigger = screen.getByRole("button", { name: "Download" })
    expect(trigger).toHaveAttribute("data-size", "icon-sm")
    expect(trigger).not.toHaveTextContent("Download")
  })
})
