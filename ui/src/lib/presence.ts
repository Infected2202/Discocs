import { ApiError } from "@/api/client"
import {
  beaconPresence,
  reportPresence,
  type PresenceReport,
  type PresenceState,
} from "@/api/playback"
import type { PlaybackState } from "@/engine/playback"

/**
 * "Now playing" presence for the social features (docs/social.md).
 *
 * The player never calls the presence API itself: it only changes its store.
 * `PresenceTracker` turns successive player snapshots into the few
 * transitions Navidrome cares about (starting / playing / paused / stopped),
 * `PresenceReporter` sends them fire-and-forget. Nothing here may throw into
 * the player or block it.
 */

export interface PresenceSnapshot {
  trackId: number | null
  playbackState: PlaybackState
  /** A seek is waiting for the track to buffer — the engine pauses meanwhile. */
  seekBuffering: boolean
  /** Bumped by every user seek (playerStore.seek). */
  seekGeneration: number
  /** Store position in seconds — right after a seek, the optimistic target. */
  positionSeconds: number
}

export function toPositionMs(seconds: number): number {
  return Number.isFinite(seconds) && seconds > 0 ? Math.round(seconds * 1000) : 0
}

function isActive(state: PresenceState): boolean {
  return state === "starting" || state === "playing"
}

export class PresenceTracker {
  private last: PresenceReport | null = null
  private observedTrackId: number | null | undefined = undefined
  private observedSeek: number | undefined = undefined

  /**
   * The report this snapshot calls for, or null when nothing changed for
   * presence. `livePositionSeconds` is read lazily — only when reporting.
   */
  next(snapshot: PresenceSnapshot, livePositionSeconds: () => number): PresenceReport | null {
    const trackChanged = this.observedTrackId !== undefined && snapshot.trackId !== this.observedTrackId
    const seeked = this.observedSeek !== undefined && snapshot.seekGeneration !== this.observedSeek
    this.observedTrackId = snapshot.trackId
    this.observedSeek = snapshot.seekGeneration
    const { trackId, playbackState } = snapshot

    if (trackId === null || playbackState === "idle" || playbackState === "error") {
      return this.stop(livePositionSeconds)
    }
    // Loading a track, or a seek pausing the engine while it buffers: the
    // user did not stop listening — report the outcome, not the transient.
    if (playbackState === "loading" || (snapshot.seekBuffering && !seeked)) return null

    // The engine still plays the previous track when the queue pointer moves
    // (skip while playing), so its position is meaningless for the new one.
    const position = () => (trackChanged ? 0 : toPositionMs(livePositionSeconds()))
    const last = this.last

    if (playbackState === "playing") {
      if (last === null || last.state === "stopped" || last.track_id !== trackId) {
        return this.emit({ track_id: trackId, state: "starting", position_ms: position() })
      }
      if (seeked) {
        return this.emit(
          { track_id: trackId, state: "playing", position_ms: toPositionMs(snapshot.positionSeconds) },
          true,
        )
      }
      if (last.state === "paused") return this.emit({ track_id: trackId, state: "playing", position_ms: position() })
      return null
    }

    // paused: only meaningful once this page has reported playback at all.
    if (last === null || last.state === "stopped") return null
    const pausedAt = seeked ? toPositionMs(snapshot.positionSeconds) : position()
    return this.emit({ track_id: trackId, state: "paused", position_ms: pausedAt }, seeked)
  }

  /** `stopped` for whatever was last reported as playing/paused, else null. */
  stop(livePositionSeconds: () => number = () => 0): PresenceReport | null {
    const last = this.last
    if (last === null || last.state === "stopped") return null
    const live = toPositionMs(livePositionSeconds())
    return this.emit({ track_id: last.track_id, state: "stopped", position_ms: live || last.position_ms })
  }

  /** Forget everything (e.g. page restored from the back/forward cache). */
  reset(): void {
    this.last = null
    this.observedTrackId = undefined
    this.observedSeek = undefined
  }

  get lastReport(): PresenceReport | null {
    return this.last
  }

  private emit(report: PresenceReport, force = false): PresenceReport | null {
    const last = this.last
    if (
      !force
      && last !== null
      && last.track_id === report.track_id
      && (last.state === report.state || (isActive(last.state) && isActive(report.state)))
    ) {
      return null
    }
    this.last = report
    return report
  }
}

export interface PresenceTransport {
  send(report: PresenceReport, init?: RequestInit): Promise<unknown>
  beacon(report: PresenceReport): boolean
}

// Bindings resolved per call, not at module load: importing this module must
// stay side-effect free (tests mock @/api/playback with partial factories).
const defaultTransport: PresenceTransport = {
  send: (report, init) => reportPresence(report, init),
  beacon: (report) => beaconPresence(report),
}

export class PresenceReporter {
  private readonly tracker = new PresenceTracker()
  /** 403 = no user behind this session (auth disabled / service): stop trying. */
  private disabled = false
  private readonly livePositionSeconds: () => number
  private readonly transport: PresenceTransport

  constructor(livePositionSeconds: () => number, transport: PresenceTransport = defaultTransport) {
    this.livePositionSeconds = livePositionSeconds
    this.transport = transport
  }

  observe(snapshot: PresenceSnapshot): void {
    if (this.disabled) return
    let report: PresenceReport | null
    try {
      report = this.tracker.next(snapshot, this.livePositionSeconds)
    } catch {
      return
    }
    if (report) void this.dispatch(report)
  }

  /** `pagehide`: the page is going away — beacon a final `stopped`. */
  pageHide(): void {
    if (this.disabled) return
    const report = this.tracker.stop(this.livePositionSeconds)
    if (!report) return
    let queued = false
    try {
      queued = this.transport.beacon(report)
    } catch {
      queued = false
    }
    if (!queued) void this.dispatch(report, { keepalive: true })
  }

  /** `pageshow` from the bfcache: the page lives again, report afresh. */
  pageShow(snapshot: PresenceSnapshot): void {
    this.tracker.reset()
    this.observe(snapshot)
  }

  /** Report `stopped` now and wait for it (logout: before the cookie is gone). */
  stopNow(): Promise<void> {
    if (this.disabled) return Promise.resolve()
    const report = this.tracker.stop(this.livePositionSeconds)
    return report ? this.dispatch(report) : Promise.resolve()
  }

  private dispatch(report: PresenceReport, init?: RequestInit): Promise<void> {
    try {
      return this.transport.send(report, init).then(
        () => undefined,
        (error: unknown) => {
          if (error instanceof ApiError && error.status === 403) this.disabled = true
        },
      )
    } catch {
      return Promise.resolve()
    }
  }
}

let activeReporter: PresenceReporter | null = null

export function setActivePresenceReporter(reporter: PresenceReporter | null): void {
  activeReporter = reporter
}

export function getActivePresenceReporter(): PresenceReporter | null {
  return activeReporter
}

/** How long logout may wait for the final `stopped` report. */
export const STOP_PRESENCE_TIMEOUT_MS = 1500

/** Best-effort `stopped` for the logged-in player; never rejects, never hangs. */
export function stopActivePresence(timeoutMs = STOP_PRESENCE_TIMEOUT_MS): Promise<void> {
  if (!activeReporter) return Promise.resolve()
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<void>((resolve) => { timer = setTimeout(resolve, timeoutMs) })
  return Promise.race([activeReporter.stopNow(), timeout]).finally(() => clearTimeout(timer))
}
