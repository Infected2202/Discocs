import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import MixPage from "./MixPage"
import type { GeneratedMixDetail } from "@/api/types"

const useMix = vi.fn()
const playMix = vi.fn()
const playUserMix = vi.fn()
const playFromEnvelope = vi.fn()

vi.mock("@/api/hooks/useMix", () => ({
  useMix: (...args: unknown[]) => useMix(...args),
}))
vi.mock("@/api/mixes", () => ({
  playMix: (...args: unknown[]) => playMix(...args),
  saveMix: vi.fn(),
}))
vi.mock("@/api/profile", () => ({
  playUserMix: (...args: unknown[]) => playUserMix(...args),
}))
vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: { playFromEnvelope: typeof playFromEnvelope }) => unknown) =>
    selector({ playFromEnvelope }),
}))
vi.mock("@/components/common/DownloadMenu", () => ({
  default: ({ href }: { href: string }) => <a href={href} aria-label="Download" />,
}))
vi.mock("@/components/media/VirtualTrackList", () => ({
  default: ({ onPlayTrack }: { onPlayTrack?: (trackId: number) => void }) => (
    <button type="button" data-testid="track-row" onClick={() => onPlayTrack?.(7)} />
  ),
}))

function makeMix(): GeneratedMixDetail {
  return {
    id: "mix-1",
    title: "Evening mix",
    status: "active",
    track_count: 1,
    artwork: null,
    created_at: "2026-07-08T00:00:00Z",
    // Another user's mix carries no scores or reasons.
    items: [{
      track_id: 7,
      position: 0,
      track: {
        id: 7,
        title: "Track",
        artists: [],
        release: null,
        duration: 120,
        artwork: { url: null, source: "placeholder", placeholder: true },
        explicit: false,
        liked: false,
        actions: [],
      },
    }],
  }
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/mixes/:id" element={<MixPage />} />
          <Route path="/u/:username/mixes/:id" element={<MixPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe("MixPage on someone's profile (/u/:username/mixes/:id)", () => {
  beforeEach(() => {
    useMix.mockReset().mockReturnValue({ data: makeMix(), isLoading: false, error: null })
    playMix.mockReset().mockResolvedValue({ session: { id: "own" } })
    playUserMix.mockReset().mockResolvedValue({ session: { id: "profile" } })
    playFromEnvelope.mockReset()
  })

  it("reads the mix through the profile and says whose it is", () => {
    renderAt("/u/alice/mixes/mix-1")

    expect(useMix).toHaveBeenCalledWith("mix-1", "alice")
    expect(screen.getByText("Mix for alice")).toBeInTheDocument()
    expect(screen.getByText("Evening mix")).toBeInTheDocument()
  })

  it("is read-only: neither Save nor Download", () => {
    renderAt("/u/alice/mixes/mix-1")

    expect(screen.queryByRole("button", { name: /save/i })).toBeNull()
    expect(screen.queryByRole("link", { name: "Download" })).toBeNull()
    expect(screen.getByRole("button", { name: /play/i })).toBeInTheDocument()
  })

  it("plays through the profile endpoint, never the viewer's own mix one", async () => {
    renderAt("/u/alice/mixes/mix-1")

    fireEvent.click(screen.getByRole("button", { name: /play/i }))

    await waitFor(() => expect(playFromEnvelope).toHaveBeenCalledWith({ session: { id: "profile" } }, undefined))
    expect(playUserMix).toHaveBeenCalledWith("alice", "mix-1")
    expect(playMix).not.toHaveBeenCalled()
  })

  it("starts from the clicked track row", async () => {
    renderAt("/u/alice/mixes/mix-1")

    fireEvent.click(screen.getByTestId("track-row"))

    await waitFor(() => expect(playFromEnvelope).toHaveBeenCalledWith({ session: { id: "profile" } }, 7))
    expect(playUserMix).toHaveBeenCalledWith("alice", "mix-1")
  })

  it("keeps the ordinary page of one's own mix: own endpoints, save and download", async () => {
    renderAt("/mixes/mix-1")

    expect(useMix).toHaveBeenCalledWith("mix-1", undefined)
    expect(screen.getByText("Generated mix")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /save/i })).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", "/api/v1/mixes/mix-1/download")

    fireEvent.click(screen.getByRole("button", { name: /play/i }))
    await waitFor(() => expect(playMix).toHaveBeenCalledWith("mix-1"))
    expect(playUserMix).not.toHaveBeenCalled()
  })
})
