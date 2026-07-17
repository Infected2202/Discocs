import { useEffect, useLayoutEffect, useRef, useState } from "react"
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

export function getListScrollMargin(
  listElement: Pick<HTMLElement, "getBoundingClientRect">,
  scrollElement: Pick<HTMLElement, "getBoundingClientRect" | "scrollTop">,
): number {
  const listRect = listElement.getBoundingClientRect()
  const scrollRect = scrollElement.getBoundingClientRect()
  return Math.max(0, listRect.top - scrollRect.top + scrollElement.scrollTop)
}

export function getVirtualRowOffset(start: number, scrollMargin: number): number {
  return start - scrollMargin
}

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
function SortableTrackRow({
  start,
  scrollMargin,
  ...rowProps
}: RowProps & { readonly start: number; readonly scrollMargin: number }) {
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
        top: getVirtualRowOffset(start, scrollMargin),
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
  const listRef = useRef<HTMLDivElement>(null)
  const [scrollMargin, setScrollMargin] = useState(0)
  const reorderable = onReorder != null
  // Widen the metric column for artist top tracks so "N прослушиваний" fits.
  const metricWide = tracks.length > 0 && "play_count" in tracks[0]

  // Local order holds the optimistic post-drop order until the server refetch
  // lands. It stays frozen while a drag is in flight (the sorting strategy
  // handles the visual reflow via transforms) and is re-synced whenever a new
  // tracks prop arrives — otherwise the row would snap back to the stale
  // order between drop and refetch.
  const [order, setOrder] = useState<TrackRowTrack[]>(tracks)
  const [activeId, setActiveId] = useState<number | null>(null)
  const orderRef = useRef(order)
  orderRef.current = order
  const draggingRef = useRef(false)

  useEffect(() => {
    if (!draggingRef.current) setOrder(tracks)
  }, [tracks])

  const items = reorderable ? order : tracks
  const selectionActive = (selectedIds?.size ?? 0) > 0

  // The list lives below AppShell and collection headers inside the shared
  // scroll element. Keep the virtualizer aligned with that document offset.
  useLayoutEffect(() => {
    const listElement = listRef.current
    const scrollElement = scrollRef?.current
    if (!listElement || !scrollElement) return

    const updateScrollMargin = () => {
      const next = getListScrollMargin(listElement, scrollElement)
      setScrollMargin((current) => current === next ? current : next)
    }

    updateScrollMargin()
    globalThis.addEventListener("resize", updateScrollMargin)
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(updateScrollMargin)
    resizeObserver?.observe(scrollElement)
    const pageElement = listElement.parentElement?.parentElement
    if (pageElement) resizeObserver?.observe(pageElement)

    return () => {
      globalThis.removeEventListener("resize", updateScrollMargin)
      resizeObserver?.disconnect()
    }
  }, [scrollRef, selectionActive])

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    scrollMargin,
    getItemKey: (index) => items[index]?.id ?? index,
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const totalSize = rowVirtualizer.getTotalSize()
  const virtualItems = rowVirtualizer.getVirtualItems()
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
    <div ref={listRef} style={{ height: totalSize, position: "relative" }}>
      {virtualItems.map((virtualRow) => {
        const track = items[virtualRow.index]
        const props = rowProps(track, virtualRow.index)
        if (reorderable) {
          return (
            <SortableTrackRow
              key={track.id}
              start={virtualRow.start}
              scrollMargin={scrollMargin}
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
              transform: `translateY(${getVirtualRowOffset(virtualRow.start, scrollMargin)}px)`,
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
