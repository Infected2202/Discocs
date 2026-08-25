import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter, Routes, Route } from "react-router"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import LoginPage from "./LoginPage"

const getSession = vi.fn()
const login = vi.fn()
vi.mock("@/api/auth", () => ({
  getSession: () => getSession(),
  login: (u: string, p: string) => login(u, p),
}))

// WebGL background is irrelevant here and does not run under jsdom.
vi.mock("@/components/player/PlasmaFBM", () => ({ default: () => null }))

function renderLogin(state?: { from?: { pathname: string } }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[{ pathname: "/login", state }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div>HOME PAGE</div>} />
          <Route path="/settings" element={<div>SETTINGS PAGE</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("LoginPage", () => {
  beforeEach(() => {
    getSession.mockReset()
    login.mockReset()
  })

  it("shows the form when there is no valid session", async () => {
    getSession.mockResolvedValue({ authenticated: false, username: null, enabled: true })
    renderLogin()
    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument()
  })

  // The redirect waits on a query, an effect and a route swap. That chain is
  // deterministic, but findBy*'s own budget is 1s regardless of testTimeout,
  // and under the full parallel run it ran out in build #378 — the first
  // failure of this test ever, on code that passed in #377.
  const REDIRECT_TIMEOUT = { timeout: 5000 }

  it("skips the form when the session is still valid", async () => {
    getSession.mockResolvedValue({ authenticated: true, username: "alice", enabled: true })
    renderLogin()
    expect(await screen.findByText("HOME PAGE", undefined, REDIRECT_TIMEOUT)).toBeInTheDocument()
  })

  it("returns to the page the user was bounced from", async () => {
    getSession.mockResolvedValue({ authenticated: true, username: "alice", enabled: true })
    renderLogin({ from: { pathname: "/settings" } })
    expect(await screen.findByText("SETTINGS PAGE", undefined, REDIRECT_TIMEOUT)).toBeInTheDocument()
  })

  it("stays on the form when the session check itself fails", async () => {
    getSession.mockRejectedValue(new TypeError("Failed to fetch"))
    renderLogin()
    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument()
    expect(screen.queryByText("HOME PAGE")).not.toBeInTheDocument()
  })

  it("switches the form copy to Russian via the language toggle", async () => {
    getSession.mockResolvedValue({ authenticated: false, username: null, enabled: true })
    renderLogin()
    await screen.findByRole("button", { name: /sign in/i })

    fireEvent.click(screen.getByRole("button", { name: "ru" }))

    expect(await screen.findByPlaceholderText("Логин")).toBeInTheDocument()
  })
})
