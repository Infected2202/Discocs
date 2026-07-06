import { useRef } from "react"
import { cn } from "@/lib/utils"

const ROTATE_AMPLITUDE = 9
const SCALE_ON_HOVER = 1.03

interface TiltedArtworkProps {
  readonly children: React.ReactNode
  readonly className?: string
}

/**
 * Tilt-on-hover wrapper for shelf artwork. Transform is written directly to the
 * DOM via ref on mousemove (no React state), so only the hovered card does any
 * work — the rest of a shelf with hundreds of covers stays fully static.
 */
export default function TiltedArtwork({ children, className }: TiltedArtworkProps) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)

  function handleMouseMove(e: React.MouseEvent<HTMLDivElement>) {
    const el = wrapperRef.current
    const inner = innerRef.current
    if (!el || !inner) return
    const rect = el.getBoundingClientRect()
    const offsetX = e.clientX - rect.left - rect.width / 2
    const offsetY = e.clientY - rect.top - rect.height / 2
    const rotateX = (-offsetY / (rect.height / 2)) * ROTATE_AMPLITUDE
    const rotateY = (offsetX / (rect.width / 2)) * ROTATE_AMPLITUDE
    inner.style.transform = `perspective(800px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale(${SCALE_ON_HOVER})`
  }

  function handleMouseLeave() {
    const inner = innerRef.current
    if (!inner) return
    inner.style.transform = "perspective(800px) rotateX(0deg) rotateY(0deg) scale(1)"
  }

  return (
    <div
      ref={wrapperRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      className={cn("[transform-style:preserve-3d]", className)}
    >
      <div
        ref={innerRef}
        className="transition-transform duration-300 ease-out will-change-transform"
      >
        {children}
      </div>
    </div>
  )
}
