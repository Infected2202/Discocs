import { beforeEach, describe, expect, it, vi } from "vitest"
import { playerPlayback } from "@/engine/playback"
import { usePlayerStore } from "./playerStore"
import { useUIStore } from "./uiStore"
import { openDjPresentation } from "./playerPresentation"

describe("player presentation coordination", () => {
  beforeEach(() => {
    vi.spyOn(playerPlayback, "activateDjMode").mockResolvedValue(undefined)
    usePlayerStore.setState({ expanded: false })
    useUIStore.setState({ djSurfaceOpen: false })
  })

  it("opens the DJ panel without activating the engine or touching transport", () => {
    usePlayerStore.setState({ expanded: true, playbackState: "playing", currentTime: 42 })

    openDjPresentation()

    // Панель и движок — независимые оси: открытие панели НЕ запускает граф.
    expect(usePlayerStore.getState()).toMatchObject({ expanded: false, playbackState: "playing", currentTime: 42 })
    expect(useUIStore.getState().djSurfaceOpen).toBe(true)
    expect(playerPlayback.activateDjMode).not.toHaveBeenCalled()
  })

  it("opens Expanded Player and closes DJ workspace", () => {
    useUIStore.setState({ djSurfaceOpen: true })

    usePlayerStore.getState().toggleExpanded()

    expect(usePlayerStore.getState().expanded).toBe(true)
    expect(useUIStore.getState().djSurfaceOpen).toBe(false)
  })
})
