// Single funnel for playback-engine failures that have no user-facing surface
// (per SYNC_REWRITE_PLAN.md product decision #4: no toast/banner UI). DevTools
// console is the only place these are observable.
export function reportEngineFailure(context: string, error: unknown): void {
  console.error(`[PlaybackEngine] ${context}:`, error)
}
