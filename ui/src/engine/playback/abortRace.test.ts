import { describe, expect, it, vi } from "vitest"
import { abortRace } from "./abortRace"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

describe("abortRace", () => {
  it("resolves with the original value when it settles before the signal aborts", async () => {
    const controller = new AbortController()
    const dispose = vi.fn()

    await expect(abortRace(Promise.resolve("done"), controller.signal, dispose)).resolves.toBe("done")
    expect(dispose).not.toHaveBeenCalled()
  })

  it("propagates the original rejection when it settles before the signal aborts", async () => {
    const controller = new AbortController()
    const dispose = vi.fn()
    const failure = new Error("worklet init failed")

    await expect(abortRace(Promise.reject(failure), controller.signal, dispose)).rejects.toBe(failure)
    expect(dispose).not.toHaveBeenCalled()
  })

  it("rejects with an AbortError as soon as the signal aborts, without waiting for the original promise", async () => {
    const controller = new AbortController()
    const work = deferred<void>()
    const dispose = vi.fn()

    const raced = abortRace(work.promise, controller.signal, dispose)
    controller.abort()

    await expect(raced).rejects.toMatchObject({ name: "AbortError" })
    expect(dispose).not.toHaveBeenCalled()
  })

  it("rejects immediately for an already-aborted signal", async () => {
    const controller = new AbortController()
    controller.abort()
    const dispose = vi.fn()

    await expect(abortRace(new Promise(() => undefined), controller.signal, dispose)).rejects.toMatchObject({
      name: "AbortError",
    })
  })

  it("disposes exactly once when the raced-away promise later resolves", async () => {
    const controller = new AbortController()
    const work = deferred<{ id: number }>()
    const dispose = vi.fn()

    const raced = abortRace(work.promise, controller.signal, dispose)
    controller.abort()
    await expect(raced).rejects.toMatchObject({ name: "AbortError" })

    work.resolve({ id: 1 })
    await work.promise
    await Promise.resolve()

    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it("disposes exactly once when the raced-away promise later rejects", async () => {
    const controller = new AbortController()
    const work = deferred<void>()
    const dispose = vi.fn()

    const raced = abortRace(work.promise, controller.signal, dispose)
    controller.abort()
    await expect(raced).rejects.toMatchObject({ name: "AbortError" })

    work.reject(new Error("late failure"))
    await work.promise.catch(() => undefined)
    await Promise.resolve()

    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it("never disposes when the promise wins the race outright", async () => {
    const controller = new AbortController()
    const work = deferred<void>()
    const dispose = vi.fn()

    const raced = abortRace(work.promise, controller.signal, dispose)
    work.resolve()
    await raced
    controller.abort()
    await Promise.resolve()

    expect(dispose).not.toHaveBeenCalled()
  })

  it("surfaces the signal's abort reason when it is an Error", async () => {
    const controller = new AbortController()
    const work = deferred<void>()
    const reason = new DOMException("Deck load timed out", "TimeoutError")

    const raced = abortRace(work.promise, controller.signal, vi.fn())
    controller.abort(reason)

    await expect(raced).rejects.toBe(reason)
  })
})
