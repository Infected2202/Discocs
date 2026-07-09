import { useEffect, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragOverEvent,
  type DragStartEvent,
} from "@dnd-kit/core"
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { useScrollRef } from "@/contexts/ScrollContext"
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

/**
 * Move the item with id `activeId` to the slot currently occupied by `overId`.
 * Returns the same array reference when nothing moves so callers can bail out.
 */
export function moveTrackById<T extends { id: number }>(
  list: T[],
  activeId: number,
  overId: number,
): T[] {
  const oldIndex = list.findIndex((t) => t.id === activeId)
  const newIndex = list.findIndex((t) => t.id === overId)
  if (oldIndex < 0 || newIndex < 0 || oldIndex === newIndex) return list
  return arrayMove(list, oldIndex, newIndex)
}

interface RowProps {
  readonly track: TrackSummary
  readonly index: number
  readonly showArtwork: boolean
  readonly showRelease: boolean
  readonly sourceLabel?: string
  readonly selectable: boolean
  readonly selected: boolean
  readonly selectionActive: boolean
  readonly onToggleSelect?: (trackId: number) => void
}

/** A single reorderable row: a sortable node positioned by the virtualizer. */
function SortableTrackRow({
  start,
  dragActive,
  ...rowProps
}: RowProps & { readonly start: number; readonly dragActive: boolean }) {
  const { attributes, listeners, setNodeRef, isDragging } = useSortable({
    id: rowProps.track.id,
  })

  return (
    <div
      ref={setNodeRef}
      data-index={rowProps.index}
      {...attributes}
      {...listeners}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        transform: `translateY(${start}px)`,
        // Only animate reflow while a drag is in progress — otherwise a
        // transition would make ordinary scrolling feel laggy.
        transition: dragActive ? "transform 160ms ease" : undefined,
        // The dragged row is rendered in the DragOverlay instead; hide the
        // original slot so siblings can visibly close the gap.
        opacity: isDragging ? 0 : 1,
        cursor: "grab",
      }}
    >
      <VirtualTrackRow {...rowProps} />
    </div>
  )
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
  const reorderable = onReorder != null

  // Local order lets us reflow rows live during a drag without waiting for the
  // server round-trip. It is re-synced whenever a *new* tracks prop arrives
  // (server refetch) but never while a drag is in flight — otherwise clearing
  // activeId on drop would momentarily snap the row back to the stale order.
  const [order, setOrder] = useState<TrackSummary[]>(tracks)
  const [activeId, setActiveId] = useState<number | null>(null)
  const orderRef = useRef(order)
  orderRef.current = order
  const draggingRef = useRef(false)

  useEffect(() => {
    if (!draggingRef.current) setOrder(tracks)
  }, [tracks])

  const items = reorderable ? order : tracks

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    getItemKey: (index) => items[index]?.id ?? index,
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const totalSize = rowVirtualizer.getTotalSize()
  const virtualItems = rowVirtualizer.getVirtualItems()
  const selectionActive = (selectedIds?.size ?? 0) > 0

  function rowProps(track: TrackSummary, index: number): RowProps {
    return {
      track,
      index,
      showArtwork,
      showRelease,
      sourceLabel,
      selectable,
      selected: selectedIds?.has(track.id) ?? false,
      selectionActive,
      onToggleSelect,
    }
  }

  function handleDragStart(event: DragStartEvent) {
    draggingRef.current = true
    setActiveId(Number(event.active.id))
  }

  function handleDragOver(event: DragOverEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    setOrder((prev) => moveTrackById(prev, Number(active.id), Number(over.id)))
  }

  function handleDragEnd() {
    draggingRef.current = false
    setActiveId(null)
    const finalIds = orderRef.current.map((t) => t.id)
    const originalIds = tracks.map((t) => t.id)
    const changed =
      finalIds.length === originalIds.length &&
      finalIds.some((id, i) => id !== originalIds[i])
    if (changed) onReorder?.(finalIds)
  }

  function handleDragCancel() {
    draggingRef.current = false
    setActiveId(null)
    setOrder(tracks)
  }

  const container = (
    <div style={{ height: totalSize, position: "relative" }}>
      {virtualItems.map((virtualRow) => {
        const track = items[virtualRow.index]
        const props = rowProps(track, virtualRow.index)
        if (reorderable) {
          return (
            <SortableTrackRow
              key={track.id}
              start={virtualRow.start}
              dragActive={activeId != null}
              {...props}
            />
          )
        }
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
            }}
          >
            <VirtualTrackRow {...props} />
          </div>
        )
      })}
    </div>
  )

  if (!reorderable) return container

  const activeTrack = activeId != null ? order.find((t) => t.id === activeId) : undefined
  const activeIndex = activeId != null ? order.findIndex((t) => t.id === activeId) : -1

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <SortableContext items={items.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        {container}
      </SortableContext>
      <DragOverlay dropAnimation={{ duration: 180, easing: "cubic-bezier(0.2, 0, 0, 1)" }}>
        {activeTrack ? (
          <div className="rounded-md bg-card shadow-xl ring-1 ring-border">
            <VirtualTrackRow {...rowProps(activeTrack, activeIndex)} />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}
