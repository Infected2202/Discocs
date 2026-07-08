import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { useRef } from "react"
import { ScrollContext } from "@/contexts/ScrollContext"
import VirtualCardGrid from "./VirtualCardGrid"

const noop = vi.fn()

// jsdom has no ResizeObserver — stub it so the column-measuring effect runs
class ResizeObserverStub {
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub)

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
    measureElement: noop,
  }),
}))

// jsdom reports clientWidth 0 — force a known width so columns resolve to 4
vi.mock("./gridLayout", () => ({
  computeGridColumns: () => 4,
}))

function Wrapper({ items }: { readonly items: string[] }) {
  const ref = useRef<HTMLElement>(null)
  return (
    <ScrollContext.Provider value={ref}>
      <div ref={ref as React.RefObject<HTMLDivElement>} style={{ height: 600, overflow: "auto" }}>
        <VirtualCardGrid
          items={items}
          getKey={(item) => item}
          renderItem={(item) => <div>{item}</div>}
        />
      </div>
    </ScrollContext.Provider>
  )
}

describe("VirtualCardGrid", () => {
  it("groups items into rows of `columns` per row", () => {
    const items = Array.from({ length: 10 }, (_, i) => `item-${i}`)
    render(<Wrapper items={items} />)
    const rows = document.querySelectorAll("[data-index]")
    expect(rows).toHaveLength(3)
  })

  it("renders every item across the rows", () => {
    const items = Array.from({ length: 6 }, (_, i) => `item-${i}`)
    render(<Wrapper items={items} />)
    expect(screen.getByText("item-0")).toBeInTheDocument()
    expect(screen.getByText("item-5")).toBeInTheDocument()
  })

  it("keeps the last (partial) row's items", () => {
    const items = Array.from({ length: 5 }, (_, i) => `item-${i}`)
    render(<Wrapper items={items} />)
    const rows = document.querySelectorAll("[data-index]")
    expect(rows).toHaveLength(2)
    expect(screen.getByText("item-4")).toBeInTheDocument()
  })

  it("empty list — no rows, zero-height container", () => {
    const { container } = render(<Wrapper items={[]} />)
    expect(container.querySelectorAll("[data-index]")).toHaveLength(0)
    const grid = container.querySelector("[style*='position: relative']") as HTMLElement
    expect(grid.style.height).toBe("0px")
  })
})
