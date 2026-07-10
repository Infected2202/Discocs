import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
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
    expect(await screen.findByRole("button", { name: /войти/i })).toBeInTheDocument()
  })

  it("skips the form when the session is still valid", async () => {
    getSession.mockResolvedValue({ authenticated: true, username: "alice", enabled: true })
    renderLogin()
    expect(await screen.findByText("HOME PAGE")).toBeInTheDocument()
  })

  it("returns to the page the user was bounced from", async () => {
    getSession.mockResolvedValue({ authenticated: true, username: "alice", enabled: true })
    renderLogin({ from: { pathname: "/settings" } })
    expect(await screen.findByText("SETTINGS PAGE")).toBeInTheDocument()
  })

  it("stays on the form when the session check itself fails", async () => {
    getSession.mockRejectedValue(new TypeError("Failed to fetch"))
    renderLogin()
    expect(await screen.findByRole("button", { name: /войти/i })).toBeInTheDocument()
    expect(screen.queryByText("HOME PAGE")).not.toBeInTheDocument()
  })
})
