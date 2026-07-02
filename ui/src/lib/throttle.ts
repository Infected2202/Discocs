// Ограничивает частоту вызовов fn до одного раза в intervalMs.
// Leading + trailing: первый вызов проходит сразу (моментальный отклик),
// а последний вызов внутри окна гарантированно исполняется в конце окна —
// чтобы не потерять финальное значение (напр. позицию трека перед паузой).
export function throttle<A extends unknown[]>(
  fn: (...args: A) => void,
  intervalMs: number
): ((...args: A) => void) & { cancel(): void } {
  let last = 0
  let timer: ReturnType<typeof setTimeout> | null = null
  let pendingArgs: A | null = null

  const invoke = (args: A, now: number) => {
    last = now
    fn(...args)
  }

  const throttled = (...args: A) => {
    const now = Date.now()
    const remaining = intervalMs - (now - last)

    if (remaining <= 0) {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      invoke(args, now)
      return
    }

    // Ещё внутри окна — запоминаем последние аргументы и планируем trailing-вызов.
    pendingArgs = args
    if (!timer) {
      timer = setTimeout(() => {
        timer = null
        if (pendingArgs) {
          invoke(pendingArgs, Date.now())
          pendingArgs = null
        }
      }, remaining)
    }
  }

  throttled.cancel = () => {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    pendingArgs = null
  }

  return throttled
}
