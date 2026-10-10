import { describe, expect, it } from "vitest"
import { prefetchTrackCount, type UserSettings } from "./settings"

function settings(patch: Partial<UserSettings>): UserSettings {
  return {
    language: "en",
    transcoding_enabled: false,
    transcoding_bitrate_kbps: 192,
    prefetch_ahead_enabled: false,
    prefetch_tracks: 3,
    ...patch,
  }
}

describe("prefetchTrackCount", () => {
  it("keeps only the next track while loading ahead is switched off", () => {
    expect(prefetchTrackCount(settings({ prefetch_tracks: 5 }))).toBe(1)
  })

  it("keeps the chosen number of tracks once it is switched on", () => {
    expect(prefetchTrackCount(settings({ prefetch_ahead_enabled: true, prefetch_tracks: 4 }))).toBe(4)
  })
})
