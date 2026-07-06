import { useState, useRef, useEffect, useCallback } from "react"
import { useNavigate } from "react-router"
import { ChevronLeft, ChevronRight } from "lucide-react"
import MediaCard, { type MediaCardProps } from "./MediaCard"
import { useColumns } from "@/hooks/useColumns"
import { animateScroll } from "@/lib/animateScroll"

const SCROLL_DURATION = 500 // ms

interface ShelfProps {
  readonly title?: string
  readonly subtitle?: string | null
  readonly items: MediaCardProps[]
  readonly shelfKey?: string
}

export default function Shelf({ title = "", subtitle, items, shelfKey }: ShelfProps) {
  const navigate = useNavigate()
  const cols = useColumns()
  const [page, setPage] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const animating = useRef(false)

  const sliced = items.slice(0, cols * 2)
  const totalPages = Math.ceil(sliced.length / cols)
  const canPrev = page > 0
  const canNext = page < totalPages - 1

  useEffect(() => {
    setPage(0)
    if (containerRef.current) containerRef.current.scrollLeft = 0
  }, [cols])

  const goTo = useCallback((next: number) => {
    if (animating.current) return
    const el = containerRef.current
    if (!el) { setPage(next); return }
    animating.current = true
    animateScroll(el, el.scrollLeft, next * el.clientWidth, SCROLL_DURATION, "x", () => {
      animating.current = false
    })
    setPage(next)
  }, [])

  if (items.length === 0) return null

  const pages = Array.from({ length: totalPages }, (_, i) =>
    sliced.slice(i * cols, (i + 1) * cols)
  )

  return (
    <section className="shelf-section space-y-1">
      {/* Header */}
      {(title || shelfKey || totalPages > 1) && <div className="px-4 sm:px-6 flex items-center gap-2 min-w-0">
        <h2
          className={shelfKey ? "text-sm font-semibold shrink-0 cursor-pointer hover:underline" : "text-sm font-semibold shrink-0"}
          onClick={shelfKey ? () => navigate(`/shelf/${shelfKey}`) : undefined}
        >{title}</h2>
        {subtitle && (
          <span className="text-xs text-muted-foreground truncate hidden sm:inline">{subtitle}</span>
        )}
        <div className="ml-auto flex items-center gap-1 shrink-0">
          {shelfKey && (
            <button
              onClick={() => navigate(`/shelf/${shelfKey}`)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-1.5 py-0.5 rounded hover:bg-muted"
            >
              More
            </button>
          )}
          {totalPages > 1 && (
            <>
              <button
                onClick={() => canPrev && goTo(page - 1)}
                className={`flex items-center justify-center w-5 h-5 rounded-full border border-border transition-colors ${canPrev ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
                aria-label="Previous"
              >
                <ChevronLeft size={11} />
              </button>
              <button
                onClick={() => canNext && goTo(page + 1)}
                className={`flex items-center justify-center w-5 h-5 rounded-full border border-border transition-colors ${canNext ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
                aria-label="Next"
              >
                <ChevronRight size={11} />
              </button>
            </>
          )}
        </div>
      </div>}

      {/* Slider */}
      <div ref={containerRef} className="overflow-x-hidden">
        <div className="flex">
          {pages.map((pageItems, pi) => (
            <div
              key={pi}
              className="px-3 pb-1"
              style={{
                display: "grid",
                gridTemplateColumns: `repeat(${cols}, 1fr)`,
                gap: "2px",
                minWidth: "100%",
                width: "100%",
                boxSizing: "border-box",
              }}
            >
              {pageItems.map((item) => (
                <MediaCard key={`${item.type}-${item.id}`} {...item} variant="shelf" />
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
