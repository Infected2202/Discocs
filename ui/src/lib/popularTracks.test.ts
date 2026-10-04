import { describe, expect, it } from "vitest"
import type { ArtistTopTrack } from "@/api/types"
import { POPULAR_TRACKS_LIMIT, pickPopularTracks } from "./popularTracks"

function track(id: number, play_count: number, deezer_rank: number | null = null): ArtistTopTrack {
  return { id, play_count, deezer_rank } as ArtistTopTrack
}

describe("pickPopularTracks", () => {
  it("keeps played tracks first and fills up with Deezer-ranked ones", () => {
    const items = [track(1, 3), track(2, 1), track(3, 0, 900), track(4, 0, 100), track(5, 0)]
    expect(pickPopularTracks(items).map((t) => t.id)).toEqual([1, 2, 3, 4])
  })

  it("caps Deezer-ranked tracks but never drops played ones", () => {
    const played = Array.from({ length: POPULAR_TRACKS_LIMIT + 2 }, (_, i) => track(i + 1, 1))
    const items = [...played, track(100, 0, 500)]
    expect(pickPopularTracks(items)).toHaveLength(POPULAR_TRACKS_LIMIT + 2)
    const ranked = Array.from({ length: 30 }, (_, i) => track(i + 1, 0, 1000 - i))
    expect(pickPopularTracks(ranked)).toHaveLength(POPULAR_TRACKS_LIMIT)
  })

  it("falls back to the first five tracks without any signal", () => {
    const items = Array.from({ length: 8 }, (_, i) => track(i + 1, 0))
    expect(pickPopularTracks(items).map((t) => t.id)).toEqual([1, 2, 3, 4, 5])
  })
})
