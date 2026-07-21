import { render } from "@testing-library/react"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

const mocks = vi.hoisted(() => ({
  dndProps: null as Record<string, (event: unknown) => void> | null,
  openDjSurface: vi.fn(),
  prepareDjDeck: vi.fn().mockResolvedValue(undefined),
}))

vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children, ...props }: { children: ReactNode }) => {
    mocks.dndProps = props as Record<string, (event: unknown) => void>
    return <>{children}</>
  },
  DragOverlay: ({ children }: { children: ReactNode }) => <>{children}</>,
  KeyboardSensor: class {},
  PointerSensor: class {},
  TouchSensor: class {},
  MeasuringStrategy: { Always: "always" },
  closestCenter: vi.fn(),
  useDroppable: vi.fn(() => ({ isOver: false, setNodeRef: vi.fn() })),
  useSensor: vi.fn(() => ({})),
  useSensors: vi.fn(() => []),
}))

vi.mock("@dnd-kit/sortable", () => ({ sortableKeyboardCoordinates: vi.fn() }))
vi.mock("@/store/uiStore", () => ({
  useUIStore: { getState: () => ({ openDjSurface: mocks.openDjSurface }) },
}))
vi.mock("@/store/playerStore", () => ({
  usePlayerStore: { getState: () => ({ prepareDjDeck: mocks.prepareDjDeck }) },
}))
vi.mock("@/engine/playback", () => ({
  playerPlayback: { getEngineSnapshot: () => ({ programDeck: "A" }) },
}))
vi.mock("@/components/media/VirtualTrackRow", () => ({
  default: ({ track }: { track: { title: string } }) => <div>{track.title}</div>,
}))

import DjTrackDragProvider, { type PlaylistTrackDragData } from "./DjTrackDragProvider"

function trackData(): PlaylistTrackDragData {
  return {
    kind: "playlist-track",
    track: { id: 42, title: "Dropped track" } as PlaylistTrackDragData["track"],
    sourceLabel: "Playlist",
    listId: "list",
    index: 0,
    showArtwork: true,
    showRelease: true,
    metricWide: false,
    begin: vi.fn(),
    finish: vi.fn(),
  }
}

describe("DjTrackDragProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.dndProps = null
  })

  it("opens the DJ surface at drag start and loads a deck on drop", () => {
    render(<DjTrackDragProvider><div>playlist</div></DjTrackDragProvider>)
    const data = trackData()

    mocks.dndProps?.onDragStart({ active: { data: { current: data } } })
    mocks.dndProps?.onDragEnd({
      active: { data: { current: data } },
      over: { data: { current: { kind: "dj-deck", deck: "B" } } },
    })

    expect(data.begin).toHaveBeenCalledOnce()
    expect(mocks.openDjSurface).toHaveBeenCalledOnce()
    expect(mocks.prepareDjDeck).toHaveBeenCalledWith(42, "B")
    expect(data.finish).toHaveBeenCalledOnce()
  })
})
