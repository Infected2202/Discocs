import { useEffect, useId, useLayoutEffect, useRef, useState } from "react"
import { useVirtualizer } from "@tanstack/react-virtual"
import { useDraggable } from "@dnd-kit/core"
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable"
import { CSS } from "@dnd-kit/utilities"
import type { PlaylistTrackDragData } from "@/components/dj/DjTrackDragProvider"
import { useScrollRef } from "@/contexts/ScrollContext"
import { useUIStore } from "@/store/uiStore"
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
  /** Disable only for short, inline lists which do not use the app scroll container. */
  readonly virtualized?: boolean
}

export function moveTrackById<T extends { id: number }>(
  list: T[],
  activeId: number,
  overId: number,
): T[] {
  const oldIndex = list.findIndex((track) => track.id === activeId)
  const newIndex = list.findIndex((track) => track.id === overId)
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

interface PositionedRowProps extends RowProps {
  readonly start: number
  readonly scrollMargin: number
  readonly dragData: PlaylistTrackDragData
}

function SortableTrackRow({ start, scrollMargin, dragData, ...rowProps }: PositionedRowProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `${dragData.listId}:${rowProps.track.id}`,
    data: dragData,
  })
  return (
    <div
      ref={setNodeRef}
      data-index={rowProps.index}
      {...attributes}
      {...listeners}
      style={{
        position: "absolute",
        top: getVirtualRowOffset(start, scrollMargin),
        left: 0,
        width: "100%",
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0 : 1,
        cursor: "grab",
      }}
    >
      <VirtualTrackRow {...rowProps} />
    </div>
  )
}

function DraggableTrackRow({
  start,
  scrollMargin,
  dragData,
  dragEnabled,
  measureElement,
  ...rowProps
}: PositionedRowProps & { readonly dragEnabled: boolean; readonly measureElement: (node: Element | null) => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `${dragData.listId}:${rowProps.track.id}`,
    data: dragData,
    disabled: !dragEnabled,
  })
  return (
    <div
      data-index={rowProps.index}
      ref={(node) => {
        setNodeRef(node)
        measureElement(node)
      }}
      {...attributes}
      {...listeners}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        transform: transform
          ? CSS.Translate.toString(transform)
          : `translateY(${getVirtualRowOffset(start, scrollMargin)}px)`,
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
  const listId = useId()
  const scrollRef = useScrollRef()
  const listRef = useRef<HTMLDivElement>(null)
  const [scrollMargin, setScrollMargin] = useState(0)
  const reorderable = onReorder != null
  // Строки без реордера перетаскиваемы только ради drop-на-деку — не имеет смысла
  // вешать touch/pointer-листенеры на каждую строку, пока DJ-панель не открыта
  // явно кнопкой (иначе задевает обычный скролл на мобильном).
  const djSurfaceOpen = useUIStore((state) => state.djSurfaceOpen)
  const metricWide = tracks.length > 0 && "play_count" in tracks[0]
  const [order, setOrder] = useState<TrackRowTrack[]>(tracks)
  const orderRef = useRef(order)
  orderRef.current = order
  const draggingRef = useRef(false)

  useEffect(() => {
    if (!draggingRef.current) setOrder(tracks)
  }, [tracks])

  const items = reorderable ? order : tracks
  const selectionActive = (selectedIds?.size ?? 0) > 0

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
    const resizeObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateScrollMargin)
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

  if (!virtualized) {
    return <div>{tracks.map((track, index) => <VirtualTrackRow key={track.id} {...rowProps(track, index)} />)}</div>
  }

  function reorderTrack(activeTrackId: number, overTrackId: number) {
    const next = moveTrackById(orderRef.current, activeTrackId, overTrackId)
    if (next === orderRef.current) return
    setOrder(next)
    onReorder?.(next.map((track) => track.id))
  }

  const totalSize = rowVirtualizer.getTotalSize()
  const container = (
    <div ref={listRef} style={{ height: totalSize, position: "relative" }}>
      {rowVirtualizer.getVirtualItems().map((virtualRow) => {
        const track = items[virtualRow.index]
        const props = rowProps(track, virtualRow.index)
        const dragData: PlaylistTrackDragData = {
          kind: "playlist-track",
          track,
          sourceLabel,
          listId,
          index: virtualRow.index,
          showArtwork,
          showRelease,
          metricWide,
          begin: () => { draggingRef.current = true },
          finish: () => { draggingRef.current = false },
          reorder: reorderable ? (overTrackId) => reorderTrack(track.id, overTrackId) : undefined,
        }
        if (reorderable) {
          return <SortableTrackRow
            key={track.id}
            start={virtualRow.start}
            scrollMargin={scrollMargin}
            dragData={dragData}
            {...props}
          />
        }
        return <DraggableTrackRow
          key={virtualRow.key}
          start={virtualRow.start}
          scrollMargin={scrollMargin}
          measureElement={rowVirtualizer.measureElement}
          dragData={dragData}
          dragEnabled={djSurfaceOpen}
          {...props}
        />
      })}
    </div>
  )

  if (!reorderable) return container
  return (
    <SortableContext items={items.map((track) => `${listId}:${track.id}`)} strategy={verticalListSortingStrategy}>
      {container}
    </SortableContext>
  )
}
