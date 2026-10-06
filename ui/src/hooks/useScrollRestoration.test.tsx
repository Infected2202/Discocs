import { act, render } from "@testing-library/react"
import { useRef } from "react"
import { createMemoryRouter, RouterProvider, Outlet } from "react-router"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { resetScrollPositions, useScrollRestoration } from "./useScrollRestoration"

// jsdom does no layout: scrollHeight/clientHeight are 0. Drive them from the
// test so "the list has not rendered yet" and "it has" can be told apart.
let contentHeight = 5000
const VIEWPORT = 500

function Shell() {
  const ref = useRef<HTMLElement>(null)
  useScrollRestoration(ref)
  return (
    <main ref={ref} data-testid="main">
      <Outlet />
    </main>
  )
}

function makeRouter() {
  return createMemoryRouter(
    [
      {
        Component: Shell,
        children: [
          { path: "/labels", element: <div>labels</div> },
          { path: "/labels/1", element: <div>label</div> },
        ],
      },
    ],
    { initialEntries: ["/labels"] }
  )
}

function main(container: HTMLElement) {
  return container.querySelector("main") as HTMLElement
}

// A real user scroll: the offset changes and the browser fires "scroll".
function userScrollTo(el: HTMLElement, top: number) {
  el.scrollTop = top
  el.dispatchEvent(new Event("scroll"))
}

beforeEach(() => {
  vi.useFakeTimers()
  resetScrollPositions()
  contentHeight = 5000
  vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(() => contentHeight)
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(() => VIEWPORT)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("useScrollRestoration", () => {
  it("returns to the saved offset when going back", async () => {
    const router = makeRouter()
    const { container } = render(<RouterProvider router={router} />)
    userScrollTo(main(container), 1800)

    await act(() => router.navigate("/labels/1"))
    expect(main(container).scrollTop).toBe(0)

    await act(() => router.navigate(-1))
    expect(main(container).scrollTop).toBe(1800)
  })

  it("keeps retrying until a late-rendering list is tall enough", async () => {
    const router = makeRouter()
    const { container } = render(<RouterProvider router={router} />)
    userScrollTo(main(container), 1800)
    await act(() => router.navigate("/labels/1"))

    contentHeight = 600 // list not loaded yet: only 100px reachable
    await act(() => router.navigate(-1))
    expect(main(container).scrollTop).toBe(100)

    contentHeight = 5000
    await act(() => vi.advanceTimersByTimeAsync(50))
    expect(main(container).scrollTop).toBe(1800)
  })

  it("stops fighting the user once they scroll themselves", async () => {
    const router = makeRouter()
    const { container } = render(<RouterProvider router={router} />)
    userScrollTo(main(container), 1800)
    await act(() => router.navigate("/labels/1"))

    contentHeight = 600
    await act(() => router.navigate(-1))
    main(container).dispatchEvent(new Event("wheel"))
    main(container).scrollTop = 40

    contentHeight = 5000
    await act(() => vi.advanceTimersByTimeAsync(100))
    expect(main(container).scrollTop).toBe(40)
  })

  it("starts a newly opened page from the top", async () => {
    const router = makeRouter()
    const { container } = render(<RouterProvider router={router} />)
    userScrollTo(main(container), 1800)

    await act(() => router.navigate("/labels/1"))

    expect(main(container).scrollTop).toBe(0)
  })

  it("leaves the scroll alone on a replace navigation", async () => {
    const router = makeRouter()
    const { container } = render(<RouterProvider router={router} />)
    userScrollTo(main(container), 1800)

    await act(() => router.navigate("/labels?sort=name", { replace: true }))

    expect(main(container).scrollTop).toBe(1800)
  })
})
