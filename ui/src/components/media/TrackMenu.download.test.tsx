import type { ReactNode } from "react"
import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { describe, expect, it, vi } from "vitest"
import TrackMenu from "./TrackMenu"
import type { TrackSummary } from "@/api/types"

vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, asChild, onClick }: { children: ReactNode; asChild?: boolean; onClick?: () => void }) =>
    asChild ? children : <div onClick={onClick}>{children}</div>,
  DropdownMenuSeparator: () => <hr />,
  DropdownMenuTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: Record<string, unknown>) => unknown) => selector({
    playSource: vi.fn(),
    adoptInstantMix: vi.fn(),
    playNext: vi.fn(),
  }),
}))

vi.mock("@/api/shares", () => ({
  useShareCapabilities: () => ({ data: { enabled: true, can_create: true } }),
  createShare: vi.fn(),
}))

const track: TrackSummary = {
  id: 42,
  title: "Download me",
  artists: [],
  release: null,
  duration: 120,
  artwork: { url: null, source: "placeholder", placeholder: true },
  explicit: false,
  liked: false,
  actions: [],
}

describe("TrackMenu download", () => {
  it("marks the trigger for touch-visible responsive styling", () => {
    render(<MemoryRouter><TrackMenu track={track} /></MemoryRouter>)

    expect(screen.getByRole("button", { name: "Track options" })).toHaveClass(
      "track-menu-trigger",
    )
  })

  it("offers the original and an MP3 320 of the attachment endpoint", () => {
    render(<MemoryRouter><TrackMenu track={track} /></MemoryRouter>)

    expect(screen.getByRole("link", { name: "Download original" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download",
    )
    expect(screen.getByRole("link", { name: "Download as MP3 320" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download?format=mp3",
    )
  })

  it("shows the share action with an icon for authenticated creators", () => {
    render(<MemoryRouter><TrackMenu track={track} /></MemoryRouter>)

    const share = screen.getByText("Share").closest("div")
    expect(share?.querySelector("svg.lucide-share-2")).toBeTruthy()
  })

  it("shows «Remove from playlist» only when the row belongs to an editable playlist", () => {
    const onRemove = vi.fn()
    const { unmount } = render(<MemoryRouter><TrackMenu track={track} /></MemoryRouter>)
    expect(screen.queryByText("Remove from playlist")).toBeNull()
    unmount()

    render(<MemoryRouter><TrackMenu track={track} onRemoveFromPlaylist={onRemove} /></MemoryRouter>)
    fireEvent.click(screen.getByText("Remove from playlist"))
    expect(onRemove).toHaveBeenCalledTimes(1)
  })
})
