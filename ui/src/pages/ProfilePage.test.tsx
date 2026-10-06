import type { ReactNode } from "react"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ProfilePage from "./ProfilePage"
import { ApiError } from "@/api/client"
import type { ListenItem, UserProfile } from "@/api/profile"
import type { Person } from "@/api/social"
import { AVATAR_KEYS } from "@/lib/avatars"
import { formatRelativeTime } from "@/lib/relativeTime"

const useUserProfile = vi.fn()
const useUserLikes = vi.fn()
const useUserPlaylists = vi.fn()
const mutate = vi.fn()
const reset = vi.fn()
const usePeople = vi.fn()
const useRefreshListensOnPlayChange = vi.fn()

vi.mock("@/api/hooks/useProfile", () => ({
  useRefreshListensOnPlayChange: (...args: unknown[]) => useRefreshListensOnPlayChange(...args),
  useUserProfile: (...args: unknown[]) => useUserProfile(...args),
  useUserLikes: (...args: unknown[]) => useUserLikes(...args),
  useUserPlaylists: (...args: unknown[]) => useUserPlaylists(...args),
  useSetMyAvatar: () => ({ mutate, reset, isPending: false, isError: false }),
}))

vi.mock("@/api/hooks/usePeople", () => ({
  usePeople: () => usePeople(),
}))

const listenAlong = vi.fn()
vi.mock("@/api/profile", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/profile")>()),
  listenAlong: (...args: unknown[]) => listenAlong(...args),
}))

const playerState = { playSource: vi.fn(), playFromEnvelope: vi.fn() }
vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: typeof playerState) => unknown) => selector(playerState),
}))

vi.mock("@/components/media/ArtworkImage", () => ({
  default: ({ alt }: { alt: string }) => <img alt={alt} />,
}))

vi.mock("@/components/media/Shelf", () => ({
  default: ({ title, items, moreHref, total }: {
    title: string
    items: Array<{ id: number | string; title: string; subtitle?: string | null }>
    moreHref?: string
    total?: number
  }) =>
    items.length === 0 ? null : (
      <div data-testid={`shelf-${title}`} data-more-href={moreHref} data-total={total}>
        <span>{title}</span>
        {items.map((item) => (
          <span key={item.id}>{item.title} / {item.subtitle}</span>
        ))}
      </div>
    ),
}))

vi.mock("@/components/media/VirtualTrackRow", () => ({
  default: ({ track, metric }: { track: { title: string }; metric?: ReactNode }) => (
    <div data-testid="listen-row">
      <span>{track.title}</span>
      <span data-testid="listen-metric">{metric}</span>
    </div>
  ),
}))

vi.mock("@/components/media/VirtualTrackList", () => ({
  default: ({ tracks }: { tracks: Array<{ id: number; title: string; play_count: number }> }) => (
    <div data-testid="top-tracks">
      {tracks.map((track) => (
        <span key={track.id}>{track.title}: {track.play_count}</span>
      ))}
    </div>
  ),
}))

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

const THREE_HOURS_AGO = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString()

function makeProfile(overrides: Partial<UserProfile> = {}, header: Partial<UserProfile["header"]> = {}): UserProfile {
  return {
    header: {
      username: "alice",
      avatar: "a03",
      created_at: "2026-03-12T10:00:00+00:00",
      viewer_is_owner: false,
      totals: { listens: 1234, artists: 210, likes: 87 },
      ...header,
    },
    period: { key: "30d", tz: "Europe/Moscow", since: null, until: "2026-10-04T10:00:00+00:00" },
    summary: { listens: 6, hours: 21.4, artists: 3 },
    by_day_bucket: "day",
    by_day: [
      { date: "2026-10-03", listens: 2 },
      { date: "2026-10-04", listens: 4 },
    ],
    by_hour: Array.from({ length: 24 }, (_, hour) => (hour === 20 ? 6 : 0)),
    sound: {
      genres: [{ label: "Electronic---Techno", genre: "Electronic", style: "Techno", listens: 3, share: 0.5 }],
      moods: [{ label: "energetic", listens: 2, share: 0.25 }],
    },
    top_artists: [
      {
        id: "artist:5", entity_type: "artist", entity_id: 5, title: "Alpha", subtitle: "",
        artwork: { url: null, source: "none", placeholder: true }, reason: null, play_action: null, listens: 12,
      },
    ],
    top_artists_total: 40,
    top_releases: [],
    top_releases_total: 0,
    top_tracks: [{ ...makeListen(0, 9, THREE_HOURS_AGO), listens: 7 }],
    top_tracks_total: 1,
    // The same track twice: a listening feed, not unique tracks.
    recent: [makeListen(11, 9, THREE_HOURS_AGO), makeListen(10, 9, THREE_HOURS_AGO)],
    ...overrides,
  }
}

function mockProfile(profile: UserProfile) {
  useUserProfile.mockReturnValue({ data: profile, isLoading: false, isPlaceholderData: false, error: null })
}

function person(username: string, nowPlaying: Person["now_playing"]): Person {
  return { username, avatar: "a01", now_playing: nowPlaying }
}

function renderPage(path = "/u/alice") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/u/:username" element={<ProfilePage />} />
        <Route path="/u/:username/history" element={<p>history page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe("ProfilePage", () => {
  beforeEach(() => {
    useUserProfile.mockReset()
    mutate.mockReset()
    reset.mockReset()
    mockProfile(makeProfile())
    useUserLikes.mockReturnValue({ data: undefined })
    useUserPlaylists.mockReturnValue({ data: undefined })
    usePeople.mockReturnValue({ data: { items: [] } })
  })

  it("shows the header: username, member-since date and all-time totals", () => {
    renderPage()

    expect(screen.getByRole("heading", { level: 1, name: "alice" })).toBeInTheDocument()
    expect(screen.getByText(/in discocs since March 12, 2026/)).toHaveTextContent(
      "1,234 listens · 210 artists · 87 likes",
    )
  })

  it("requests the 30-day period by default and refetches for the picked period", () => {
    renderPage()
    expect(useUserProfile).toHaveBeenLastCalledWith("alice", "30d")

    fireEvent.mouseDown(screen.getByRole("tab", { name: "7d" }), { button: 0 })

    expect(useUserProfile).toHaveBeenLastCalledWith("alice", "7d")
    expect(screen.getByRole("tab", { name: "7d" })).toHaveAttribute("aria-selected", "true")
  })

  it("lists recent listens with relative times, repeats included, and links to the full history", () => {
    renderPage()

    const recent = screen.getByRole("region", { name: "Recent listens" })
    const rows = within(recent).getAllByTestId("listen-row")
    expect(rows).toHaveLength(2)
    const expected = formatRelativeTime(THREE_HOURS_AGO, "en")
    expect(expected).not.toBe("")
    for (const metric of within(recent).getAllByTestId("listen-metric")) {
      expect(metric).toHaveTextContent(expected)
    }
    expect(within(recent).getByRole("link", { name: "All" })).toHaveAttribute("href", "/u/alice/history")
  })

  it("puts the playing track first in the recent listens, marked now", () => {
    usePeople.mockReturnValue({
      data: {
        items: [
          person("alice", {
            track_id: 99,
            title: "Live One",
            artists: "Alpha",
            state: "playing",
            track: { ...makeListen(0, 99, THREE_HOURS_AGO), title: "Live One" },
          }),
        ],
      },
    })
    renderPage()

    const recent = screen.getByRole("region", { name: "Recent listens" })
    const rows = within(recent).getAllByTestId("listen-row")
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent("Live One")
    expect(within(rows[0]).getByTestId("listen-now")).toHaveTextContent("now")
  })

  it("names the period in the titles of the period-driven sections", () => {
    renderPage()

    expect(screen.getByRole("region", { name: "Statistics (30 days)" })).toBeInTheDocument()
    expect(screen.getByTestId("shelf-Top artists (30 days)")).toBeInTheDocument()
    // Recent listens are not period-driven.
    expect(screen.getByRole("region", { name: "Recent listens" })).toBeInTheDocument()

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Year" }), { button: 0 })

    expect(screen.getByRole("region", { name: "Statistics (year)" })).toBeInTheDocument()
    expect(screen.getByTestId("shelf-Top artists (year)")).toBeInTheDocument()
  })

  it("links the top shelves to their full lists for the selected period", () => {
    mockProfile(makeProfile({
      top_releases: [{
        id: "release:4", entity_type: "release", entity_id: 4, title: "LP", subtitle: "",
        artwork: { url: null, source: "none", placeholder: true }, reason: null, play_action: null, listens: 3,
      }],
      top_releases_total: 17,
    }))
    renderPage()

    const artists = screen.getByTestId("shelf-Top artists (30 days)")
    expect(artists).toHaveAttribute("data-more-href", "/u/alice/top/artists?period=30d")
    expect(artists).toHaveAttribute("data-total", "40")
    const releases = screen.getByTestId("shelf-Top releases (30 days)")
    expect(releases).toHaveAttribute("data-more-href", "/u/alice/top/releases?period=30d")
    expect(releases).toHaveAttribute("data-total", "17")

    fireEvent.mouseDown(screen.getByRole("tab", { name: "90d" }), { button: 0 })

    expect(screen.getByTestId("shelf-Top artists (90 days)")).toHaveAttribute(
      "data-more-href", "/u/alice/top/artists?period=90d",
    )
  })

  it("links the top tracks to their full list only when the period has more", () => {
    mockProfile(makeProfile({ top_tracks_total: 12 }, { viewer_is_owner: false }))
    const { unmount } = renderPage("/u/alice?period=7d")

    const section = screen.getByRole("region", { name: "Top tracks (7 days)" })
    expect(within(section).getByRole("link", { name: "More" })).toHaveAttribute(
      "href", "/u/alice/top/tracks?period=7d",
    )
    unmount()

    mockProfile(makeProfile({ top_tracks_total: 1 }))
    renderPage()
    expect(
      within(screen.getByRole("region", { name: "Top tracks (30 days)" })).queryByRole("link"),
    ).not.toBeInTheDocument()
  })

  it("takes the period from the URL, so a full list's back link restores it", () => {
    renderPage("/u/alice?period=365d")

    expect(useUserProfile).toHaveBeenLastCalledWith("alice", "365d")
    expect(screen.getByRole("tab", { name: "Year" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByTestId("shelf-Top artists (year)")).toHaveAttribute(
      "data-more-href", "/u/alice/top/artists?period=365d",
    )
  })

  it("ignores an unknown period in the URL", () => {
    renderPage("/u/alice?period=14d")

    expect(useUserProfile).toHaveBeenLastCalledWith("alice", "30d")
  })

  it("draws by-day bars proportional to the listens, labelled with their value", () => {
    renderPage()

    const byDay = screen.getByRole("list", { name: "By day" })
    const bars = within(byDay).getAllByRole("listitem")
    expect(bars).toHaveLength(2)
    expect(bars[0]).toHaveAccessibleName(/2 listens$/)
    expect(bars[1]).toHaveAccessibleName(/4 listens$/)
    const heights = within(byDay).getAllByTestId("chart-bar").map((bar) => bar.style.height)
    expect(heights).toEqual(["50%", "100%"])

    const byHour = screen.getByRole("list", { name: "By hour of day" })
    expect(within(byHour).getAllByRole("listitem")).toHaveLength(24)
    expect(within(byHour).getByRole("listitem", { name: "20:00: 6 listens" })).toBeInTheDocument()
  })

  it("labels month buckets by month for the all-time period", () => {
    mockProfile(makeProfile({
      by_day_bucket: "month",
      by_day: [
        { date: "2025-02-01", listens: 1 },
        { date: "2026-03-01", listens: 3 },
      ],
    }))
    renderPage()

    const byMonth = screen.getByRole("list", { name: "By month" })
    expect(within(byMonth).getByRole("listitem", { name: "March 2026: 3 listens" })).toBeInTheDocument()
    expect(screen.queryByRole("list", { name: "By day" })).not.toBeInTheDocument()
  })

  it("shows the summary and the sound profile of the period", () => {
    renderPage()

    expect(screen.getByTestId("summary-listens")).toHaveTextContent("6")
    expect(screen.getByTestId("summary-hours")).toHaveTextContent("21.4")
    expect(screen.getByTestId("summary-artists")).toHaveTextContent("3")
    expect(screen.getByText("Techno")).toBeInTheDocument()
    expect(screen.getByText("50%")).toBeInTheDocument()
    expect(screen.getByText("energetic")).toBeInTheDocument()
  })

  it("puts the listens count into the top-artist cards and the top-track rows", () => {
    renderPage()

    expect(screen.getByTestId("shelf-Top artists (30 days)")).toHaveTextContent("Alpha / 12 listens")
    expect(screen.getByTestId("top-tracks")).toHaveTextContent("Track 9: 7")
  })

  it("shows an empty state instead of stats for a user without listens", () => {
    mockProfile(makeProfile(
      { summary: { listens: 0, hours: 0, artists: 0 }, by_day: [], recent: [], top_artists: [], top_tracks: [] },
      { totals: { listens: 0, artists: 0, likes: 0 } },
    ))
    renderPage()

    expect(screen.getByText("No listens yet.")).toBeInTheDocument()
    expect(screen.queryByRole("tab")).not.toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Recent listens" })).not.toBeInTheDocument()
  })

  it("says the period is empty while keeping the period switch", () => {
    mockProfile(makeProfile({ summary: { listens: 0, hours: 0, artists: 0 }, top_artists: [], top_tracks: [] }))
    renderPage()

    expect(screen.getByText("No listens in this period.")).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "30d" })).toBeInTheDocument()
  })

  it("reports an unknown user", () => {
    useUserProfile.mockReturnValue({
      data: undefined, isLoading: false, isPlaceholderData: false, error: new ApiError(404, "not_found", "nope"),
    })
    renderPage("/u/ghost")

    expect(screen.getByText("User not found.")).toBeInTheDocument()
  })

  it("shows a skeleton while loading", () => {
    useUserProfile.mockReturnValue({ data: undefined, isLoading: true, isPlaceholderData: false, error: null })
    renderPage()

    expect(screen.getByTestId("profile-skeleton")).toBeInTheDocument()
  })

  it("shows the live line only while the user is playing", () => {
    usePeople.mockReturnValue({
      data: { items: [person("Alice", { track_id: 3, title: "Signals", artists: "Alpha", state: "playing", track: null })] },
    })
    const { unmount } = renderPage()

    expect(screen.getByTestId("now-playing")).toHaveTextContent("Now playing: Alpha - Signals")
    expect(screen.getByTestId("live-dot")).toHaveClass("motion-safe:animate-pulse")
    unmount()

    usePeople.mockReturnValue({ data: { items: [person("alice", null), person("bob", { track_id: 1, title: "X", artists: "Y", state: "playing", track: null })] } })
    renderPage()
    expect(screen.queryByTestId("now-playing")).not.toBeInTheDocument()
  })

  it("refreshes the listens on this user's play changes, once the people list is known", () => {
    usePeople.mockReturnValue({ data: undefined })
    const { unmount } = renderPage()
    expect(useRefreshListensOnPlayChange).toHaveBeenLastCalledWith("alice", undefined)
    unmount()

    const playing = { track_id: 3, title: "Signals", artists: "Alpha", state: "playing" as const, track: null }
    usePeople.mockReturnValue({ data: { items: [person("bob", null), person("Alice", playing)] } })
    renderPage()
    expect(useRefreshListensOnPlayChange).toHaveBeenLastCalledWith("alice", playing)
  })

  it("shows the live line on one's own profile too", () => {
    mockProfile(makeProfile({}, { viewer_is_owner: true }))
    usePeople.mockReturnValue({
      data: { items: [person("alice", { track_id: 3, title: "Signals", artists: "Alpha", state: "playing", track: null })] },
    })
    renderPage()

    expect(screen.getByTestId("now-playing")).toHaveTextContent("Now playing: Alpha - Signals")
  })

  it("does not let another user's avatar be changed", () => {
    renderPage()

    expect(screen.queryByRole("button", { name: "Choose avatar" })).not.toBeInTheDocument()
  })

  it("lets the owner pick a built-in avatar, saving it and closing the picker", async () => {
    mockProfile(makeProfile({}, { viewer_is_owner: true }))
    mutate.mockImplementation((_key: string, options?: { onSuccess?: () => void }) => options?.onSuccess?.())
    renderPage()

    fireEvent.click(screen.getByRole("button", { name: "Choose avatar" }))
    const dialog = await screen.findByRole("dialog")
    // Buttons are labelled by grid position; the grid order is AVATAR_KEYS (mixed).
    const option = (key: string) =>
      within(dialog).getByRole("button", { name: `Avatar ${AVATAR_KEYS.indexOf(key) + 1}` })
    expect(option("a03")).toHaveAttribute("aria-pressed", "true")
    expect(option("a01")).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(option("a05"))

    expect(mutate).toHaveBeenCalledWith("a05", expect.anything())
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("renders likes and playlists shelves", () => {
    useUserLikes.mockReturnValue({
      data: {
        tracks: { items: [], total: 0 },
        releases: {
          items: [{
            id: "release:4", entity_type: "release", entity_id: 4, title: "Liked LP", subtitle: "Beta",
            artwork: { url: null, source: "none", placeholder: true }, reason: null, play_action: null,
          }],
          total: 30,
        },
        artists: { items: [], total: 0 },
        limit: 16,
        offset: 0,
      },
    })
    useUserPlaylists.mockReturnValue({
      data: {
        items: [{
          id: 8, title: "Night drive", kind: "manual", description: null, track_count: 3,
          artwork: { url: null, source: "none", placeholder: true }, source: null, visibility: "public",
          editable: false, created_at: "", updated_at: "",
          action: { type: "open", target: "/playlists/8" },
          play_action: { type: "post", endpoint: "/api/v1/playlists/8/play" },
        }],
        total: 20,
        limit: 16,
        offset: 0,
        next_offset: 16,
      },
    })
    renderPage()

    expect(screen.getByRole("region", { name: "Likes" })).toBeInTheDocument()
    expect(screen.getByTestId("shelf-Releases")).toHaveTextContent("Liked LP")
    expect(screen.queryByTestId("shelf-Tracks")).not.toBeInTheDocument()
    expect(screen.getByTestId("shelf-Playlists")).toHaveTextContent("Night drive / 3 tracks")
    // Each shelf links to its full list and knows how long that list is.
    expect(screen.getByTestId("shelf-Releases")).toHaveAttribute("data-more-href", "/u/alice/likes/releases")
    expect(screen.getByTestId("shelf-Releases")).toHaveAttribute("data-total", "30")
    expect(screen.getByTestId("shelf-Playlists")).toHaveAttribute("data-more-href", "/u/alice/playlists")
    expect(screen.getByTestId("shelf-Playlists")).toHaveAttribute("data-total", "20")
  })

  describe("listen along", () => {
    const playing = (track: ListenItem | null) =>
      person("alice", { track_id: 9, title: "Signals", artists: "Alpha", state: "playing", track })

    beforeEach(() => {
      listenAlong.mockReset()
      playerState.playFromEnvelope.mockReset()
    })

    it("picks up another user's playback at their track and position", async () => {
      const envelope = { start_track_id: 9, start_position_seconds: 42.5 }
      listenAlong.mockResolvedValue(envelope)
      usePeople.mockReturnValue({ data: { items: [playing(makeListen(0, 9, THREE_HOURS_AGO))] } })
      renderPage()

      const button = within(screen.getByTestId("now-playing")).getByRole("button", { name: "Listen" })
      fireEvent.click(button)

      await vi.waitFor(() => expect(playerState.playFromEnvelope).toHaveBeenCalledTimes(1))
      expect(listenAlong).toHaveBeenCalledWith("alice")
      expect(playerState.playFromEnvelope).toHaveBeenCalledWith(envelope, 9, { startPositionSeconds: 42.5 })
    })

    it("has no button on one's own profile or for a track outside the library", () => {
      mockProfile(makeProfile({}, { viewer_is_owner: true }))
      usePeople.mockReturnValue({ data: { items: [playing(makeListen(0, 9, THREE_HOURS_AGO))] } })
      const { unmount } = renderPage()
      expect(screen.getByTestId("now-playing")).toBeInTheDocument()
      expect(screen.queryByTestId("listen-along")).not.toBeInTheDocument()
      unmount()

      mockProfile(makeProfile())
      usePeople.mockReturnValue({ data: { items: [playing(null)] } })
      renderPage()
      expect(screen.getByTestId("now-playing")).toBeInTheDocument()
      expect(screen.queryByTestId("listen-along")).not.toBeInTheDocument()
    })

    it("does nothing to the player when the user stopped meanwhile", async () => {
      listenAlong.mockRejectedValue(new ApiError(409, "not_playing", "not playing"))
      usePeople.mockReturnValue({ data: { items: [playing(makeListen(0, 9, THREE_HOURS_AGO))] } })
      renderPage()

      const button = screen.getByTestId("listen-along")
      fireEvent.click(button)

      await vi.waitFor(() => expect(button).not.toBeDisabled())
      expect(listenAlong).toHaveBeenCalledTimes(1)
      expect(playerState.playFromEnvelope).not.toHaveBeenCalled()
    })
  })
})
