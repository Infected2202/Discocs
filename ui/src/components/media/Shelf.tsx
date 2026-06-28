import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
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
      <div className="px-6 flex items-baseline gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {subtitle && <span className="text-sm text-muted-foreground">{subtitle}</span>}
        {total != null && total > items.length && (
          <span className="text-xs text-muted-foreground ml-auto">{total} total</span>
        )}
      </div>

      {/* Horizontal scroll row */}
      <ScrollArea>
        <div className="flex gap-1 px-3 pb-3">
          {items.map((item) => (
            <MediaCard key={`${item.type}-${item.id}`} {...item} />
          ))}
        </div>
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </section>
  )
}
