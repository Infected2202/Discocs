import type { PlaybackCapabilities } from "./types"

export function detectPlaybackCapabilities(scope: typeof globalThis = globalThis): PlaybackCapabilities {
  const audioContext = "AudioContext" in scope
  const prototype = audioContext
    ? (scope.AudioContext as typeof AudioContext | undefined)?.prototype
    : undefined
  const mediaElementSource = typeof prototype?.createMediaElementSource === "function"
  const reasons: string[] = []
  if (!audioContext) reasons.push("AudioContext is unavailable")
  if (audioContext && !mediaElementSource) reasons.push("MediaElementAudioSourceNode is unavailable")
  return {
    webAudio: audioContext,
    mediaElementSource,
    ordinary: typeof scope.Audio === "function",
    manualMix: audioContext && mediaElementSource,
    reasons,
  }
}
