import { useState, type ReactNode } from "react"
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  MeasuringStrategy,
  PointerSensor,
  TouchSensor,
  closestCenter,
  useDroppable,
  useSensor,
  useSensors,
  type DragCancelEvent,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core"
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable"
import type { DeckId } from "@/engine/playback"
import { playerPlayback } from "@/engine/playback"
import type { TrackRowTrack } from "@/components/media/VirtualTrackRow"
import VirtualTrackRow from "@/components/media/VirtualTrackRow"
import { usePlayerStore } from "@/store/playerStore"
import { useUIStore } from "@/store/uiStore"

export interface PlaylistTrackDragData {
  readonly kind: "playlist-track"
  readonly track: TrackRowTrack
  readonly sourceLabel?: string
  readonly listId: string
  readonly index: number
  readonly showArtwork: boolean
  readonly showRelease: boolean
  readonly metricWide: boolean
  readonly begin: () => void
  readonly reorder?: (overTrackId: number) => void
  readonly finish: () => void
}

export interface DjDeckDropData {
  readonly kind: "dj-deck"
  readonly deck: DeckId
}

function isTrackDragData(value: unknown): value is PlaylistTrackDragData {
  return typeof value === "object" && value !== null && (value as PlaylistTrackDragData).kind === "playlist-track"
}

function isDeckDropData(value: unknown): value is DjDeckDropData {
  return typeof value === "object" && value !== null && (value as DjDeckDropData).kind === "dj-deck"
}

function DeckDropTarget({ deck, disabled }: { readonly deck: DeckId; readonly disabled: boolean }) {
  const data: DjDeckDropData = { kind: "dj-deck", deck }
  const { isOver, setNodeRef } = useDroppable({ id: `dj-drag-dock-${deck}`, data, disabled })
  return (
    <div
      ref={setNodeRef}
      className={`grid min-h-16 flex-1 place-items-center border text-sm font-semibold tracking-[.18em] transition-colors ${
        disabled
          ? "border-border bg-background/90 text-muted-foreground"
          : isOver
            ? "border-primary bg-primary/20 text-primary"
            : "border-primary/60 bg-background/95 text-foreground"
      }`}
    >
      DECK {deck}{disabled ? " · PROGRAM" : " · LOAD"}
    </div>
  )
}

function DeckDropDock() {
  const programDeck = playerPlayback.getEngineSnapshot().programDeck
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[92px] z-[90] flex justify-center px-4">
      <div className="pointer-events-auto flex w-full max-w-3xl gap-2 rounded-md border border-border bg-background/90 p-2 shadow-2xl backdrop-blur">
        <DeckDropTarget deck="A" disabled={programDeck === "A"} />
        <DeckDropTarget deck="B" disabled={programDeck === "B"} />
      </div>
    </div>
  )
}

export default function DjTrackDragProvider({ children }: { readonly children: ReactNode }) {
  const [active, setActive] = useState<PlaylistTrackDragData | null>(null)
  const djSurfaceOpen = useUIStore((state) => state.djSurfaceOpen)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  function handleDragStart(event: DragStartEvent) {
    const data = event.active.data.current
    if (!isTrackDragData(data)) return
    data.begin()
    setActive(data)
  }

  function finishDrag(data: PlaylistTrackDragData | null) {
    data?.finish()
    setActive(null)
  }

  function handleDragCancel(_event: DragCancelEvent) {
    finishDrag(active)
  }

  function handleDragEnd(event: DragEndEvent) {
    const data = event.active.data.current
    if (!isTrackDragData(data)) {
      setActive(null)
      return
    }
    const overData = event.over?.data.current
    if (isDeckDropData(overData)) {
      const player = usePlayerStore.getState()
      useUIStore.getState().openDjSurface()
      // Перетаскивание на деку — явное намерение свести: включаем DJ-движок, затем
      // грузим трек в свободную деку графа (при выключенном движке prefetch лишь
      // кеширует blob и деку в графе не создаёт).
      void player.activateDj().then(() => player.prepareDjDeck(data.track.id, overData.deck))
      finishDrag(data)
      return
    }
    if (isTrackDragData(overData) && data.listId === overData.listId) {
      data.reorder?.(overData.track.id)
    }
    finishDrag(data)
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      measuring={{ droppable: { strategy: MeasuringStrategy.Always } }}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      {children}
      {active && djSurfaceOpen && <DeckDropDock />}
      <DragOverlay dropAnimation={{ duration: 180, easing: "cubic-bezier(0.2, 0, 0, 1)" }}>
        {active ? (
          <div className="cursor-grabbing">
            <VirtualTrackRow
              track={active.track}
              index={active.index}
              showArtwork={active.showArtwork}
              showRelease={active.showRelease}
              metricWide={active.metricWide}
              sourceLabel={active.sourceLabel}
              selectable={false}
              selected={false}
              selectionActive={false}
            />
          </div>
        ) : null}
      </DragOverlay>
    </DndContext>
  )
}
