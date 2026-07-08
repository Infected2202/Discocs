import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { playerLog } from "./playerLogger"

describe("playerLog", () => {
  const debug = vi.spyOn(console, "debug").mockImplementation(() => {})

  beforeEach(() => {
    localStorage.clear()
    debug.mockClear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it("logs the message and payload when debug logging is enabled", () => {
    localStorage.setItem("discocs.debug", "1")

    playerLog("queue", "skip", { trackId: 7 })

    expect(debug).toHaveBeenCalledWith("[player:queue] skip", { trackId: 7 })
  })

  it("logs only the message when no payload is provided", () => {
    localStorage.setItem("discocs.debug", "1")

    playerLog("queue", "resume")

    expect(debug).toHaveBeenCalledWith("[player:queue] resume")
  })
})
