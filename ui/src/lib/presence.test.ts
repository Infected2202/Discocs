import { describe, expect, it, vi } from "vitest"
import { ApiError } from "@/api/client"
import {
  PresenceReporter,
  PresenceTracker,
  setActivePresenceReporter,
  stopActivePresence,
  toPositionMs,
  type PresenceSnapshot,
  type PresenceTransport,
} from "./presence"

function snap(overrides: Partial<PresenceSnapshot> = {}): PresenceSnapshot {
  return {
    trackId: 7,
    playbackState: "playing",
    seekBuffering: false,
    seekGeneration: 0,
    positionSeconds: 0,
    ...overrides,
  }
}

const at = (seconds: number) => () => seconds

describe("PresenceTracker", () => {
  it("carries the session and queue item, and stops the same ones after the queue moves on", () => {
    const tracker = new PresenceTracker()

    expect(tracker.next(snap({ sessionId: "s1", queueItemId: "q1" }), at(0))).toEqual({
      track_id: 7, state: "starting", position_ms: 0, session_id: "s1", queue_item_id: "q1",
    })
    expect(tracker.stop(at(12))).toEqual({
      track_id: 7, state: "stopped", position_ms: 12_000, session_id: "s1", queue_item_id: "q1",
    })
    // Without a session (guest/legacy) the fields are simply absent.
    const bare = new PresenceTracker().next(snap(), at(0))
    expect(bare).not.toHaveProperty("session_id")
    expect(bare).not.toHaveProperty("queue_item_id")
  })

  it("reports starting, paused, resumed playing and stopped for one track", () => {
    const tracker = new PresenceTracker()

    expect(tracker.next(snap({ playbackState: "loading" }), at(0))).toBeNull()
    expect(tracker.next(snap(), at(0.2))).toEqual({ track_id: 7, state: "starting", position_ms: 200 })
    expect(tracker.next(snap({ playbackState: "paused" }), at(30))).toEqual({
      track_id: 7, state: "paused", position_ms: 30_000,
    })
    expect(tracker.next(snap(), at(30))).toEqual({ track_id: 7, state: "playing", position_ms: 30_000 })
    expect(tracker.next(snap({ playbackState: "idle" }), at(200))).toEqual({
      track_id: 7, state: "stopped", position_ms: 200_000,
    })
  })

  it("dedupes identical consecutive states (progress ticks, repeated pauses)", () => {
    const tracker = new PresenceTracker()
    tracker.next(snap(), at(0))

    expect(tracker.next(snap({ positionSeconds: 5 }), at(5))).toBeNull()
    expect(tracker.next(snap({ positionSeconds: 6 }), at(6))).toBeNull()
    expect(tracker.next(snap({ playbackState: "paused" }), at(6))?.state).toBe("paused")
    expect(tracker.next(snap({ playbackState: "paused" }), at(6))).toBeNull()
    expect(tracker.next(snap({ playbackState: "idle" }), at(6))?.state).toBe("stopped")
    expect(tracker.next(snap({ playbackState: "idle" }), at(6))).toBeNull()
    expect(tracker.next(snap({ trackId: null, playbackState: "idle" }), at(0))).toBeNull()
  })

  it("reports playing with the seek target, even though playing was already reported", () => {
    const tracker = new PresenceTracker()
    tracker.next(snap(), at(0))

    expect(tracker.next(snap({ seekGeneration: 1, positionSeconds: 95.5 }), at(10))).toEqual({
      track_id: 7, state: "playing", position_ms: 95_500,
    })
    // A second seek to the same place is still a seek.
    expect(tracker.next(snap({ seekGeneration: 2, positionSeconds: 95.5 }), at(10))?.position_ms).toBe(95_500)
  })

  it("keeps a paused state but moves its position on seek while paused", () => {
    const tracker = new PresenceTracker()
    tracker.next(snap(), at(0))
    tracker.next(snap({ playbackState: "paused" }), at(10))

    expect(
      tracker.next(snap({ playbackState: "paused", seekGeneration: 1, positionSeconds: 60 }), at(10)),
    ).toEqual({ track_id: 7, state: "paused", position_ms: 60_000 })
  })

  it("ignores the engine pause while a seek buffers", () => {
    const tracker = new PresenceTracker()
    tracker.next(snap(), at(0))
    tracker.next(snap({ seekGeneration: 1, seekBuffering: true, positionSeconds: 120 }), at(0))

    expect(tracker.next(snap({ playbackState: "paused", seekGeneration: 1, seekBuffering: true }), at(0))).toBeNull()
    expect(tracker.next(snap({ seekGeneration: 1 }), at(120))).toBeNull()
  })

  it("reports a new track as starting at 0 even while the engine still plays the old one", () => {
    const tracker = new PresenceTracker()
    tracker.next(snap(), at(0))

    expect(tracker.next(snap({ trackId: 8 }), at(170))).toEqual({ track_id: 8, state: "starting", position_ms: 0 })
    // load → play of the same new track is not a second start.
    expect(tracker.next(snap({ trackId: 8, playbackState: "loading" }), at(0))).toBeNull()
    expect(tracker.next(snap({ trackId: 8 }), at(0.1))).toBeNull()
  })

  it("stays silent for a restored-but-never-played track", () => {
    const tracker = new PresenceTracker()

    expect(tracker.next(snap({ playbackState: "idle" }), at(42))).toBeNull()
    expect(tracker.next(snap({ playbackState: "paused" }), at(42))).toBeNull()
    expect(tracker.stop(at(42))).toBeNull()
    // First real play resumes from the restored position.
    expect(tracker.next(snap(), at(42))).toEqual({ track_id: 7, state: "starting", position_ms: 42_000 })
  })

  it("stops the last reported track when the player errors out", () => {
    const tracker = new PresenceTracker()
    tracker.next(snap(), at(0))

    expect(tracker.next(snap({ trackId: 9, playbackState: "error" }), at(0))?.track_id).toBe(7)
  })

  it("converts positions to non-negative integer milliseconds", () => {
    expect(toPositionMs(1.2346)).toBe(1235)
    expect(toPositionMs(-3)).toBe(0)
    expect(toPositionMs(Number.NaN)).toBe(0)
    expect(toPositionMs(Number.POSITIVE_INFINITY)).toBe(0)
  })
})

function transport(
  send: PresenceTransport["send"] = () => Promise.resolve({ status: "ok" }),
  beacon: PresenceTransport["beacon"] = () => true,
) {
  return {
    send: vi.fn<PresenceTransport["send"]>(send),
    beacon: vi.fn<PresenceTransport["beacon"]>(beacon),
  }
}

describe("PresenceReporter", () => {
  it("sends each transition once", () => {
    const t = transport()
    const reporter = new PresenceReporter(() => 0, t)

    reporter.observe(snap())
    reporter.observe(snap())
    reporter.observe(snap({ playbackState: "paused" }))

    expect(t.send.mock.calls.map(([report]) => report.state)).toEqual(["starting", "paused"])
  })

  it("swallows send failures, synchronous throws and tracker-free noise", async () => {
    const rejecting = transport(() => Promise.reject(new TypeError("offline")))
    const reporter = new PresenceReporter(() => 0, rejecting)
    expect(() => reporter.observe(snap())).not.toThrow()
    await Promise.resolve()

    const throwing = transport(() => { throw new Error("boom") })
    const other = new PresenceReporter(() => 0, throwing)
    expect(() => other.observe(snap())).not.toThrow()
    await expect(other.stopNow()).resolves.toBeUndefined()
  })

  it("stops trying after a 403 (no user behind the session)", async () => {
    const t = transport(() => Promise.reject(new ApiError(403, "forbidden", "no user")))
    const reporter = new PresenceReporter(() => 0, t)

    reporter.observe(snap())
    await new Promise((resolve) => setTimeout(resolve, 0))
    reporter.observe(snap({ playbackState: "paused" }))

    expect(t.send).toHaveBeenCalledTimes(1)
  })

  it("beacons stopped on pagehide and falls back to a keepalive fetch", () => {
    const t = transport(undefined, () => false)
    const reporter = new PresenceReporter(() => 12, t)
    reporter.observe(snap())
    t.send.mockClear()

    reporter.pageHide()

    expect(t.beacon).toHaveBeenCalledWith({ track_id: 7, state: "stopped", position_ms: 12_000 })
    expect(t.send).toHaveBeenCalledWith({ track_id: 7, state: "stopped", position_ms: 12_000 }, { keepalive: true })
    // Nothing more to stop.
    reporter.pageHide()
    expect(t.beacon).toHaveBeenCalledTimes(1)
  })

  it("does not beacon when nothing was playing", () => {
    const t = transport()
    const reporter = new PresenceReporter(() => 0, t)

    reporter.pageHide()

    expect(t.beacon).not.toHaveBeenCalled()
    expect(t.send).not.toHaveBeenCalled()
  })

  it("reports afresh after a back/forward-cache restore", () => {
    const t = transport()
    const reporter = new PresenceReporter(() => 50, t)
    reporter.observe(snap())
    reporter.pageHide()
    t.send.mockClear()

    reporter.pageShow(snap())

    expect(t.send).toHaveBeenCalledWith({ track_id: 7, state: "starting", position_ms: 50_000 }, undefined)
  })

  it("stopNow waits for the stopped report", async () => {
    let resolveSend: () => void = () => undefined
    const t = transport(() => new Promise<void>((resolve) => { resolveSend = resolve }))
    const reporter = new PresenceReporter(() => 0, t)
    reporter.observe(snap())
    resolveSend()

    let done = false
    const pending = reporter.stopNow().then(() => { done = true })
    await Promise.resolve()
    expect(done).toBe(false)
    expect(t.send).toHaveBeenLastCalledWith({ track_id: 7, state: "stopped", position_ms: 0 }, undefined)
    resolveSend()
    await pending
    expect(done).toBe(true)
  })
})

describe("stopActivePresence", () => {
  it("resolves without a reporter", async () => {
    setActivePresenceReporter(null)
    await expect(stopActivePresence()).resolves.toBeUndefined()
  })

  it("never lets a hanging report block logout", async () => {
    const t = transport(() => new Promise(() => undefined))
    const reporter = new PresenceReporter(() => 0, t)
    reporter.observe(snap())
    setActivePresenceReporter(reporter)
    try {
      await expect(stopActivePresence(10)).resolves.toBeUndefined()
      expect(t.send).toHaveBeenLastCalledWith({ track_id: 7, state: "stopped", position_ms: 0 }, undefined)
    } finally {
      setActivePresenceReporter(null)
    }
  })
})
