import { act, render } from "@testing-library/react"
import { createMemoryRouter, RouterProvider, Outlet, type InitialEntry } from "react-router"
import { beforeEach, describe, expect, it } from "vitest"
import { usePlayerStore } from "@/store/playerStore"
import { usePlayerHistory } from "./usePlayerHistory"

function Shell() {
  usePlayerHistory()
  return <Outlet />
}

function makeRouter(initialEntries: InitialEntry[] = ["/labels"]) {
  return createMemoryRouter(
    [
      {
        Component: Shell,
        children: [
          { path: "/labels", element: <div>labels</div> },
          { path: "/labels/1", element: <div>label</div> },
          { path: "/artists/1", element: <div>artist</div> },
        ],
      },
    ],
    { initialEntries }
  )
}

const expanded = () => usePlayerStore.getState().expanded
const setExpanded = (value: boolean) => act(() => usePlayerStore.setState({ expanded: value }))

beforeEach(() => {
  usePlayerStore.setState({ expanded: false })
})

describe("usePlayerHistory", () => {
  it("pushes a same-URL history entry when the player expands", async () => {
    const router = makeRouter()
    render(<RouterProvider router={router} />)

    await setExpanded(true)

    expect(router.state.location.pathname).toBe("/labels")
    expect(router.state.location.state).toMatchObject({ playerOpen: true })
    expect(router.state.historyAction).toBe("PUSH")
  })

  it("collapses the player on back instead of leaving the page", async () => {
    const router = makeRouter()
    render(<RouterProvider router={router} />)
    await act(() => router.navigate("/labels/1"))
    await setExpanded(true)

    await act(() => router.navigate(-1))

    expect(expanded()).toBe(false)
    expect(router.state.location.pathname).toBe("/labels/1")
    // One more back now really leaves the page.
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe("/labels")
  })

  it("removes its history entry when collapsed by a button, so back leaves the page in one step", async () => {
    const router = makeRouter()
    render(<RouterProvider router={router} />)
    await act(() => router.navigate("/labels/1"))
    await setExpanded(true)

    await setExpanded(false)
    expect(router.state.location.state).toBeNull()
    expect(router.state.location.pathname).toBe("/labels/1")

    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe("/labels")
  })

  it("collapses when a link replaces the player's entry", async () => {
    const router = makeRouter()
    render(<RouterProvider router={router} />)
    await act(() => router.navigate("/labels/1"))
    await setExpanded(true)

    await act(() => router.navigate("/artists/1", { replace: true }))

    expect(expanded()).toBe(false)
    expect(router.state.location.pathname).toBe("/artists/1")
    // Entry was replaced, not stacked: back goes to the page under the player.
    await act(() => router.navigate(-1))
    expect(router.state.location.pathname).toBe("/labels")
  })

  it("re-expands on forward to the player's entry", async () => {
    const router = makeRouter()
    render(<RouterProvider router={router} />)
    await setExpanded(true)
    await act(() => router.navigate(-1))
    expect(expanded()).toBe(false)

    await act(() => router.navigate(1))

    expect(expanded()).toBe(true)
  })

  it("does not reopen after a reload on a player entry, and steps off it", async () => {
    const router = makeRouter([
      "/labels",
      { pathname: "/labels", state: { playerOpen: true } },
    ])
    render(<RouterProvider router={router} />)
    await act(async () => {})

    expect(expanded()).toBe(false)
    expect(router.state.location.state).toBeNull()
    expect(router.state.location.pathname).toBe("/labels")
  })
})
