import MediaCard, { type MediaCardProps } from "./MediaCard"

interface ShelfProps {
  title: string
  subtitle?: string | null
  total?: number
  items: MediaCardProps[]
}

export default function Shelf({ title, subtitle, total, items }: ShelfProps) {
  if (items.length === 0) return null

  return (
    <section className="space-y-3">
      {/* Header */}
      <div className="px-4 sm:px-6 flex items-baseline gap-3 min-w-0">
        <h2 className="text-base font-semibold shrink-0">{title}</h2>
        {subtitle && (
          <span className="text-sm text-muted-foreground truncate hidden sm:inline">{subtitle}</span>
        )}
        {total != null && total > items.length && (
          <span className="text-xs text-muted-foreground ml-auto shrink-0">{total} total</span>
        )}
      </div>

      {/* Horizontal scroll row — native overflow for reliable scrollbar on all devices */}
      <div className="shelf-scroll overflow-x-auto overflow-y-hidden">
        <div className="flex gap-1 px-3 pb-3 w-max">
          {items.map((item) => (
            <MediaCard key={`${item.type}-${item.id}`} {...item} />
          ))}
        </div>
      </div>
    </section>
  )
}
