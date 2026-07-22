// Shared helper for racing an in-flight async operation against an
// AbortSignal. Plain `Promise.race` is not enough here: once the signal
// wins, the original promise is still running (a worklet RPC in flight,
// say) and will eventually settle on its own -- if nothing disposes
// whatever it produces, that's a leak (an undisconnected node, an open
// MessagePort). `dispose` is the fire-and-forget cleanup for exactly that
// "settled late, after we already gave up" case; it is never invoked if the
// original promise wins the race outright.
export function abortRace<T>(
  promise: Promise<T>,
  signal: AbortSignal,
  dispose: () => void,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false

    const disposeLate = () => {
      // Nothing is awaiting `promise` anymore -- we already rejected for
      // abort below. Swallow whatever it settles with (value or error) and
      // just run the caller's cleanup; a disposal failure must not surface
      // as an unhandled rejection this far from where anyone could react.
      try {
        dispose()
      } catch {
        // ignore
      }
    }

    const onAbort = () => {
      if (settled) return
      settled = true
      signal.removeEventListener("abort", onAbort)
      reject(toAbortError(signal))
      void promise.then(disposeLate, disposeLate)
    }

    if (signal.aborted) {
      onAbort()
      return
    }

    signal.addEventListener("abort", onAbort, { once: true })

    promise.then(
      (value) => {
        if (settled) return
        settled = true
        signal.removeEventListener("abort", onAbort)
        resolve(value)
      },
      (error: unknown) => {
        if (settled) return
        settled = true
        signal.removeEventListener("abort", onAbort)
        reject(error)
      },
    )
  })
}

function toAbortError(signal: AbortSignal): Error {
  const reason = (signal as { reason?: unknown }).reason
  // jsdom's DOMException does not extend Error, so an explicit instanceof
  // check against Error alone misses reasons built with `new DOMException(...)`.
  if (reason instanceof Error || reason instanceof DOMException) return reason as Error
  return new DOMException("Aborted", "AbortError")
}
