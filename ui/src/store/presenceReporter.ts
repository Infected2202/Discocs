import { playerPlayback } from "@/engine/playback"
import {
  PresenceReporter,
  setActivePresenceReporter,
  getActivePresenceReporter,
  type PresenceSnapshot,
  type PresenceTransport,
} from "@/lib/presence"
import { usePlayerStore } from "./playerStore"

type PlayerSnapshotSource = ReturnType<typeof usePlayerStore.getState>

function presenceSnapshot(state: PlayerSnapshotSource): PresenceSnapshot {
  return {
    trackId: state.currentTrackId,
    playbackState: state.playbackState,
    seekBuffering: state.seekBuffering,
    seekGeneration: state.seekGeneration,
    positionSeconds: state.currentTime,
    sessionId: state.session?.id ?? null,
    queueItemId: state.currentQueueItemId,
  }
}

/**
 * Start reporting the logged-in app player's state to `/playback/presence`.
 * Opt-in on purpose: only `AppShell` (behind `RequireAuth`) calls it, so the
 * guest `SharedPlayerPage` — which has its own <audio> and no session — never
 * reports anything. Returns the stop function (effect cleanup).
 */
export function startPresenceReporting(transport?: PresenceTransport): () => void {
  const reporter = new PresenceReporter(() => playerPlayback.currentTime, transport)
  reporter.observe(presenceSnapshot(usePlayerStore.getState()))
  const unsubscribe = usePlayerStore.subscribe((state) => reporter.observe(presenceSnapshot(state)))
  const onPageHide = () => reporter.pageHide()
  const onPageShow = (event: PageTransitionEvent) => {
    if (event.persisted) reporter.pageShow(presenceSnapshot(usePlayerStore.getState()))
  }
  globalThis.addEventListener("pagehide", onPageHide)
  globalThis.addEventListener("pageshow", onPageShow)
  setActivePresenceReporter(reporter)
  return () => {
    unsubscribe()
    globalThis.removeEventListener("pagehide", onPageHide)
    globalThis.removeEventListener("pageshow", onPageShow)
    if (getActivePresenceReporter() === reporter) setActivePresenceReporter(null)
  }
}
