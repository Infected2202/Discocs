import { describe, it, expect, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { useRef } from "react"
import { ScrollContext } from "@/contexts/ScrollContext"
import VirtualTrackList, {
  getListScrollMargin,
  getVirtualRowOffset,
  moveTrackById,
} from "./VirtualTrackList"
import type { TrackSummary } from "@/api/types"

// jsdom has no layout engine — mock useVirtualizer to render all rows directly
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count, estimateSize }: { count: number; estimateSize: () => number }) => ({
    getTotalSize: () => count * estimateSize(),
    getVirtualItems: () =>
      Array.from({ length: count }, (_, i) => ({
        key: i,
        index: i,
        start: i * estimateSize(),
        size: estimateSize(),
      })),
    measureElement: () => {},
  }),
}))

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (sel: (s: object) => unknown) =>
    sel({ currentTrackId: null, playbackState: "idle", playSource: vi.fn(), togglePlay: vi.fn() }),
}))

vi.mock("@/store/navidromeStore", () => ({
  useNavidromeStore: (sel: (s: object) => unknown) =>
    sel({ likedIds: new Set(), toggleLike: vi.fn() }),
}))

vi.mock("./TrackMenu", () => ({ default: () => null }))

function makeTrack(id: number): TrackSummary {
  return {
    id,
    title: `Track ${id}`,
    duration: 180,
    artists: [{ id: 1, name: "Artist" }],
    release: { id: 1, title: "Album" },
    artwork: { url: null, source: "placeholder", placeholder: true },
    explicit: false,
    liked: false,
    actions: [],
  }
}

function Wrapper({ tracks, onReorder, onPlayTrack, virtualized }: {
  readonly tracks: TrackSummary[]
  readonly onReorder?: (trackIds: number[]) => void
  readonly onPlayTrack?: (trackId: number) => void
  readonly virtualized?: boolean
}) {
  const ref = useRef<HTMLElement>(null)
  return (
    <MemoryRouter>
      <ScrollContext.Provider value={ref}>
        <div ref={ref as React.RefObject<HTMLDivElement>} style={{ height: 600, overflow: "auto" }}>
          <VirtualTrackList
            tracks={tracks}
            sourceLabel="Test"
            onReorder={onReorder}
            onPlayTrack={onPlayTrack}
            virtualized={virtualized}
          />
        </div>
      </ScrollContext.Provider>
    </MemoryRouter>
  )
}

describe("VirtualTrackList", () => {
  it("рендерит все треки (через mocked virtualizer)", () => {
    const tracks = Array.from({ length: 5 }, (_, i) => makeTrack(i + 1))
    render(<Wrapper tracks={tracks} />)
    expect(screen.getByText("Track 1")).toBeInTheDocument()
    expect(screen.getByText("Track 5")).toBeInTheDocument()
  })

  it("высота контейнера = count * ROW_HEIGHT (52px)", () => {
    const tracks = Array.from({ length: 10 }, (_, i) => makeTrack(i + 1))
    const { container } = render(<Wrapper tracks={tracks} />)
    const virtualContainer = container.querySelector("[style*='position: relative']") as HTMLElement
    expect(virtualContainer.style.height).toBe("520px")
  })

  it("пустой список — контейнер нулевой высоты, строк нет", () => {
    const { container } = render(<Wrapper tracks={[]} />)
    const virtualContainer = container.querySelector("[style*='position: relative']") as HTMLElement
    expect(virtualContainer.style.height).toBe("0px")
    expect(container.querySelectorAll("[data-index]")).toHaveLength(0)
  })

  it("каждая строка имеет data-index соответствующий позиции", () => {
    const tracks = Array.from({ length: 3 }, (_, i) => makeTrack(i + 1))
    render(<Wrapper tracks={tracks} />)
    const rows = document.querySelectorAll("[data-index]")
    const indices = Array.from(rows).map((r) => Number(r.getAttribute("data-index")))
    expect(indices).toEqual([0, 1, 2])
  })

  it("передаёт sourceLabel в строки (title отображается)", () => {
    const tracks = [makeTrack(42)]
    render(<Wrapper tracks={tracks} />)
    expect(screen.getByText("Track 42")).toBeInTheDocument()
  })

  it("клик по строке зовёт onPlayTrack с id трека (весь плейлист/микс с позиции)", () => {
    const onPlayTrack = vi.fn()
    render(<Wrapper tracks={[makeTrack(42)]} onPlayTrack={onPlayTrack} />)

    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0])

    expect(onPlayTrack).toHaveBeenCalledWith(42)
  })

  it("non-virtualized: рендерит все строки в обычном потоке без relative-контейнера фикс. высоты", () => {
    const tracks = Array.from({ length: 4 }, (_, i) => makeTrack(i + 1))
    const { container } = render(<Wrapper tracks={tracks} virtualized={false} />)

    // Every row is present…
    expect(screen.getByText("Track 1")).toBeInTheDocument()
    expect(screen.getByText("Track 4")).toBeInTheDocument()
    // …and there's no absolutely-positioned virtual spacer container.
    expect(container.querySelector("[style*='position: relative']")).toBeNull()
    expect(container.querySelectorAll("[data-track-row]")).toHaveLength(4)
  })

  it("non-virtualized: клик по строке зовёт onPlayTrack", () => {
    const onPlayTrack = vi.fn()
    render(<Wrapper tracks={[makeTrack(42)]} onPlayTrack={onPlayTrack} virtualized={false} />)

    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0])

    expect(onPlayTrack).toHaveBeenCalledWith(42)
  })

  it("не резервирует desktop-колонку release на мобильном", () => {
    render(<Wrapper tracks={[makeTrack(42)]} />)
    const row = document.querySelector("[data-track-row]") as HTMLElement
    expect(row.style.getPropertyValue("--track-grid-mobile")).not.toContain("180px")
    expect(row.style.getPropertyValue("--track-grid-desktop")).toContain("180px")
  })

  it("с onReorder строки рендерятся как sortable-узлы (dnd-kit не падает)", () => {
    const onReorder = vi.fn()
    const tracks = [makeTrack(1), makeTrack(2), makeTrack(3)]
    render(<Wrapper tracks={tracks} onReorder={onReorder} />)

    const rows = document.querySelectorAll("[data-index]")
    expect(rows).toHaveLength(3)
    // dnd-kit useSortable проставляет aria-roledescription="sortable".
    expect((rows[0] as HTMLElement).getAttribute("aria-roledescription")).toBe("sortable")
    expect(screen.getByText("Track 2")).toBeInTheDocument()
  })
})

describe("moveTrackById", () => {
  const list = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }]

  it("перемещает элемент вперёд (id=1 на место id=3)", () => {
    expect(moveTrackById(list, 1, 3).map((t) => t.id)).toEqual([2, 3, 1, 4])
  })

  it("перемещает элемент назад (id=4 на место id=2)", () => {
    expect(moveTrackById(list, 4, 2).map((t) => t.id)).toEqual([1, 4, 2, 3])
  })

  it("возвращает исходный массив без изменений при active==over", () => {
    expect(moveTrackById(list, 2, 2)).toBe(list)
  })

  it("возвращает исходный массив, если id не найден", () => {
    expect(moveTrackById(list, 99, 2)).toBe(list)
    expect(moveTrackById(list, 1, 99)).toBe(list)
  })

  it("не мутирует исходный массив при перемещении", () => {
    const original = list.map((t) => ({ ...t }))
    moveTrackById(list, 1, 3)
    expect(list.map((t) => t.id)).toEqual(original.map((t) => t.id))
  })
})

describe("virtual-list positioning", () => {
  it("measures the list offset in the scroll element's coordinate space", () => {
    const listElement = {
      getBoundingClientRect: () => ({ top: 420 }) as DOMRect,
    }
    const scrollElement = {
      getBoundingClientRect: () => ({ top: 20 }) as DOMRect,
      scrollTop: 300,
    }

    expect(getListScrollMargin(listElement, scrollElement)).toBe(700)
  })

  it("subtracts scroll margin from each absolute row position", () => {
    expect(getVirtualRowOffset(4340, 700)).toBe(3640)
  })
})
