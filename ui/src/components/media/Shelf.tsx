import { useState, useEffect } from "react"
import { useNavigate } from "react-router"
import { ChevronLeft, ChevronRight } from "lucide-react"
import MediaCard, { type MediaCardProps } from "./MediaCard"
import { useColumns } from "@/hooks/useColumns"

interface ShelfProps {
  title: string
  subtitle?: string | null
  items: MediaCardProps[]
  shelfKey?: string
}

export default function Shelf({ title, subtitle, items, shelfKey }: ShelfProps) {
  const navigate = useNavigate()
  const cols = useColumns()
  const [page, setPage] = useState(0)
  const [animKey, setAnimKey] = useState(0)
  const [direction, setDirection] = useState<"left" | "right">("right")

  // Reset to first page on column count change (resize / zoom)
  useEffect(() => { setPage(0) }, [cols])

  if (items.length === 0) return null

  const sliced = items.slice(0, cols * 2)
  const totalPages = Math.ceil(sliced.length / cols)
  const visible = sliced.slice(page * cols, (page + 1) * cols)
  const canPrev = page > 0
  const canNext = page < totalPages - 1

  function goTo(next: number, dir: "left" | "right") {
    setDirection(dir)
    setPage(next)
    setAnimKey((k) => k + 1)
  }

  return (
    <section className="shelf-section space-y-3">
      {/* Header */}
      <div className="px-4 sm:px-6 flex items-center gap-3 min-w-0">
        <h2 className="text-base font-semibold shrink-0">{title}</h2>
        {subtitle && (
          <span className="text-sm text-muted-foreground truncate hidden sm:inline">{subtitle}</span>
        )}
        <div className="ml-auto flex items-center gap-2 shrink-0">
          {shelfKey && (
            <button
              onClick={() => navigate(`/shelf/${shelfKey}`)}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-muted"
            >
              More
            </button>
          )}
          {totalPages > 1 && (
            <>
              <button
                onClick={() => canPrev && goTo(page - 1, "left")}
                className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canPrev ? "hover:bg-muted" : "opacity-30"}`}
                aria-label="Previous"
              >
                <ChevronLeft size={14} />
              </button>
              <button
                onClick={() => canNext && goTo(page + 1, "right")}
                className={`flex items-center justify-center w-7 h-7 rounded-full border border-border transition-colors ${canNext ? "hover:bg-muted" : "opacity-30"}`}
                aria-label="Next"
              >
                <ChevronRight size={14} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Card grid — animated on page change */}
      <div className="overflow-hidden px-3 pb-3">
        <div
          key={animKey}
          className={direction === "right" ? "shelf-slide-right" : "shelf-slide-left"}
          style={{ display: "grid", gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: "2px" }}
        >
          {visible.map((item) => (
            <MediaCard key={`${item.type}-${item.id}`} {...item} variant="shelf" />
          ))}
        </div>
      </div>
    </section>
  )
}
