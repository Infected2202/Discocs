import { useRef, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from "react"
import { cn } from "@/lib/utils"
import styles from "./DjControlSurface.module.css"

interface DjKnobProps {
  readonly label: string
  readonly displayLabel?: string
  readonly labelContent?: ReactNode
  readonly labelAccessory?: ReactNode
  readonly value: number
  readonly min?: number
  readonly max?: number
  readonly defaultValue?: number
  readonly disabled?: boolean
  readonly className?: string
  readonly onChange?: (value: number) => void
  readonly onCommit?: (value: number) => void
}

interface KnobArcStyle extends CSSProperties {
  "--knob-arc-start": string
  "--knob-arc-end": string
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function visualPosition(value: number, min: number, max: number, neutral: number): number {
  if (max === min) return 0
  const safeValue = clamp(value, min, max)
  const safeNeutral = clamp(neutral, min, max)

  // DJ controls put their neutral (usually 0 dB) at twelve o'clock even when
  // the underlying audio range is asymmetric, such as -24 dB…+6 dB EQ.
  if (safeNeutral > min && safeNeutral < max) {
    if (safeValue <= safeNeutral) return ((safeValue - min) / (safeNeutral - min)) * 0.5
    return 0.5 + ((safeValue - safeNeutral) / (max - safeNeutral)) * 0.5
  }

  return (safeValue - min) / (max - min)
}

export default function DjKnob({
  label,
  displayLabel,
  labelContent,
  labelAccessory,
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
  const position = visualPosition(value, min, max, defaultValue)
  const neutralPosition = visualPosition(defaultValue, min, max, defaultValue)
  const rotation = -135 + position * 270
  const arcStart = Math.min(position, neutralPosition) * 270
  const arcEnd = Math.max(position, neutralPosition) * 270
  const arcStyle: KnobArcStyle = {
    "--knob-arc-start": `${arcStart}deg`,
    "--knob-arc-end": `${arcEnd}deg`,
  }

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
        <span className={styles.knobArc} style={arcStyle} data-testid="knob-value-arc" />
        <span className={styles.knobCap} style={{ transform: `rotate(${rotation}deg)` }} data-testid="knob-pointer">
          <span className={styles.knobMark} />
        </span>
      </button>
      <div className={styles.controlLabelRow}>
        {labelContent ?? <span className={styles.controlLabel}>{displayLabel ?? label}</span>}
        {labelAccessory}
      </div>
    </div>
  )
}
