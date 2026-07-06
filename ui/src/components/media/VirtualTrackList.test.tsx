import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { useRef } from "react"
import { ScrollContext } from "@/contexts/ScrollContext"
import VirtualTrackList from "./VirtualTrackList"
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

function Wrapper({ tracks }: { readonly tracks: TrackSummary[] }) {
  const ref = useRef<HTMLElement>(null)
  return (
    <MemoryRouter>
      <ScrollContext.Provider value={ref}>
        <div ref={ref as React.RefObject<HTMLDivElement>} style={{ height: 600, overflow: "auto" }}>
          <VirtualTrackList tracks={tracks} sourceLabel="Test" />
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
})
