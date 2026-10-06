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
  DropdownMenuSub: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuSubTrigger: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuSubContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
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

  it("groups the formats under one «Download» item: MP3 192, MP3 320 and the original", () => {
    render(<MemoryRouter><TrackMenu track={track} /></MemoryRouter>)

    expect(screen.getByText("Download")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "MP3 192" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download?format=mp3_192",
    )
    expect(screen.getByRole("link", { name: "MP3 320" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download?format=mp3_320",
    )
    expect(screen.getByRole("link", { name: "Original" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download",
    )
  })

  it("calls the stored file FLAC when it is one, and names any other container", () => {
    const { unmount } = render(
      <MemoryRouter><TrackMenu track={{ ...track, audio_format: "flac" }} /></MemoryRouter>,
    )
    expect(screen.getByRole("link", { name: "FLAC" })).toHaveAttribute(
      "href",
      "/api/v1/tracks/42/download",
    )
    unmount()

    render(<MemoryRouter><TrackMenu track={{ ...track, audio_format: "m4a" }} /></MemoryRouter>)
    expect(screen.getByRole("link", { name: "Original (M4A)" })).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "FLAC" })).toBeNull()
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
