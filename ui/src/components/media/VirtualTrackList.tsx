import { useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { useScrollRef } from "@/contexts/ScrollContext"
import { cn } from "@/lib/utils"
import VirtualTrackRow from "./VirtualTrackRow"
import type { TrackSummary } from "@/api/types"

const ROW_HEIGHT = 52

interface VirtualTrackListProps {
  readonly tracks: TrackSummary[]
  readonly showArtwork?: boolean
  readonly showRelease?: boolean
  readonly sourceLabel?: string
  readonly selectable?: boolean
  readonly selectedIds?: ReadonlySet<number>
  readonly onToggleSelect?: (trackId: number) => void
  readonly onReorder?: (trackIds: number[]) => void
}

function movedOrder(tracks: TrackSummary[], from: number, to: number): number[] {
  const ids = tracks.map((t) => t.id)
  const [moved] = ids.splice(from, 1)
  ids.splice(to, 0, moved)
  return ids
}

export default function VirtualTrackList({
  tracks,
  showArtwork = true,
  showRelease = true,
  sourceLabel,
  selectable = false,
  selectedIds,
  onToggleSelect,
  onReorder,
}: VirtualTrackListProps) {
  const scrollRef = useScrollRef()
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [overIndex, setOverIndex] = useState<number | null>(null)

  const rowVirtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
  })

  const totalSize = rowVirtualizer.getTotalSize()
  const virtualItems = rowVirtualizer.getVirtualItems()
  const selectionActive = (selectedIds?.size ?? 0) > 0
  const reorderable = onReorder != null

  function handleDrop(targetIndex: number) {
    if (dragIndex !== null && dragIndex !== targetIndex) {
      onReorder?.(movedOrder(tracks, dragIndex, targetIndex))
    }
    setDragIndex(null)
    setOverIndex(null)
  }

  return (
    <div style={{ height: totalSize, position: "relative" }}>
      {virtualItems.map((virtualRow) => (
        <div
          key={virtualRow.key}
          data-index={virtualRow.index}
          ref={rowVirtualizer.measureElement}
          draggable={reorderable}
          onDragStart={reorderable ? () => setDragIndex(virtualRow.index) : undefined}
          onDragOver={reorderable ? (e) => { e.preventDefault(); setOverIndex(virtualRow.index) } : undefined}
          onDragEnd={reorderable ? () => { setDragIndex(null); setOverIndex(null) } : undefined}
          onDrop={reorderable ? (e) => { e.preventDefault(); handleDrop(virtualRow.index) } : undefined}
          className={cn(
            dragIndex === virtualRow.index && "opacity-40",
            overIndex === virtualRow.index && dragIndex !== null && dragIndex !== virtualRow.index &&
              "shadow-[inset_0_2px_0_0_hsl(var(--primary))]",
          )}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: "100%",
            transform: `translateY(${virtualRow.start}px)`,
          }}
        >
          <VirtualTrackRow
            track={tracks[virtualRow.index]}
            index={virtualRow.index}
            showArtwork={showArtwork}
            showRelease={showRelease}
            sourceLabel={sourceLabel}
            selectable={selectable}
            selected={selectedIds?.has(tracks[virtualRow.index].id) ?? false}
            selectionActive={selectionActive}
            onToggleSelect={onToggleSelect}
          />
        </div>
      ))}
    </div>
  )
}
