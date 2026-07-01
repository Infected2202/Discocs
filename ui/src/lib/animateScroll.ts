export function animateScroll(
  el: HTMLElement,
  from: number,
  to: number,
  duration: number,
  axis: "x" | "y" = "y",
  onDone?: () => void,
) {
  const start = performance.now()
  function frame(now: number) {
    const t = Math.min((now - start) / duration, 1)
    const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t)
    const value = from + (to - from) * eased
    if (axis === "x") el.scrollLeft = value
    else el.scrollTop = value
    if (t < 1) requestAnimationFrame(frame)
    else onDone?.()
  }
  requestAnimationFrame(frame)
}
