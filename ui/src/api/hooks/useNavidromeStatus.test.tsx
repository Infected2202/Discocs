import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { useNavidromeStatus } from "./useNavidromeStatus"

const getSession = vi.fn()

vi.mock("../auth", () => ({
  getSession: () => getSession(),
}))

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

function wrapper({ children }: { readonly children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe("useNavidromeStatus", () => {
  beforeEach(() => {
    getSession.mockReset()
    queryClient.clear()
  })

  it("derives connectivity from the authenticated session without reading global settings", async () => {
    getSession.mockResolvedValue({
      authenticated: true,
      username: "alice",
      enabled: true,
    })

    const { result } = renderHook(() => useNavidromeStatus(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.status).toBe("connected")
    expect(getSession).toHaveBeenCalledTimes(1)
  })

  it("does not treat the disabled-auth bypass as a Navidrome identity", async () => {
    getSession.mockResolvedValue({
      authenticated: true,
      username: null,
      enabled: false,
    })

    const { result } = renderHook(() => useNavidromeStatus(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.status).toBe("disconnected")
  })
})
