import { act, fireEvent, render, screen, within } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { TimelineLoadState } from "@/api/timeline"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"

const playback = vi.hoisted(() => ({
  init: vi.fn(),
  getEngineSnapshot: vi.fn(),
  getDeckCurrentTime: vi.fn(() => 0),
  getMixerMeters: vi.fn(() => ({ A: 0.2, B: 0.1, master: 0.3 })),
  subscribeEngine: vi.fn(() => () => undefined),
  setDeckTrim: vi.fn(),
  setDeckEq: vi.fn(),
  setDeckFilter: vi.fn(),
  setDeckChannelFader: vi.fn(),
  setCrossfader: vi.fn(),
  setMasterGain: vi.fn(),
  setDeckTempo: vi.fn().mockResolvedValue(undefined),
  setAutoTempoMaster: vi.fn().mockResolvedValue(undefined),
  setClockTempoMaster: vi.fn().mockResolvedValue(undefined),
  setDeckTempoMaster: vi.fn().mockResolvedValue(undefined),
  setMasterClockTempo: vi.fn().mockResolvedValue(undefined),
  toggleDeckSync: vi.fn().mockResolvedValue(undefined),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  activateDjMode: vi.fn().mockResolvedValue(undefined),
  deactivateDjMode: vi.fn().mockResolvedValue(undefined),
  load: vi.fn(),
  play: vi.fn(),
  pause: vi.fn(),
  seek: vi.fn(),
}))
const useTimeline = vi.hoisted(() => vi.fn<() => TimelineLoadState>(() => ({ status: "missing" })))
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
        sourceKind: "signalsmith" as const,
        tempoMode: "pitch-preserving" as const,
        tempoRatio: 1,
        degradedReason: null,
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
        sourceKind: "media-element" as const,
        tempoMode: "native" as const,
        tempoRatio: 1,
        degradedReason: "Signalsmith unavailable",
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
    beatSync: {
      auto: true,
      master: "clock" as const,
      clockBpm: 126,
      decks: {
        A: { enabled: false, phase: "off" as const, reason: null },
        B: { enabled: false, phase: "off" as const, reason: null },
      },
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
    useTimeline.mockReset()
    useTimeline.mockReturnValue({ status: "missing" })
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
      // Полный микшер рендерится только при активном движке; выключенный движок
      // проверяется отдельным тестом (inactive preview).
      djEngineActive: true,
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

  it("shows and controls the standalone master clock", () => {
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

    expect(screen.getByLabelText("Master clock tempo")).toHaveValue(126)
    fireEvent.change(screen.getByLabelText("Master clock tempo"), { target: { value: "128.5" } })
    expect(playback.setMasterClockTempo).toHaveBeenCalledWith(128.5)
    fireEvent.click(screen.getByLabelText("Use automatic tempo master"))
    expect(playback.setAutoTempoMaster).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByLabelText("Use master clock"))
    expect(playback.setClockTempoMaster).toHaveBeenCalledOnce()
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

    expect(screen.getByLabelText("Master clock tempo")).toHaveValue(126)
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

  it("keeps pitch locked until a deck owns MASTER", () => {
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
    const pitchA = within(deckA).getByRole("slider", { name: "Deck A pitch" })
    expect(pitchA).toBeDisabled()
    expect(within(deckB).getByRole("slider", { name: "Deck B pitch" })).toBeDisabled()
    expect(playback.setDeckTempo).not.toHaveBeenCalled()
  })

  it("enables SYNC on every loaded deck and unlocks pitch only on MASTER", () => {
    const base = snapshot()
    playback.getEngineSnapshot.mockReturnValue({
      ...base,
      decks: {
        A: { ...base.decks.A, transport: "playing" as never },
        B: { ...base.decks.B, transport: "playing" as never, tempoRatio: 1.05 },
      },
      beatSync: {
        auto: true,
        master: "A",
        clockBpm: 126,
        decks: {
          A: { enabled: false, phase: "off", reason: null },
          B: { enabled: true, phase: "aligned", reason: null },
        },
      },
    })
    useTimeline.mockReturnValue({
      status: "ready",
      timeline: {
        durationSeconds: 180,
        levels: [],
        bpm: 120,
        beatConfidence: 0.9,
        rhythmCoverageSeconds: 180,
        beats: new Float32Array([0, 0.5, 1]),
        localTempo: new Float32Array([120, 120, 120]),
      },
    })
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    expect(screen.getByLabelText("Master clock tempo")).toBeDisabled()
    expect(screen.getByLabelText("Use master clock")).toBeDisabled()
    expect(screen.getByLabelText("Set Deck A as tempo master")).toHaveAttribute("data-active", "true")
    expect(screen.getByLabelText("Deck A pitch")).toBeEnabled()
    expect(screen.getByLabelText("Deck B pitch")).toBeDisabled()
    expect(screen.getByLabelText("Sync Deck A to tempo master")).toBeEnabled()
    expect(screen.getByLabelText("Sync Deck B to tempo master")).toHaveAttribute("data-enabled", "true")
    expect(screen.getByLabelText("Sync Deck B to tempo master")).toHaveAttribute("data-active", "true")
    expect(screen.getByLabelText("Deck B current tempo")).toHaveTextContent("126.00")
    expect(screen.getByLabelText("Deck B pitch")).toHaveAttribute("aria-valuenow", "0.625")
    expect(screen.getByLabelText("Set Deck B as tempo master")).toBeEnabled()

    fireEvent.click(screen.getByLabelText("Sync Deck A to tempo master"))
    fireEvent.click(screen.getByLabelText("Sync Deck B to tempo master"))
    fireEvent.keyDown(screen.getByLabelText("Deck A pitch"), { key: "End" })
    fireEvent.click(screen.getByLabelText("Set Deck B as tempo master"))
    fireEvent.click(screen.getByLabelText("Use automatic tempo master"))
    expect(playback.toggleDeckSync).toHaveBeenCalledWith("A")
    expect(playback.toggleDeckSync).toHaveBeenCalledWith("B")
    expect(playback.setDeckTempo).toHaveBeenCalledWith("A", 1.08)
    expect(playback.setDeckTempoMaster).toHaveBeenCalledWith("B")
    expect(playback.setAutoTempoMaster).toHaveBeenCalledOnce()
    expect(playback.setClockTempoMaster).not.toHaveBeenCalled()
  })

  it("shows an armed paused SYNC as blue text without the playing background", () => {
    const base = snapshot()
    playback.getEngineSnapshot.mockReturnValue({
      ...base,
      beatSync: {
        ...base.beatSync,
        master: "A",
        decks: {
          ...base.beatSync.decks,
          B: { enabled: true, phase: "pending", reason: null },
        },
      },
    })
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    const sync = screen.getByLabelText("Sync Deck B to tempo master")
    expect(sync).toHaveAttribute("data-enabled", "true")
    expect(sync).not.toHaveAttribute("data-active")
    expect(sync).toHaveAttribute("aria-pressed", "true")
  })

  it("hides the mixer and shows an activate button while the engine is off", () => {
    usePlayerStore.setState({ djEngineActive: false })
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    expect(screen.queryByRole("region", { name: "Mixer" })).not.toBeInTheDocument()

    const toggle = screen.getByTestId("toggle-dj-engine")
    expect(toggle).toHaveAttribute("aria-label", "Activate DJ")
    fireEvent.click(toggle)
    expect(playback.activateDjMode).toHaveBeenCalledOnce()
  })

  it("deactivates the engine from the panel without closing it", () => {
    useUIStore.setState({ djSurfaceOpen: true })
    render(<DjControlSurface />)

    const toggle = screen.getByTestId("toggle-dj-engine")
    expect(toggle).toHaveAttribute("aria-label", "Deactivate DJ")
    fireEvent.click(toggle)

    expect(playback.deactivateDjMode).toHaveBeenCalledOnce()
    // Панель остаётся открытой — деактивация не закрывает контролер.
    expect(useUIStore.getState().djSurfaceOpen).toBe(true)
    expect(screen.getByTestId("dj-control-surface")).toBeInTheDocument()
  })
})
