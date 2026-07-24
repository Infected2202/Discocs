import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Routes, Route } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import RequireAuth, { errorDetail, sessionGateState } from "./RequireAuth"
import { ApiError } from "@/api/client"

const getSession = vi.fn()
vi.mock("@/api/auth", () => ({
  getSession: () => getSession(),
}))

function renderGated() {
  const qc = new QueryClient()
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

  it("uses a 401 as an explicit logout signal", () => {
    expect(sessionGateState(undefined, new ApiError(401, "unauthorized", "nope"), true)).toBe("login")
  })

  it("uses the retry screen for an unknown session after a network error", () => {
    expect(sessionGateState(undefined, new TypeError("Failed to fetch"), true)).toBe("offline")
  })

  it("keeps the app state after a failed focus refetch of a known session", () => {
    const session = { authenticated: true, username: "alice", enabled: true }
    expect(sessionGateState(session, new TypeError("Failed to fetch"), true)).toBe("app")
  })

  it("shows the real error message on the retry screen, not just the generic copy", async () => {
    getSession.mockRejectedValue(new SyntaxError("Unexpected token '<', \"<!doctype \"... is not valid JSON"))
    renderGated()
    expect(await screen.findByText(/Unexpected token/)).toBeInTheDocument()
  })

  it("extracts a readable message from Error instances and stringifies anything else", () => {
    expect(errorDetail(new TypeError("Failed to fetch"))).toBe("Failed to fetch")
    expect(errorDetail("plain string")).toBe("plain string")
  })
})
