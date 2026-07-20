import { useRef, type KeyboardEvent, type PointerEvent } from "react"
import { cn } from "@/lib/utils"
import styles from "./DjControlSurface.module.css"

interface DjKnobProps {
  readonly label: string
  readonly value: number
  readonly min?: number
  readonly max?: number
  readonly defaultValue?: number
  readonly disabled?: boolean
  readonly className?: string
  readonly onChange?(value: number): void
  readonly onCommit?(value: number): void
}
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export default function DjKnob({
  label,
  value,
  min = 0,
  max = 1,
  defaultValue = min,
  disabled = false,
  className,
  onChange,
  onCommit,
}: DjKnobProps) {
  const drag = useRef<{ pointerId: number; startY: number; startValue: number } | null>(null)
  const span = max - min
  const normalized = span === 0 ? 0 : (clamp(value, min, max) - min) / span
  const rotation = -135 + normalized * 270

  function update(next: number, commit = false) {
    const clamped = clamp(next, min, max)
    onChange?.(clamped)
    if (commit) onCommit?.(clamped)
  }

  function handlePointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (disabled) return
    event.preventDefault()
    event.currentTarget.setPointerCapture?.(event.pointerId)
    drag.current = { pointerId: event.pointerId, startY: event.clientY, startValue: value }
  }

  function handlePointerMove(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current
    if (!active || active.pointerId !== event.pointerId) return
    update(active.startValue + ((active.startY - event.clientY) / 120) * span)
  }

  function handlePointerUp(event: PointerEvent<HTMLButtonElement>) {
    const active = drag.current
    if (!active || active.pointerId !== event.pointerId) return
    const next = active.startValue + ((active.startY - event.clientY) / 120) * span
    drag.current = null
    update(next, true)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return
    const step = span / 100
    if (event.key === "ArrowUp" || event.key === "ArrowRight") {
      event.preventDefault()
      update(value + step, true)
    } else if (event.key === "ArrowDown" || event.key === "ArrowLeft") {
      event.preventDefault()
      update(value - step, true)
    } else if (event.key === "Home") {
      event.preventDefault()
      update(min, true)
    } else if (event.key === "End") {
      event.preventDefault()
      update(max, true)
    }
  }

  return (
    <div className={cn(styles.knobGroup, className)}>
      <button
        type="button"
        role="slider"
        aria-label={label}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={Number(value.toFixed(3))}
        disabled={disabled}
        className={styles.knob}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => { drag.current = null }}
        onDoubleClick={() => update(defaultValue, true)}
        onKeyDown={handleKeyDown}
      >
        <span className={styles.knobArc} />
        <span className={styles.knobCap} style={{ transform: `rotate(${rotation}deg)` }}>
          <span className={styles.knobMark} />
        </span>
      </button>
      <span className={styles.controlLabel}>{label}</span>
    </div>
  )
}
