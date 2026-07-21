import { act, fireEvent, render, screen, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"

const playback = vi.hoisted(() => ({
  init: vi.fn(),
  getEngineSnapshot: vi.fn(),
  getMixerMeters: vi.fn(() => ({ A: 0.2, B: 0.1, master: 0.3 })),
  subscribeEngine: vi.fn(() => () => undefined),
  setDeckTrim: vi.fn(),
  setDeckEq: vi.fn(),
  setDeckFilter: vi.fn(),
  setDeckChannelFader: vi.fn(),
  setCrossfader: vi.fn(),
  setMasterGain: vi.fn(),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  load: vi.fn(),
  play: vi.fn(),
  pause: vi.fn(),
  seek: vi.fn(),
}))
const useTimeline = vi.hoisted(() => vi.fn(() => ({ status: "missing" as const })))
const renderWaveform = vi.hoisted(() => vi.fn())

vi.mock("@/engine/playback", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/engine/playback")>()),
  playerPlayback: playback,
}))

vi.mock("@/engine/timeline", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/engine/timeline")>()),
  useTimeline,
}))

vi.mock("@/engine/waveform", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/engine/waveform")>()),
  WaveformSurface: (props: {
    ariaLabel?: string
    input: { follow: boolean; viewport: { startSeconds: number; endSeconds: number } }
  }) => {
    renderWaveform(props)
    return <canvas data-testid="waveform-canvas" />
  },
}))

vi.mock("@/components/media/ArtworkImage", () => ({
  default: () => <div data-testid="artwork" />,
}))

vi.mock("@/components/player/QueueItem", () => ({
  default: ({ trackId }: { trackId: number }) => <div data-testid="queue-row">{trackId}</div>,
}))

import DjControlSurface from "./DjControlSurface"

function snapshot(programDeck: "A" | "B" | null = "A") {
  return {
    revision: 1,
    contextState: "running" as const,
    programDeck,
    decks: {
      A: {
        id: "A" as const,
        role: programDeck === "A" ? "program" as const : "prepared" as const,
        preparation: "ready" as const,
        transport: "paused" as const,
        trackId: 1,
        queueItemId: "q1",
        duration: 180,
        anchor: { mediaSeconds: 12, audioTime: 5, rate: 1 },
        buffered: [],
      },
      B: {
        id: "B" as const,
        role: programDeck === "B" ? "program" as const : "prepared" as const,
        preparation: "ready" as const,
        transport: "paused" as const,
        trackId: 2,
        queueItemId: "q2",
        duration: 200,
        anchor: { mediaSeconds: 0, audioTime: 5, rate: 1 },
        buffered: [],
      },
    },
    mixer: {
      crossfader: 0,
      masterGain: 1,
      channelFaders: { A: 1, B: 1 },
      trims: { A: 0.5, B: 0.5 },
      eq: { A: { low: 0.5, mid: 0.5, high: 0.5 }, B: { low: 0.5, mid: 0.5, high: 0.5 } },
      filters: { A: 0, B: 0 },
      meters: { A: 0.2, B: 0.1, master: 0.3 },
    },
    automation: { owner: "none" as const },
    capabilities: { webAudio: true, mediaElementSource: true, ordinary: true, manualMix: true, reasons: [] },
    error: null,
  }
}

describe("DjControlSurface", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    playback.subscribeEngine.mockReturnValue(() => undefined)
    playback.getMixerMeters.mockReturnValue({ A: 0.2, B: 0.1, master: 0.3 })
    playback.getEngineSnapshot.mockReturnValue(snapshot())
    useTimeline.mockClear()
    renderWaveform.mockClear()
    useUIStore.setState({ djSurfaceOpen: false })
    usePlayerStore.setState({
      expanded: false,
      queue: null,
      currentTrack: null,
      currentQueueItemId: null,
      playedHistory: [],
      playbackState: "playing",
      currentTime: 21,
    })
  })

  it("opens and closes as presentation-only state without transport commands", () => {
    render(<DjControlSurface />)
    expect(screen.queryByTestId("dj-control-surface")).not.toBeInTheDocument()
    expect(playback.subscribeEngine).not.toHaveBeenCalled()
    act(() => useUIStore.getState().openDjSurface())
    expect(screen.getByTestId("dj-control-surface")).toHaveAttribute("aria-hidden", "false")
    expect(useTimeline).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByTestId("close-dj-surface"))

    expect(useUIStore.getState().djSurfaceOpen).toBe(false)
    expect(playback.load).not.toHaveBeenCalled()
    expect(playback.play).not.toHaveBeenCalled()
    expect(playback.pause).not.toHaveBeenCalled()
    expect(playback.seek).not.toHaveBeenCalled()
    expect(screen.queryByTestId("dj-control-surface")).not.toBeInTheDocument()
  })

  it("shows the ready program timeline instead of a permanent pending placeholder", () => {
    useTimeline.mockImplementationOnce(() => ({
      status: "ready",
      timeline: {
        durationSeconds: 180,
        levels: [],
        bpm: 118.26,
        beatConfidence: 0.8,
        rhythmCoverageSeconds: 180,
        beats: new Float32Array([0.5]),
        localTempo: new Float32Array([118.26]),
      },
    }) as never)
    useUIStore.setState({ djSurfaceOpen: true })

    render(<DjControlSurface />)

    expect(screen.getByText("118.3")).toBeInTheDocument()
    expect(screen.getByText("Timeline ready")).toBeInTheDocument()
    expect(screen.queryByText("Timeline pending")).not.toBeInTheDocument()
  })

  it("zooms both detailed deck waveforms together and resets to 16 seconds", () => {
    useTimeline.mockImplementation(() => ({
      status: "ready",
      timeline: {
        durationSeconds: 180,
        levels: [],
        bpm: 120,
        beatConfidence: 0.8,
        rhythmCoverageSeconds: 180,
        beats: new Float32Array([0.5]),
        localTempo: new Float32Array([120]),
      },
    }) as never)
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    const latestDeckWindow = (deck: "A" | "B") => {
      const calls = renderWaveform.mock.calls
        .map(([props]) => props)
        .filter((candidate) => candidate.ariaLabel === `Deck ${deck} detailed waveform`)
      const viewport = calls[calls.length - 1].input.viewport
      return viewport.endSeconds - viewport.startSeconds
    }

    expect(latestDeckWindow("A")).toBe(16)
    expect(latestDeckWindow("B")).toBe(16)
    expect(screen.getAllByLabelText("Deck waveforms zoom")).toHaveLength(1)
    expect(screen.getByLabelText("Reset deck waveforms zoom")).toBeDisabled()

    fireEvent.click(screen.getByLabelText("Zoom in deck waveforms"))
    expect(latestDeckWindow("A")).toBe(8)
    expect(latestDeckWindow("B")).toBe(8)
    expect(screen.getByLabelText("Zoom in deck waveforms")).toBeDisabled()

    fireEvent.click(screen.getByLabelText("Reset deck waveforms zoom"))
    expect(latestDeckWindow("A")).toBe(16)
    expect(latestDeckWindow("B")).toBe(16)

    fireEvent.click(screen.getByLabelText("Zoom out deck waveforms"))
    expect(latestDeckWindow("A")).toBe(30)
    expect(latestDeckWindow("B")).toBe(30)
  })

  it("renders an uninitialized engine snapshot without indexing a null program deck", () => {
    playback.getEngineSnapshot.mockReturnValue(snapshot(null))
    useUIStore.setState({ djSurfaceOpen: true })

    render(<DjControlSurface />)

    expect(screen.getByText("--.--")).toBeInTheDocument()
    expect(screen.getByText("Timeline missing")).toBeInTheDocument()
  })

  it("closes on Escape and restores focus", () => {
    render(<><button type="button" data-testid="before-dj">Before DJ</button><DjControlSurface /></>)
    const trigger = screen.getByTestId("before-dj")
    trigger.focus()
    act(() => useUIStore.getState().openDjSurface())
    expect(screen.getByTestId("close-dj-surface")).toHaveFocus()

    fireEvent.keyDown(document, { key: "Escape" })

    expect(useUIStore.getState().djSurfaceOpen).toBe(false)
    expect(trigger).toHaveFocus()
  })

  it("keeps controls bound to physical Deck A after the program role moves to B", () => {
    playback.getEngineSnapshot.mockReturnValue(snapshot("B"))
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    fireEvent.keyDown(screen.getByLabelText("Deck A gain"), { key: "ArrowUp" })

    expect(playback.setDeckTrim).toHaveBeenCalledWith("A", 0.51)
    expect(screen.getByLabelText("Make Deck A program")).toBeEnabled()
  })

  it("keeps every channel control inside the central mixer instead of the deck panels", () => {
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    const mixer = screen.getByRole("region", { name: "Mixer" })
    const deckA = screen.getByRole("region", { name: "Deck A" })
    expect(within(mixer).getByRole("slider", { name: "Deck A gain" })).toBeInTheDocument()
    expect(within(mixer).getByRole("slider", { name: "Deck B filter" })).toBeInTheDocument()
    expect(within(mixer).getByRole("slider", { name: "Crossfader" })).toBeInTheDocument()
    expect(within(deckA).queryByRole("slider", { name: "Deck A gain" })).not.toBeInTheDocument()
    expect(within(mixer).getByRole("slider", { name: "Crossfader" })).toHaveAttribute("aria-valuenow", "0")
  })

  it("places Deck B effect controls in the wide grid column before its edge label", () => {
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    const rack = screen.getByRole("region", { name: "Deck B effects" })
    expect(rack.firstElementChild).toHaveAttribute("data-part", "effect-controls")
    expect(rack).toHaveAttribute("data-side", "B")
  })

  it("keeps Traktor-style routing and pitch placeholders in their physical sections", () => {
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    const channelA = screen.getByRole("region", { name: "Deck A mixer channel" })
    const fxAssignment = within(channelA).getByRole("group", { name: "Deck A effect assignment" })
    expect(within(channelA).getByRole("combobox", { name: "Deck A filter type" })).toBeDisabled()
    expect(within(channelA).getByRole("button", { name: "Toggle Deck A filter" })).toBeDisabled()
    expect(within(channelA).getByRole("button", { name: "Mute Deck A high" })).toBeDisabled()
    expect(within(channelA).getByRole("button", { name: "Match Deck A key" })).toBeDisabled()
    expect(within(fxAssignment).getByRole("button", { name: "1" })).toBeDisabled()
    expect(within(fxAssignment).getByRole("button", { name: "2" })).toBeDisabled()
    const deckA = screen.getByRole("region", { name: "Deck A" })
    const deckB = screen.getByRole("region", { name: "Deck B" })
    expect(within(deckA).getByRole("slider", { name: "Deck A pitch" })).toBeDisabled()
    expect(within(deckB).getByRole("slider", { name: "Deck B pitch" })).toBeDisabled()
  })
})
