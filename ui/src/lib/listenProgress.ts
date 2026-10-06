/** Share of a track that must actually be played for it to count as a listen. */
export const LISTEN_FRACTION = 0.5
// Media time can run ahead of the wall clock only by timer jitter; a bigger
// jump between two samples is a seek and is not played time.
const MAX_AHEAD_OF_CLOCK_SECONDS = 1

/**
 * Counts how much of one play was actually played, for the listen threshold
 * (docs/social.md «Что такое прослушивание»). A listen is half the track
 * heard, not a position: seeking forward and skipping must not earn one.
 *
 * Fed with playback positions while playing; position gains that outrun the
 * wall clock (seeks) and losses (seeks back) are not counted. `rebase()` drops
 * the reference point — on pause/stop, so a seek made while paused is not
 * mistaken for playing. `start()` begins a new play (another queue item, the
 * same one restarted or repeated).
 */
export class ListenProgress {
  private key: string | null = null
  private played = 0
  private lastPosition: number | null = null
  private lastWallMs = 0
  private reported = false

  start(key: string | null): void {
    this.key = key
    this.played = 0
    this.lastPosition = null
    this.reported = false
  }

  rebase(): void {
    this.lastPosition = null
  }

  get playedSeconds(): number {
    return this.played
  }

  /** True exactly once per play: when the played time first reaches half the track. */
  sample(key: string, positionSeconds: number, durationSeconds: number, wallMs: number): boolean {
    if (key !== this.key) this.start(key)
    if (this.lastPosition !== null) {
      const gained = positionSeconds - this.lastPosition
      const elapsed = (wallMs - this.lastWallMs) / 1000
      if (gained > 0 && gained <= elapsed + MAX_AHEAD_OF_CLOCK_SECONDS) this.played += gained
    }
    this.lastPosition = positionSeconds
    this.lastWallMs = wallMs
    if (this.reported || !Number.isFinite(durationSeconds) || durationSeconds <= 0) return false
    if (this.played < durationSeconds * LISTEN_FRACTION) return false
    this.reported = true
    return true
  }
}
