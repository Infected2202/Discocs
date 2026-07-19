import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import SettingsPage from "./SettingsPage"

const apiFetch = vi.fn()

vi.mock("@/api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (path: string) => path,
}))


function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <SettingsPage />
    </QueryClientProvider>
  )
}

describe("SettingsPage", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    apiFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/v1/me/settings") {
        const patch = init?.body ? JSON.parse(String(init.body)) : {}
        return Promise.resolve({
          language: "en",
          transcoding_enabled: false,
          transcoding_bitrate_kbps: 192,
          ...patch,
        })
      }
      return Promise.resolve({
        model_key: "discogs_multi",
        status: "not_built",
        region_count: 0,
      })
    })
  })

  it("keeps instance credentials out of the public UI", async () => {
    renderSettings()

    expect(screen.queryByRole("heading", { name: "Navidrome" })).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Server URL")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Flow Profile" })).toBeInTheDocument()
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/v1/jobs/flow-profile/status"))
    expect(apiFetch).not.toHaveBeenCalledWith("/api/v1/settings/navidrome")
  })

  it("enables transcoding and unlocks the quality selector", async () => {
    renderSettings()
    const toggle = await screen.findByRole("switch", { name: "Transcoding" })
    const quality = screen.getByLabelText("Streaming quality")
    expect(quality).toBeDisabled()

    fireEvent.click(toggle)

    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(
      "/api/v1/me/settings",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ transcoding_enabled: true }) }),
    ))
    await waitFor(() => expect(quality).not.toBeDisabled())
  })
})
