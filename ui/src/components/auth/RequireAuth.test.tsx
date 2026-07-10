import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, act } from "@testing-library/react"
import { MemoryRouter, Routes, Route } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import RequireAuth from "./RequireAuth"
import { ApiError } from "@/api/client"

const getSession = vi.fn()
vi.mock("@/api/auth", () => ({
  getSession: () => getSession(),
}))

// RequireAuth intentionally redirects/unmounts immediately on a 401. Keep a
// rejection observer attached in the test too, so Vitest does not report the
// expected request failure as an unhandled rejection after that unmount.
function rejectedSession(error: Error): Promise<never> {
  const promise = Promise.reject(error)
  void promise.catch(() => undefined)
  return promise
}

function renderGated() {
  // retryDelay: 0 — the component retries network errors; don't wait between attempts.
  const qc = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/login" element={<div>LOGIN PAGE</div>} />
          <Route element={<RequireAuth />}>
            <Route index element={<div>HOME PAGE</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
  return qc
}

describe("RequireAuth", () => {
  beforeEach(() => getSession.mockReset())

  it("renders the protected content when authenticated", async () => {
    getSession.mockResolvedValue({ authenticated: true, username: "alice", enabled: true })
    renderGated()
    expect(await screen.findByText("HOME PAGE")).toBeInTheDocument()
  })

  it("redirects to login when the server says not authenticated", async () => {
    getSession.mockResolvedValue({ authenticated: false, username: null, enabled: true })
    renderGated()
    expect(await screen.findByText("LOGIN PAGE")).toBeInTheDocument()
  })

  it("redirects to login on a 401 without retrying", async () => {
    getSession.mockImplementation(() => rejectedSession(new ApiError(401, "unauthorized", "nope")))
    renderGated()
    expect(await screen.findByText("LOGIN PAGE")).toBeInTheDocument()
    expect(getSession).toHaveBeenCalledTimes(1)
  })

  it("shows a retry screen (not login) when the initial session check fails on the network", async () => {
    getSession.mockImplementation(() => rejectedSession(new TypeError("Failed to fetch")))
    renderGated()
    expect(await screen.findByText("Нет соединения с сервером")).toBeInTheDocument()
    expect(screen.queryByText("LOGIN PAGE")).not.toBeInTheDocument()
    // network errors are retried before giving up
    expect(getSession.mock.calls.length).toBeGreaterThan(1)
  })

  it("keeps the app rendered when a focus-refetch fails on the network", async () => {
    getSession.mockResolvedValueOnce({ authenticated: true, username: "alice", enabled: true })
    getSession.mockImplementation(() => rejectedSession(new TypeError("Failed to fetch")))
    const qc = renderGated()
    expect(await screen.findByText("HOME PAGE")).toBeInTheDocument()

    // Simulate the refetch that fires when the tab regains focus on mobile.
    await act(async () => {
      await qc.refetchQueries({ queryKey: ["auth", "session"] })
    })

    expect(screen.getByText("HOME PAGE")).toBeInTheDocument()
    expect(screen.queryByText("LOGIN PAGE")).not.toBeInTheDocument()
  })
})
