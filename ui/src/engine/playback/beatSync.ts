export interface BeatTimeline {
  readonly bpm: number
  readonly beats: Float32Array
}

export interface BeatPosition {
  readonly interval: number
  readonly phase: number
}

export const MIN_TEMPO_RATIO = 0.92
export const MAX_TEMPO_RATIO = 1.08

export function tempoRatioFor(masterBpm: number, followerBpm: number): number | null {
  if (!Number.isFinite(masterBpm) || masterBpm <= 0 || !Number.isFinite(followerBpm) || followerBpm <= 0) {
    return null
  }
  const ratio = masterBpm / followerBpm
  return ratio >= MIN_TEMPO_RATIO && ratio <= MAX_TEMPO_RATIO ? ratio : null
}

export function locateBeat(beats: Float32Array, seconds: number): BeatPosition | null {
  if (beats.length < 2 || !Number.isFinite(seconds)) return null
  let low = 0
  let high = beats.length - 1
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2)
    if (beats[middle] <= seconds) low = middle
    else high = middle
  }
  const interval = seconds < beats[0] ? 0 : Math.min(low, beats.length - 2)
  const start = beats[interval]
  const end = beats[interval + 1]
  if (!(end > start)) return null
  return { interval, phase: Math.min(1, Math.max(0, (seconds - start) / (end - start))) }
}

export function alignFollowerPosition(
  masterBeats: Float32Array,
  masterSeconds: number,
  followerBeats: Float32Array,
  followerSeconds: number,
): number | null {
  const master = locateBeat(masterBeats, masterSeconds)
  if (!master || followerBeats.length < 2 || !Number.isFinite(followerSeconds)) return null

  let nearest = 0
  let distance = Number.POSITIVE_INFINITY
  for (let index = 0; index < followerBeats.length - 1; index += 1) {
    const start = followerBeats[index]
    const end = followerBeats[index + 1]
    if (!(end > start)) continue
    const candidate = start + master.phase * (end - start)
    const candidateDistance = Math.abs(candidate - followerSeconds)
    if (candidateDistance < distance) {
      distance = candidateDistance
      nearest = candidate
    }
  }
  return nearest
}

export function projectPlayhead(
  mediaSeconds: number,
  anchorAudioTime: number,
  rate: number,
  targetAudioTime: number,
): number {
  return mediaSeconds + Math.max(0, targetAudioTime - anchorAudioTime) * Math.max(0, rate)
}
