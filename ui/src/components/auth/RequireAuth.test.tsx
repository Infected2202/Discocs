import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Routes, Route } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import RequireAuth from "./RequireAuth"

const getSession = vi.fn()
vi.mock("@/api/auth", () => ({
  getSession: () => getSession(),
}))

function renderGated() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
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

  it("redirects to login when not authenticated", async () => {
    getSession.mockResolvedValue({ authenticated: false, username: null, enabled: true })
    renderGated()
    expect(await screen.findByText("LOGIN PAGE")).toBeInTheDocument()
  })
})
