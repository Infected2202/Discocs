import type { ReactNode } from "react"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ListeningHistoryPage from "./ListeningHistoryPage"
import { ApiError } from "@/api/client"
import type { ListenItem, UserListensPage } from "@/api/profile"

const useUserListens = vi.fn()
const fetchNextPage = vi.fn()

vi.mock("@/api/hooks/useProfile", () => ({
  useUserListens: (...args: unknown[]) => useUserListens(...args),
}))

vi.mock("@/components/media/VirtualTrackRow", () => ({
  default: ({ track, metric }: { track: { title: string }; metric?: ReactNode }) => (
    <div data-testid="listen-row">
      {track.title} | {metric}
    </div>
  ),
}))

function localIso(daysAgo: number, hour: number): string {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo, hour, 0).toISOString()
}

function makeListen(listenId: number, trackId: number, listenedAt: string): ListenItem {
  return {
    listen_id: listenId,
    listened_at: listenedAt,
    id: trackId,
    title: `Track ${trackId}`,
    artists: [],
    duration: 200,
    release: null,
    artwork: { url: null, source: "none", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function page(items: ListenItem[], offset: number, nextOffset: number | null): UserListensPage {
  return { items, total: 3, limit: 50, offset, next_offset: nextOffset }
}

function mockListens(pages: UserListensPage[], extra: Record<string, unknown> = {}) {
  useUserListens.mockReturnValue({
    data: { pages },
    isLoading: false,
    error: null,
    hasNextPage: pages.at(-1)?.next_offset != null,
    isFetchingNextPage: false,
    fetchNextPage,
    ...extra,
  })
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/u/alice/history"]}>
      <Routes>
        <Route path="/u/:username/history" element={<ListeningHistoryPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("ListeningHistoryPage", () => {
  beforeEach(() => {
    useUserListens.mockReset()
    fetchNextPage.mockReset()
  })

  it("groups the listens of all loaded pages under local day headings", () => {
    // Earlier hours of "today" can be in the future right after midnight; 0:00 never is.
    mockListens([
      page([makeListen(3, 1, localIso(0, 0)), makeListen(2, 1, localIso(1, 21))], 0, 2),
      page([makeListen(1, 2, localIso(1, 9))], 2, null),
    ])
    renderPage()

    expect(useUserListens).toHaveBeenCalledWith("alice")
    const today = screen.getByRole("region", { name: "Today" })
    const yesterday = screen.getByRole("region", { name: "Yesterday" })
    expect(within(today).getAllByTestId("listen-row")).toHaveLength(1)
    // The day spans the page boundary and is still one group.
    expect(within(yesterday).getAllByTestId("listen-row")).toHaveLength(2)
    expect(screen.getByText(/alice · 3 listens/)).toBeInTheDocument()
  })

  it("loads the next page on demand and hides the button on the last page", () => {
    mockListens([page([makeListen(3, 1, localIso(0, 0))], 0, 50)])
    const { unmount } = renderPage()

    fireEvent.click(screen.getByRole("button", { name: "Load more" }))
    expect(fetchNextPage).toHaveBeenCalledTimes(1)
    unmount()

    mockListens([page([makeListen(3, 1, localIso(0, 0))], 0, null)])
    renderPage()
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument()
  })

  it("shows an empty state for a user without listens", () => {
    mockListens([{ items: [], total: 0, limit: 50, offset: 0, next_offset: null }])
    renderPage()

    expect(screen.getByText("No listens yet.")).toBeInTheDocument()
  })

  it("reports an unknown user", () => {
    useUserListens.mockReturnValue({
      data: undefined,
      isLoading: false,
      error: new ApiError(404, "not_found", "nope"),
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage,
    })
    renderPage()

    expect(screen.getByText("User not found.")).toBeInTheDocument()
  })
})
