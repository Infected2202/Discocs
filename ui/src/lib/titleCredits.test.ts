import { describe, expect, it } from "vitest"
import type { TrackCredit } from "@/api/types"
import { splitTitleByCredits } from "./titleCredits"

function credit(id: number, text: string, role = "featured"): TrackCredit {
  return { id, name: text, role, text }
}

describe("splitTitleByCredits", () => {
  it("returns the whole title as one plain segment without credits", () => {
    expect(splitTitleByCredits("Night Drive", undefined)).toEqual([{ text: "Night Drive" }])
    expect(splitTitleByCredits("Night Drive", [])).toEqual([{ text: "Night Drive" }])
  })

  it("cuts a featured name out of the title in place", () => {
    const rlgn = credit(9, "RLGN")
    expect(splitTitleByCredits("Pi Pu Pa (ft. RLGN)", [rlgn])).toEqual([
      { text: "Pi Pu Pa (ft. " },
      { text: "RLGN", credit: rlgn },
      { text: ")" },
    ])
  })

  it("matches case-insensitively but keeps the title's own spelling", () => {
    const solomun = credit(3, "Solomun", "remixer")
    const segments = splitTitleByCredits("Track (SOLOMUN Remix)", [solomun])
    expect(segments[1]).toEqual({ text: "SOLOMUN", credit: solomun })
  })

  it("does not link a name found inside another word", () => {
    const ace = credit(5, "Ace", "remixer")
    const segments = splitTitleByCredits("Space (Ace Remix)", [ace])
    expect(segments.map((s) => s.text)).toEqual(["Space (", "Ace", " Remix)"])
  })

  it("links the last occurrence when the name repeats", () => {
    const space = credit(6, "Space", "remixer")
    const segments = splitTitleByCredits("Space (Space Remix)", [space])
    expect(segments).toEqual([
      { text: "Space (" },
      { text: "Space", credit: space },
      { text: " Remix)" },
    ])
  })

  it("links several credits without overlapping, longer names first", () => {
    const duo = credit(1, "Ray Keith & Nookie", "remixer")
    const nookie = credit(2, "Nookie", "remixer")
    const feat = credit(3, "MC Det")
    const segments = splitTitleByCredits("Tune (feat. MC Det) [Ray Keith & Nookie Remix]", [nookie, feat, duo])
    expect(segments.filter((s) => s.credit).map((s) => s.credit?.id)).toEqual([3, 1])
    expect(segments.map((s) => s.text).join("")).toBe("Tune (feat. MC Det) [Ray Keith & Nookie Remix]")
  })

  it("skips a credit whose name is not in the title", () => {
    const tagOnly = credit(4, "Tag Remixer", "remixer")
    expect(splitTitleByCredits("Plain", [tagOnly])).toEqual([{ text: "Plain" }])
  })
})
