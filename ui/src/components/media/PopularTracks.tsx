import { useState, useRef, useEffect, useCallback } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import TrackTable from "./TrackTable"
import type { ArtistTopTrack } from "@/api/types"

const PAGE_SIZE = 5
const SCROLL_DURATION = 600 // ms

function animateScroll(el: HTMLElement, from: number, to: number, duration: number) {
  const start = performance.now()
  function frame(now: number) {
    const t = Math.min((now - start) / duration, 1)
    const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t)
    el.scrollTop = from + (to - from) * eased
    if (t < 1) requestAnimationFrame(frame)
  }
  requestAnimationFrame(frame)
}

interface PopularTracksProps {
  tracks: ArtistTopTrack[]
  sourceLabel?: string
}

export default function PopularTracks({ tracks, sourceLabel }: PopularTracksProps) {
  const [page, setPage] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)
  const [containerH, setContainerH] = useState(Math.min(tracks.length, PAGE_SIZE) * 52)

  useEffect(() => {
    if (innerRef.current && tracks.length > 0) {
      const rowH = innerRef.current.scrollHeight / tracks.length
      setContainerH(rowH * Math.min(tracks.length, PAGE_SIZE))
    }
  }, [tracks.length])

  const totalPages = Math.ceil(tracks.length / PAGE_SIZE)
  const canPrev = page > 0
  const canNext = page < totalPages - 1

  const goTo = useCallback((next: number) => {
    const el = containerRef.current
    if (!el) { setPage(next); return }
    const rowH = el.scrollHeight / tracks.length
    animateScroll(el, el.scrollTop, next * PAGE_SIZE * rowH, SCROLL_DURATION)
    setPage(next)
  }, [tracks.length])

  if (tracks.length === 0) return null

  return (
    <section className="space-y-0">
      {totalPages > 1 && (
        <div className="px-4 sm:px-6 flex items-center justify-end gap-2 pb-1">
          <button
            onClick={() => canPrev && goTo(page - 1)}
            className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canPrev ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
            aria-label="Previous"
          >
            <ChevronLeft size={14} />
          </button>
          <button
            onClick={() => canNext && goTo(page + 1)}
            className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canNext ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
            aria-label="Next"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}
      <div
        ref={containerRef}
        style={{ height: containerH, overflow: "hidden" }}
        className="px-4 sm:px-6"
      >
        <div ref={innerRef}>
          <TrackTable tracks={tracks} showRelease sourceLabel={sourceLabel} />
        </div>
      </div>
    </section>
  )
}
