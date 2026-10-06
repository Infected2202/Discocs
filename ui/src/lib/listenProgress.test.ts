import { describe, expect, it } from "vitest"
import { ListenProgress } from "./listenProgress"

/** Feed positions one wall-clock second apart, as steady playback would. */
function play(progress: ListenProgress, key: string, from: number, to: number, startMs = 0, duration = 200) {
  const fired: number[] = []
  for (let position = from; position <= to; position += 1) {
    if (progress.sample(key, position, duration, startMs + (position - from) * 1000)) fired.push(position)
  }
  return fired
}

describe("ListenProgress", () => {
  it("fires once, when half the track has actually been played", () => {
    const progress = new ListenProgress()

    expect(play(progress, "q1", 0, 150)).toEqual([100])
    expect(progress.playedSeconds).toBe(150)
  })

  it("does not count a seek forward as played time", () => {
    const progress = new ListenProgress()
    play(progress, "q1", 0, 10)

    // A jump to 190 s a quarter of a second later is a seek, not playback.
    expect(progress.sample("q1", 190, 200, 10_250)).toBe(false)
    expect(play(progress, "q1", 190, 200, 10_250)).toEqual([])
    expect(progress.playedSeconds).toBe(20)
  })

  it("does not count a seek made while paused", () => {
    const progress = new ListenProgress()
    play(progress, "q1", 0, 60)

    // Paused for ten minutes, sought to the end, resumed.
    progress.rebase()
    expect(progress.sample("q1", 195, 200, 660_000)).toBe(false)
    expect(progress.playedSeconds).toBe(60)
  })

  it("counts played time across seeks back and resumes", () => {
    const progress = new ListenProgress()
    play(progress, "q1", 0, 60)
    progress.rebase()

    // Replaying the first minute after a seek back counts: it was heard again.
    expect(play(progress, "q1", 0, 40, 100_000)).toEqual([40])
  })

  it("keeps counting when samples arrive rarely, as in a background tab", () => {
    const progress = new ListenProgress()
    let fired = false
    for (let position = 0; position <= 120; position += 10) {
      fired = progress.sample("q1", position, 200, position * 1000) || fired
    }
    expect(fired).toBe(true)
  })

  it("starts over for another play and for a restart", () => {
    const progress = new ListenProgress()
    play(progress, "q1", 0, 120)

    expect(play(progress, "q2", 0, 99)).toEqual([])
    progress.start(null)
    expect(play(progress, "q2", 0, 100)).toEqual([100])
  })

  it("waits for a known duration", () => {
    const progress = new ListenProgress()
    expect(progress.sample("q1", 0, Number.NaN, 0)).toBe(false)
    expect(progress.sample("q1", 1, Number.NaN, 1000)).toBe(false)
  })
})
