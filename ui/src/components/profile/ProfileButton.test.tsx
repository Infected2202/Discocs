import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import ProfileButton from "./ProfileButton"

const navigate = vi.fn()
const useNavidromeStatus = vi.fn()
const getSession = vi.fn()
const logout = vi.fn()
const redirectToLogin = vi.fn()

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return {
    ...actual,
    useNavigate: () => navigate,
  }
})

vi.mock("@/api/hooks/useNavidromeStatus", () => ({
  useNavidromeStatus: () => useNavidromeStatus(),
}))

vi.mock("@/api/auth", () => ({
  getSession: () => getSession(),
  logout: () => logout(),
}))

vi.mock("@/lib/authRedirect", () => ({
  redirectToLogin: () => redirectToLogin(),
}))

function renderProfileButton() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ProfileButton />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe("ProfileButton", () => {
  beforeEach(() => {
    navigate.mockReset()
    useNavidromeStatus.mockReset()
    getSession.mockReset()
    logout.mockReset()
    redirectToLogin.mockReset()
    getSession.mockResolvedValue({ username: "alice" })
  })

  it("shows the loading status while Navidrome state is pending", async () => {
    useNavidromeStatus.mockReturnValue({ status: "disconnected", isLoading: true })

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))

    expect(await screen.findByText("Checking…")).toBeInTheDocument()
  })

  it("shows the connected status when Navidrome is available", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))

    expect(await screen.findByText("Navidrome authenticated")).toBeInTheDocument()
  })

  it("shows the current username as a separate identity indicator", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(await screen.findByTitle("Profile: alice"))

    expect(await screen.findByText("alice")).toBeInTheDocument()
    expect(screen.getByText("Signed in")).toBeInTheDocument()
  })

  it("redirects after logout even when the API call succeeds", async () => {
    useNavidromeStatus.mockReturnValue({ status: "disconnected", isLoading: false })
    logout.mockResolvedValue(undefined)

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))
    fireEvent.click(await screen.findByRole("button", { name: /sign out.*alice/i }))

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(redirectToLogin).toHaveBeenCalledTimes(1))
  })
})
