import type { KeyboardEvent, PointerEvent } from "react"
import { cn } from "@/lib/utils"
import styles from "./DjControlSurface.module.css"

interface DjFaderProps {
  readonly label: string
  readonly value: number
  readonly min?: number
  readonly max?: number
  readonly orientation?: "vertical" | "horizontal"
  readonly disabled?: boolean
  readonly className?: string
  readonly onChange?: (value: number) => void
  readonly onCommit?: (value: number) => void
}
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export default function DjFader({
  label,
  value,
  min = 0,
  max = 1,
  orientation = "vertical",
  disabled = false,
  className,
  onChange,
  onCommit,
}: DjFaderProps) {
  const span = max - min
  const normalized = span === 0 ? 0 : (clamp(value, min, max) - min) / span

  function valueFromPointer(event: PointerEvent<HTMLButtonElement>): number {
    const rect = event.currentTarget.getBoundingClientRect()
    const ratio = orientation === "vertical"
      ? 1 - (event.clientY - rect.top) / Math.max(1, rect.height)
      : (event.clientX - rect.left) / Math.max(1, rect.width)
    return min + clamp(ratio, 0, 1) * span
  }

  function handlePointer(event: PointerEvent<HTMLButtonElement>, commit = false) {
    if (disabled) return
    event.preventDefault()
    if (event.type === "pointerdown") event.currentTarget.setPointerCapture?.(event.pointerId)
    const next = valueFromPointer(event)
    onChange?.(next)
    if (commit) onCommit?.(next)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return
    const step = span / 100
    let next: number | null = null
    if (event.key === "ArrowUp" || event.key === "ArrowRight") next = value + step
    if (event.key === "ArrowDown" || event.key === "ArrowLeft") next = value - step
    if (event.key === "Home") next = min
    if (event.key === "End") next = max
    if (next === null) return
    event.preventDefault()
    const clamped = clamp(next, min, max)
    onChange?.(clamped)
    onCommit?.(clamped)
  }

  const position = `${normalized * 100}%`
  return (
    <div className={cn(styles.faderGroup, orientation === "horizontal" && styles.faderGroupHorizontal, className)}>
      <button
        type="button"
        role="slider"
        aria-label={label}
        aria-orientation={orientation}
        aria-valuemin={min}
        aria-valuemax={max}
        aria-valuenow={Number(value.toFixed(3))}
        disabled={disabled}
        className={cn(styles.fader, orientation === "horizontal" && styles.faderHorizontal)}
        onPointerDown={(event) => handlePointer(event)}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture?.(event.pointerId)) handlePointer(event)
        }}
        onPointerUp={(event) => handlePointer(event, true)}
        onKeyDown={handleKeyDown}
      >
        <span className={styles.faderScale} />
        <span className={styles.faderTrack} />
        <span
          className={styles.faderThumb}
          style={orientation === "vertical" ? { bottom: position } : { left: position }}
        >
          <span />
        </span>
      </button>
      <span className={styles.controlLabel}>{label}</span>
    </div>
  )
}
