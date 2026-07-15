import type { ReactNode } from "react"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"
import TrackMenu from "./TrackMenu"
import type { TrackSummary } from "@/api/types"

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, asChild }: { children: ReactNode; asChild?: boolean }) =>
    asChild ? children : <div>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    session: null,
    playSource: vi.fn(),
    refreshQueue: vi.fn(),
    playFromEnvelope: vi.fn(),
  }),
}))

const track = {
  id: 42,
  title: "Download me",
  artists: [],
  release: null,
  duration: 120,
  artwork: { url: null, source: "placeholder", placeholder: true },
} as TrackSummary

describe("TrackMenu download", () => {
  it("links the download action to the attachment endpoint", () => {
    render(<MemoryRouter><TrackMenu track={track} /></MemoryRouter>)

    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download",
    )
  })
})
