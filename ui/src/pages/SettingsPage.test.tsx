import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
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
    apiFetch.mockResolvedValue({
      model_key: "discogs_multi",
      status: "not_built",
      region_count: 0,
    })
  })

  it("keeps instance credentials out of the public UI", async () => {
    renderSettings()

    expect(screen.queryByRole("heading", { name: "Navidrome" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Flow Profile" })).toBeInTheDocument()
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/v1/jobs/flow-profile/status"))
    expect(apiFetch).not.toHaveBeenCalledWith("/api/v1/settings/navidrome")
  })
})
