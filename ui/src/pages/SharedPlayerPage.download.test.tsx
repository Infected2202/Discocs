import type { ComponentProps, ReactNode } from "react"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import SharedPlayerPage from "./SharedPlayerPage"

const fetchPublicShare = vi.fn()

// Same approach as TrackMenu.download.test.tsx: render the menu contents
// inline so the links are assertable without driving Radix's open state.
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, asChild }: { children: ReactNode; asChild?: boolean }) =>
    asChild ? children : <div>{children}</div>,
  DropdownMenuTrigger: ({ children, ...props }: ComponentProps<"button">) =>
    <button {...props}>{children}</button>,
}))

vi.mock("@/api/shares", () => ({
  fetchPublicShare: (...args: unknown[]) => fetchPublicShare(...args),
}))

vi.mock("@/hooks/useArtworkTheme", () => ({ useArtworkTheme: () => undefined }))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/share/test-token"]}>
      <Routes><Route path="/share/:token" element={<SharedPlayerPage />} /></Routes>
    </MemoryRouter>,
  )
}

function share(items: unknown[]) {
  return {
    kind: "release",
    title: "Shared album",
    subtitle: "Artist",
    expires_at: null,
    artwork_url: "/cover",
    download_url: "/api/v1/public/shares/tok/download",
    items,
  }
}

const AVAILABLE_ITEMS = [
  {
    position: 0, title: "First", artist: "Artist", duration: 60, available: true,
    audio_url: "/audio/0", download_url: "/api/v1/public/shares/tok/items/0/download",
  },
  {
    position: 1, title: "Second", artist: "Artist", duration: 90, available: true,
    audio_url: "/audio/1", download_url: "/api/v1/public/shares/tok/items/1/download",
  },
]

describe("SharedPlayerPage downloads", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    fetchPublicShare.mockReset()
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => undefined)
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined)
    fetchPublicShare.mockResolvedValue(share(AVAILABLE_ITEMS))
  })

  it("points every track download at that item's capability URL", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    const links = screen.getAllByRole("link", { name: "Download track" })
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      // now playing, then one per queue row
      "/api/v1/public/shares/tok/items/0/download",
      "/api/v1/public/shares/tok/items/0/download",
      "/api/v1/public/shares/tok/items/1/download",
    ])
  })

  it("offers the whole shared list as one archive", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    expect(screen.getByRole("link", { name: "Download all" })).toHaveAttribute(
      "href",
      "/api/v1/public/shares/tok/download",
    )
  })

  it("marks downloads as attachments so the browser saves instead of navigating", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    for (const link of screen.getAllByRole("link", { name: /^Download/ })) {
      expect(link).toHaveAttribute("download")
    }
  })

  it("names the track in each trigger so queue rows stay distinguishable", async () => {
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    expect(screen.getAllByRole("button", { name: 'Actions for First' })).toHaveLength(2)
    expect(screen.getByRole("button", { name: 'Actions for Second' })).toBeInTheDocument()
  })

  it("offers no download for an item whose file is gone", async () => {
    fetchPublicShare.mockResolvedValue(share([
      AVAILABLE_ITEMS[0],
      { ...AVAILABLE_ITEMS[1], available: false },
    ]))
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    expect(screen.queryByRole("button", { name: 'Actions for Second' })).not.toBeInTheDocument()
    expect(screen.getAllByRole("link", { name: "Download track" })).toHaveLength(2)
  })

  it("keeps a single-track share downloadable without a queue", async () => {
    fetchPublicShare.mockResolvedValue({ ...share([AVAILABLE_ITEMS[0]]), kind: "track" })
    renderPage()
    await screen.findByRole("heading", { name: "First" })

    // No queue aside for one item, so the header menu is the only way in.
    expect(screen.queryByRole("link", { name: "Download all" })).not.toBeInTheDocument()
    await waitFor(() => expect(
      screen.getByRole("link", { name: "Download track" }),
    ).toHaveAttribute("href", "/api/v1/public/shares/tok/items/0/download"))
  })
})
