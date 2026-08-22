interface RetryEntry {
  timer: ReturnType<typeof setInterval> | null
  generation: number
}

const registry = new Map<string, RetryEntry>()

/**
 * Run `task` immediately. If it throws, keep retrying it on a plain
 * `intervalMs` timer — not tied to browser online/offline events, since a
 * VPN tunnel drop may never fire those — until it succeeds or a newer call
 * under the same `key` supersedes it. Never throws into the caller and never
 * blocks it: `task()` is only kicked off, not awaited.
 *
 * Keyed and coalescing: a second call with the same `key` cancels whatever
 * retry loop is currently pending for that key. Only the latest task under a
 * key matters for this use case (e.g. rapid queue jumps) — retrying a
 * superseded attempt after the fact would be wrong.
 */
export function scheduleBackgroundRetry(
  key: string,
  task: () => Promise<void>,
  intervalMs = 15_000,
): void {
  const previous = registry.get(key)
  if (previous?.timer != null) clearInterval(previous.timer)

  const entry: RetryEntry = { timer: null, generation: (previous?.generation ?? 0) + 1 }
  registry.set(key, entry)
  const generation = entry.generation
  const isCurrent = () => registry.get(key)?.generation === generation

  const attempt = () => {
    task().then(
      () => {
        if (!isCurrent()) return
        const current = registry.get(key)
        if (current?.timer != null) clearInterval(current.timer)
        registry.delete(key)
      },
      () => {
        if (!isCurrent()) return
        const current = registry.get(key)
        if (current && current.timer == null) {
          current.timer = setInterval(attempt, intervalMs)
        }
      },
    )
  }

  attempt()
}

/** Cancel a pending background retry loop for `key`. No-op if none is pending. */
export function cancelBackgroundRetry(key: string): void {
  const entry = registry.get(key)
  if (entry?.timer != null) clearInterval(entry.timer)
  registry.delete(key)
}

/** Cancel every pending background retry loop, e.g. on logout. */
export function cancelAllBackgroundRetries(): void {
  for (const entry of registry.values()) {
    if (entry.timer != null) clearInterval(entry.timer)
  }
  registry.clear()
}
