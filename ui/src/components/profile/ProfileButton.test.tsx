import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { avatarUrl } from "@/lib/avatars"
import ProfileButton from "./ProfileButton"

const navigate = vi.fn()
const useNavidromeStatus = vi.fn()
const getSession = vi.fn()
const logout = vi.fn()
const redirectToLogin = vi.fn()
const getUserSettings = vi.fn()
const updateUserSettings = vi.fn()
const fetchPeople = vi.fn()

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

vi.mock("@/api/social", () => ({
  fetchPeople: () => fetchPeople(),
}))

vi.mock("@/lib/authRedirect", () => ({
  redirectToLogin: () => redirectToLogin(),
}))

vi.mock("@/api/settings", () => ({
  getUserSettings: () => getUserSettings(),
  updateUserSettings: (patch: unknown) => updateUserSettings(patch),
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
    getUserSettings.mockReset()
    updateUserSettings.mockReset()
    fetchPeople.mockReset()
    fetchPeople.mockResolvedValue({
      items: [
        { username: "bob", avatar: "a02", now_playing: null },
        { username: "alice", avatar: "a03", now_playing: null },
      ],
    })
    getSession.mockResolvedValue({ username: "alice" })
    getUserSettings.mockResolvedValue({ language: "en" })
    updateUserSettings.mockResolvedValue({ language: "ru" })
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

  it("links «My profile» to the current user's profile page", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(await screen.findByTitle("Profile: alice"))
    fireEvent.click(await screen.findByRole("button", { name: /my profile/i }))

    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith("/u/alice")
  })

  it("shows the profile avatar of the signed-in user next to the login", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(await screen.findByTitle("Profile: alice"))

    const avatar = await screen.findByRole("img", { name: "alice" })
    expect(avatar).toHaveAttribute("src", avatarUrl("a03"))
    expect(avatarUrl("a03")).not.toBe(avatarUrl("a02"))
  })

  it("falls back to the login's first letter until the avatar is known", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })
    fetchPeople.mockResolvedValue({ items: [] })

    renderProfileButton()
    fireEvent.click(await screen.findByTitle("Profile: alice"))

    expect(await screen.findByLabelText("alice")).toHaveTextContent("A")
    expect(screen.queryByRole("img", { name: "alice" })).not.toBeInTheDocument()
  })

  it("makes the login a link to the profile and closes the popover on click", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(await screen.findByTitle("Profile: alice"))
    const link = await screen.findByRole("link", { name: "alice" })
    expect(link).toHaveAttribute("href", "/u/alice")

    fireEvent.click(link)

    await waitFor(() => expect(screen.queryByText("Signed in")).not.toBeInTheDocument())
  })

  it("closes the popover when «My profile» is used", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(await screen.findByTitle("Profile: alice"))
    fireEvent.click(await screen.findByRole("button", { name: /my profile/i }))

    await waitFor(() => expect(screen.queryByText("Signed in")).not.toBeInTheDocument())
  })

  it("redirects after the server confirms logout", async () => {
    useNavidromeStatus.mockReturnValue({ status: "disconnected", isLoading: false })
    logout.mockResolvedValue(undefined)

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))
    fireEvent.click(await screen.findByRole("button", { name: /sign out.*alice/i }))

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(redirectToLogin).toHaveBeenCalledTimes(1))
  })

  it("stays in the app and reports a failed server-side logout", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })
    logout.mockRejectedValue(new Error("offline"))

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))
    fireEvent.click(await screen.findByRole("button", { name: /sign out.*alice/i }))

    await waitFor(() => expect(logout).toHaveBeenCalledTimes(1))
    expect(redirectToLogin).not.toHaveBeenCalled()
    expect(await screen.findByRole("alert")).toHaveTextContent("Sign out failed. Try again.")
  })

  it("shows a language switcher with the active language highlighted", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))

    const english = await screen.findByRole("button", { name: "English" })
    const russian = await screen.findByRole("button", { name: "Русский" })
    expect(english).toBeInTheDocument()
    expect(russian).toBeInTheDocument()
  })

  it("patches the language setting when a different language is picked", async () => {
    useNavidromeStatus.mockReturnValue({ status: "connected", isLoading: false })

    renderProfileButton()
    fireEvent.click(screen.getByTitle("Profile"))
    fireEvent.click(await screen.findByRole("button", { name: "Русский" }))

    await waitFor(() =>
      expect(updateUserSettings).toHaveBeenCalledWith({ language: "ru" })
    )
  })
})
