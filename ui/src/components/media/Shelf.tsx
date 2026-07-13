import { useState, useRef, useEffect, useCallback } from "react"
import { useNavigate } from "react-router"
import { useTranslation } from "react-i18next"
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

const MOBILE_COLS = 2
const MOBILE_GAP_PX = 8

export default function Shelf({ title = "", subtitle, items, shelfKey }: ShelfProps) {
  const { t } = useTranslation("media")
  const navigate = useNavigate()
  const cols = useColumns()
  const isMobile = cols === MOBILE_COLS
  const [page, setPage] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)
  const animating = useRef(false)

  const sliced = isMobile ? items : items.slice(0, cols * 2)
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
  const shelfHref = shelfKey ? `/shelf/${shelfKey}` : null

  return (
    <section className="shelf-section space-y-1">
      {/* Header */}
      {(title || shelfKey || (!isMobile && totalPages > 1)) && <div className="px-4 sm:px-6 flex items-center gap-2 min-w-0">
        {shelfHref ? (
          <button
            type="button"
            className="text-sm font-semibold shrink-0 hover:underline"
            onClick={() => navigate(shelfHref)}
          >
            {title}
          </button>
        ) : (
          <h2 className="text-sm font-semibold shrink-0">{title}</h2>
        )}
        {subtitle && (
          <span className="text-xs text-muted-foreground truncate hidden sm:inline">{subtitle}</span>
        )}
        <div
          aria-hidden="true"
          className="h-px min-w-3 flex-1 bg-linear-to-r from-primary/50 to-transparent"
        />
        <div className="flex items-center gap-1 shrink-0">
          {shelfHref && (
            <button
              type="button"
              onClick={() => navigate(shelfHref)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-1.5 py-0.5 rounded hover:bg-muted"
            >
              {t("shelf.more")}
            </button>
          )}
          {!isMobile && totalPages > 1 && (
            <>
              <button
                type="button"
                onClick={() => canPrev && goTo(page - 1)}
                disabled={!canPrev}
                className={`flex items-center justify-center w-5 h-5 rounded-full border border-border transition-colors ${canPrev ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
                aria-label={t("shelf.previous")}
              >
                <ChevronLeft size={11} />
              </button>
              <button
                type="button"
                onClick={() => canNext && goTo(page + 1)}
                disabled={!canNext}
                className={`flex items-center justify-center w-5 h-5 rounded-full border border-border transition-colors ${canNext ? "hover:bg-muted" : "opacity-30 pointer-events-none"}`}
                aria-label={t("shelf.next")}
              >
                <ChevronRight size={11} />
              </button>
            </>
          )}
        </div>
      </div>}

      {/* Slider */}
      {isMobile ? (
        <div
          className="flex overflow-x-auto px-3 pb-1"
          style={{
            gap: `${MOBILE_GAP_PX}px`,
            scrollSnapType: "x mandatory",
            scrollPaddingLeft: "12px",
            scrollPaddingRight: "12px",
            WebkitOverflowScrolling: "touch",
          }}
        >
          {sliced.map((item) => (
            <div
              key={`${item.type}-${item.id}`}
              style={{
                flex: `0 0 calc((100% - ${MOBILE_GAP_PX}px) / ${MOBILE_COLS})`,
                minWidth: 0,
                scrollSnapAlign: "start",
              }}
            >
              <MediaCard {...item} variant="shelf" />
            </div>
          ))}
        </div>
      ) : (
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
      )}
    </section>
  )
}
