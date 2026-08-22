import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cancelAllBackgroundRetries, cancelBackgroundRetry, scheduleBackgroundRetry } from "./backgroundRetry"

describe("scheduleBackgroundRetry", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    cancelAllBackgroundRetries()
    vi.useRealTimers()
  })

  it("never schedules anything when the task succeeds immediately", async () => {
    const task = vi.fn().mockResolvedValue(undefined)
    scheduleBackgroundRetry("k", task)
    await vi.advanceTimersByTimeAsync(0)

    expect(task).toHaveBeenCalledTimes(1)

    // No interval was ever set up — advancing far past intervalMs must not
    // trigger a second call.
    await vi.advanceTimersByTimeAsync(60_000)
    expect(task).toHaveBeenCalledTimes(1)
  })

  it("retries on the interval until the task eventually succeeds", async () => {
    const task = vi.fn()
      .mockRejectedValueOnce(new Error("network down"))
      .mockRejectedValueOnce(new Error("network down"))
      .mockResolvedValueOnce(undefined)
    scheduleBackgroundRetry("k", task, 1_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(task).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(task).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(task).toHaveBeenCalledTimes(3)

    // It succeeded on the third attempt — the interval must now be cleared.
    await vi.advanceTimersByTimeAsync(10_000)
    expect(task).toHaveBeenCalledTimes(3)
  })

  it("keeps retrying indefinitely while the task keeps failing", async () => {
    const task = vi.fn().mockRejectedValue(new Error("still down"))
    scheduleBackgroundRetry("k", task, 1_000)
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(5_000)
    // 1 immediate + 5 interval ticks
    expect(task).toHaveBeenCalledTimes(6)
  })

  it("cancels the previous pending retry when called again under the same key", async () => {
    const task1 = vi.fn().mockRejectedValue(new Error("stale"))
    const task2 = vi.fn().mockResolvedValue(undefined)

    scheduleBackgroundRetry("k", task1, 1_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(task1).toHaveBeenCalledTimes(1)

    // A newer call for the same key supersedes task1 before its retry timer fires.
    scheduleBackgroundRetry("k", task2, 1_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(task2).toHaveBeenCalledTimes(1)

    // task1's old interval must not have survived — advancing well past its
    // would-be tick never calls it again.
    await vi.advanceTimersByTimeAsync(10_000)
    expect(task1).toHaveBeenCalledTimes(1)
    expect(task2).toHaveBeenCalledTimes(1)
  })

  it("ignores a late resolution from a superseded attempt", async () => {
    let resolveStale!: () => void
    const stale = new Promise<void>((resolve) => { resolveStale = resolve })
    const task1 = vi.fn().mockReturnValue(stale)
    const task2 = vi.fn().mockRejectedValue(new Error("also down"))

    scheduleBackgroundRetry("k", task1, 1_000)
    await vi.advanceTimersByTimeAsync(0)
    scheduleBackgroundRetry("k", task2, 1_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(task2).toHaveBeenCalledTimes(1)

    // The superseded first attempt finally resolves — must not clear task2's
    // still-pending retry loop.
    resolveStale()
    await vi.advanceTimersByTimeAsync(0)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(task2).toHaveBeenCalledTimes(2)
  })

  it("keys are independent of each other", async () => {
    const taskA = vi.fn().mockRejectedValue(new Error("a down"))
    const taskB = vi.fn().mockResolvedValue(undefined)

    scheduleBackgroundRetry("a", taskA, 1_000)
    scheduleBackgroundRetry("b", taskB, 1_000)
    await vi.advanceTimersByTimeAsync(0)

    expect(taskA).toHaveBeenCalledTimes(1)
    expect(taskB).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(taskA).toHaveBeenCalledTimes(2)
    // b already succeeded, must not be retried
    expect(taskB).toHaveBeenCalledTimes(1)
  })

  it("cancelBackgroundRetry stops a pending retry loop", async () => {
    const task = vi.fn().mockRejectedValue(new Error("down"))
    scheduleBackgroundRetry("k", task, 1_000)
    await vi.advanceTimersByTimeAsync(0)
    expect(task).toHaveBeenCalledTimes(1)

    cancelBackgroundRetry("k")
    await vi.advanceTimersByTimeAsync(10_000)
    expect(task).toHaveBeenCalledTimes(1)
  })

  it("cancelAllBackgroundRetries stops every pending retry loop", async () => {
    const taskA = vi.fn().mockRejectedValue(new Error("a down"))
    const taskB = vi.fn().mockRejectedValue(new Error("b down"))
    scheduleBackgroundRetry("a", taskA, 1_000)
    scheduleBackgroundRetry("b", taskB, 1_000)
    await vi.advanceTimersByTimeAsync(0)

    cancelAllBackgroundRetries()
    await vi.advanceTimersByTimeAsync(10_000)

    expect(taskA).toHaveBeenCalledTimes(1)
    expect(taskB).toHaveBeenCalledTimes(1)
  })
})
