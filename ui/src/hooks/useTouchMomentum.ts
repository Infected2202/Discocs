import { useCallback, useRef, type RefObject, type TouchEvent as ReactTouchEvent } from "react"

const MOMENTUM_PROJECTION_MS = 900
const MIN_VELOCITY_PX_PER_MS = 0.2
const MAX_SAMPLE_AGE_MS = 120

interface TouchSample {
  readonly x: number
  readonly time: number
  readonly velocity: number
}

export function projectMomentumTarget(
  scrollLeft: number,
  velocity: number,
  maxScrollLeft: number
): number {
  const projected = scrollLeft + velocity * MOMENTUM_PROJECTION_MS
  return Math.min(maxScrollLeft, Math.max(0, projected))
}

export function useTouchMomentum(ref: RefObject<HTMLElement | null>) {
  const sampleRef = useRef<TouchSample | null>(null)

  const onTouchStart = useCallback((event: ReactTouchEvent<HTMLElement>) => {
    const touch = event.touches[0]
    if (event.touches.length !== 1 || !touch) {
      sampleRef.current = null
      return
    }

    sampleRef.current = {
      x: touch.clientX,
      time: event.timeStamp,
      velocity: 0,
    }
  }, [])

  const onTouchMove = useCallback((event: ReactTouchEvent<HTMLElement>) => {
    const previous = sampleRef.current
    const touch = event.touches[0]
    if (event.touches.length !== 1 || !previous || !touch) {
      sampleRef.current = null
      return
    }

    const elapsed = event.timeStamp - previous.time
    if (elapsed <= 0) return

    const instantVelocity = (previous.x - touch.clientX) / elapsed
    sampleRef.current = {
      x: touch.clientX,
      time: event.timeStamp,
      velocity:
        previous.velocity === 0
          ? instantVelocity
          : previous.velocity * 0.25 + instantVelocity * 0.75,
    }
  }, [])

  const onTouchEnd = useCallback((event: ReactTouchEvent<HTMLElement>) => {
    const sample = sampleRef.current
    sampleRef.current = null

    const element = ref.current
    if (
      !element ||
      !sample ||
      event.timeStamp - sample.time > MAX_SAMPLE_AGE_MS ||
      Math.abs(sample.velocity) < MIN_VELOCITY_PX_PER_MS
    ) return

    const maxScrollLeft = Math.max(0, element.scrollWidth - element.clientWidth)
    const target = projectMomentumTarget(element.scrollLeft, sample.velocity, maxScrollLeft)
    if (Math.abs(target - element.scrollLeft) < 1) return

    element.scrollTo({ left: target, behavior: "smooth" })
  }, [ref])

  const onTouchCancel = useCallback(() => {
    sampleRef.current = null
  }, [])

  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel }
}
