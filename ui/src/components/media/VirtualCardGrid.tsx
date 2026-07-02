import { useEffect, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { useScrollRef } from "@/contexts/ScrollContext"
import { computeGridColumns } from "./gridLayout"

interface VirtualCardGridProps<T> {
  items: T[]
  getKey: (item: T, index: number) => string
  renderItem: (item: T, index: number) => React.ReactNode
  minColumnWidth?: number
  gap?: number
}

// Виртуализированный адаптивный грид. Держит в DOM только видимые строки
// карточек (и их картинки) независимо от глубины прокрутки — иначе бесконечная
// полка копит тысячи <img> в памяти. Число колонок вычисляется из ширины
// контейнера, повторяя CSS auto-fill; строки виртуализируются по общему
// скролл-контейнеру (<main> из ScrollContext).
export default function VirtualCardGrid<T>({
  items,
  getKey,
  renderItem,
  minColumnWidth = 160,
  gap = 4,
}: VirtualCardGridProps<T>) {
  const scrollRef = useScrollRef()
  const containerRef = useRef<HTMLDivElement>(null)
  const [columns, setColumns] = useState(1)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () =>
      setColumns(computeGridColumns(el.clientWidth, minColumnWidth, gap))
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [minColumnWidth, gap])

  const rowCount = Math.ceil(items.length / columns)

  const rowVirtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => {
      const width = containerRef.current?.clientWidth ?? columns * minColumnWidth
      const colWidth = (width - gap * (columns - 1)) / columns
      // квадратная обложка ≈ ширина колонки + подпись/паддинги + зазор строки
      return colWidth + 48 + gap
    },
    overscan: 3,
  })

  const virtualRows = rowVirtualizer.getVirtualItems()

  return (
    <div
      ref={containerRef}
      style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}
    >
      {virtualRows.map((virtualRow) => {
        const start = virtualRow.index * columns
        const rowItems = items.slice(start, start + columns)
        return (
          <div
            key={virtualRow.key}
            data-index={virtualRow.index}
            ref={rowVirtualizer.measureElement}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${virtualRow.start}px)`,
              display: "grid",
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
              gap,
            }}
          >
            {rowItems.map((item, i) => (
              <div key={getKey(item, start + i)}>{renderItem(item, start + i)}</div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
