import { useState, useRef, useEffect, useLayoutEffect, useCallback } from "react"
import { useTranslation } from "react-i18next"
import { ChevronLeft, ChevronRight } from "lucide-react"
import VirtualTrackList from "./VirtualTrackList"
import { animateScroll } from "@/lib/animateScroll"
import type { ArtistTopTrack } from "@/api/types"

const PAGE_SIZE = 5
const SCROLL_DURATION = 600 // ms

interface PopularTracksProps {
  readonly tracks: ArtistTopTrack[]
  readonly sourceLabel?: string
}

export default function PopularTracks({ tracks, sourceLabel }: PopularTracksProps) {
  const { t } = useTranslation("media")
  const [page, setPage] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const innerRef = useRef<HTMLDivElement>(null)
  const [containerH, setContainerH] = useState<number | null>(null)
  const animating = useRef(false)

  const needsScroll = tracks.length > PAGE_SIZE
  const totalPages = Math.ceil(tracks.length / PAGE_SIZE)
  const canPrev = page > 0
  const canNext = page < totalPages - 1

  // Measure synchronously before paint to avoid flash
  useLayoutEffect(() => {
    if (!needsScroll || !innerRef.current || tracks.length === 0) return
    const rowH = innerRef.current.scrollHeight / tracks.length
    setContainerH(rowH * PAGE_SIZE)
  }, [tracks.length, needsScroll])

  const pageRef = useRef(page)
  pageRef.current = page
  const canNextRef = useRef(canNext)
  canNextRef.current = canNext
  const canPrevRef = useRef(canPrev)
  canPrevRef.current = canPrev

  const goTo = useCallback((next: number) => {
    if (animating.current) return
    const el = containerRef.current
    if (!el) { setPage(next); return }
    animating.current = true
    const rowH = el.scrollHeight / tracks.length
    animateScroll(el, el.scrollTop, next * PAGE_SIZE * rowH, SCROLL_DURATION, "y", () => {
      animating.current = false
    })
    setPage(next)
  }, [tracks.length])

  useEffect(() => {
    const el = containerRef.current
    if (!el || !needsScroll) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      if (e.deltaY > 0 && canNextRef.current) goTo(pageRef.current + 1)
      else if (e.deltaY < 0 && canPrevRef.current) goTo(pageRef.current - 1)
    }
    el.addEventListener("wheel", onWheel, { passive: false })
    return () => el.removeEventListener("wheel", onWheel)
  }, [needsScroll, goTo])

  if (tracks.length === 0) return null

  return (
    <section className="space-y-0">
      <div className="px-4 sm:px-6 flex items-center justify-end gap-2 pb-1">
        <button
          onClick={() => canPrev && goTo(page - 1)}
          className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canPrev ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
          aria-label={t("shelf.previous")}
        >
          <ChevronLeft size={14} />
        </button>
        <button
          onClick={() => canNext && goTo(page + 1)}
          className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canNext ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
          aria-label={t("shelf.next")}
        >
          <ChevronRight size={14} />
        </button>
      </div>
      <div
        ref={containerRef}
        style={needsScroll && containerH ? { height: containerH, overflow: "hidden" } : undefined}
        className="px-4 sm:px-6"
      >
        <div ref={innerRef}>
          <VirtualTrackList tracks={tracks} virtualized={false} showRelease sourceLabel={sourceLabel} />
        </div>
      </div>
    </section>
  )
}
