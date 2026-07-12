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
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core"
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import { useScrollRef } from "@/contexts/ScrollContext"
import VirtualTrackRow, { type TrackRowTrack } from "./VirtualTrackRow"

const ROW_HEIGHT = 52

interface VirtualTrackListProps {
  readonly tracks: TrackRowTrack[]
  readonly showArtwork?: boolean
  readonly showRelease?: boolean
  readonly sourceLabel?: string
  readonly selectable?: boolean
  readonly selectedIds?: ReadonlySet<number>
  readonly onToggleSelect?: (trackId: number) => void
  readonly onReorder?: (trackIds: number[]) => void
  /** When set, playing any row plays the whole collection starting at that track. */
  readonly onPlayTrack?: (trackId: number) => void
  /**
   * Virtualize rows against the ancestor scroll container (ScrollContext).
   * On by default for long, page-owning lists (playlists, mixes). Pass false
   * for short lists rendered inline among other content (release tracklist,
   * search results, an artist's popular tracks) so every row lives in normal
   * document flow — no absolute positioning, no scroll-window dependency.
   */
  readonly virtualized?: boolean
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
  readonly track: TrackRowTrack
  readonly index: number
  readonly showArtwork: boolean
  readonly showRelease: boolean
  readonly metricWide: boolean
  readonly sourceLabel?: string
  readonly selectable: boolean
  readonly selected: boolean
  readonly selectionActive: boolean
  readonly onToggleSelect?: (trackId: number) => void
  readonly onPlayTrack?: (trackId: number) => void
}

/** A single reorderable row: a sortable node positioned by the virtualizer. */
function SortableTrackRow({ start, ...rowProps }: RowProps & { readonly start: number }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
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
        // Positioned via `top`, NOT translateY: dnd-kit measures element rects
        // ignoring CSS transforms (sortable moves items with transforms), so
        // transform-positioned rows would all measure at top:0 — the overlay
        // would spawn at the top of the list and collision detection would
        // reflow rows in the wrong places.
        top: start,
        left: 0,
        width: "100%",
        // Sibling reflow comes from the sorting strategy's transform. The item
        // order (and thus `top`) stays frozen while the drag is in flight, and
        // dnd-kit deliberately excludes transforms from rect measurement — so
        // collision rects stay valid while rows visually make way.
        transform: CSS.Transform.toString(transform),
        transition,
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
  onPlayTrack,
  virtualized = true,
}: VirtualTrackListProps) {
  const scrollRef = useScrollRef()
  const reorderable = onReorder != null
  // Widen the metric column for artist top tracks so "N прослушиваний" fits.
  const metricWide = tracks.length > 0 && "play_count" in tracks[0]

  // Local order holds the optimistic post-drop order until the server refetch
  // lands. It stays frozen while a drag is in flight (the sorting strategy
  // handles the visual reflow via transforms) and is re-synced whenever a new
  // tracks prop arrives — otherwise the row would snap back to the stale
  // order between drop and refetch.
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

  function rowProps(track: TrackRowTrack, index: number): RowProps {
    return {
      track,
      index,
      showArtwork,
      showRelease,
      metricWide,
      sourceLabel,
      selectable,
      selected: selectedIds?.has(track.id) ?? false,
      selectionActive,
      onToggleSelect,
      onPlayTrack,
    }
  }

  // Non-virtualized: short list rendered inline. Every row lives in normal
  // document flow, so parents can measure/clip it (e.g. PopularTracks paging)
  // and it needs no ancestor scroll container. No drag-reorder here.
  if (!virtualized) {
    return (
      <div>
        {tracks.map((track, index) => (
          <VirtualTrackRow key={track.id} {...rowProps(track, index)} />
        ))}
      </div>
    )
  }

  function handleDragStart(event: DragStartEvent) {
    draggingRef.current = true
    setActiveId(Number(event.active.id))
  }

  function handleDragEnd(event: DragEndEvent) {
    draggingRef.current = false
    setActiveId(null)
    const { active, over } = event
    if (!over || active.id === over.id) return
    const next = moveTrackById(orderRef.current, Number(active.id), Number(over.id))
    if (next === orderRef.current) return
    setOrder(next)
    onReorder?.(next.map((t) => t.id))
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
            <SortableTrackRow key={track.id} start={virtualRow.start} {...props} />
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
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <SortableContext items={items.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        {container}
      </SortableContext>
      <DragOverlay dropAnimation={{ duration: 180, easing: "cubic-bezier(0.2, 0, 0, 1)" }}>
        {activeTrack ? (
          <div className="cursor-grabbing">
            <VirtualTrackRow {...rowProps(activeTrack, activeIndex)} />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}
