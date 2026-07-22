import { describe, expect, it, vi } from "vitest"
import { TempoSyncController } from "./TempoSyncController"

describe("TempoSyncController", () => {
  it("waits for an in-flight duplicate transport fact before reporting it complete", async () => {
    let finishHandoff!: () => void
    const handoff = new Promise<void>((resolve) => {
      finishHandoff = resolve
    })
    const alignFollower = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockReturnValueOnce(handoff)
    const controller = new TempoSyncController({ alignFollower })

    await controller.dispatch({ type: "deck-transport", deck: "A", transport: "playing" })
    await controller.dispatch({ type: "deck-capability", deck: "A", hasTrack: true, feasible: true, ready: true, bpm: 126 })
    await controller.dispatch({ type: "toggle-sync", deck: "A", mode: "beat" })
    await controller.dispatch({ type: "deck-capability", deck: "B", hasTrack: true, feasible: true, ready: true, bpm: 120 })
    await controller.dispatch({ type: "deck-transport", deck: "B", transport: "playing" })
    await controller.dispatch({ type: "toggle-sync", deck: "B", mode: "beat" })

    const runtimeNotification = controller.dispatch({ type: "deck-transport", deck: "A", transport: "paused" })
    let explicitDispatchFinished = false
    const explicitDispatch = controller
      .dispatch({ type: "deck-transport", deck: "A", transport: "paused" })
      .then((transition) => {
        explicitDispatchFinished = true
        return transition
      })

    await Promise.resolve()
    expect(explicitDispatchFinished).toBe(false)
    expect(controller.getState().followers.A.phase).toBe("pending")

    finishHandoff()
    await runtimeNotification
    const duplicateTransition = await explicitDispatch

    expect(duplicateTransition.effects).toEqual([])
    expect(alignFollower).toHaveBeenCalledTimes(2)
    expect(controller.getState().master).toBe("B")
    expect(controller.getState().followers.A.phase).toBe("aligned")
  })
})
