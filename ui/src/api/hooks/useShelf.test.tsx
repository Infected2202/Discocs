import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useShelf } from "./useShelf"

const apiFetch = vi.fn()
const apiUrl = vi.fn()

vi.mock("../client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (...args: unknown[]) => apiUrl(...args),
}))

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe("useShelf", () => {
  afterEach(() => {
    apiFetch.mockReset()
    apiUrl.mockReset()
  })

  it("requests the first page and uses the returned next offset for pagination", async () => {
    apiUrl
      .mockReturnValueOnce("/api/v1/dashboard/shelves/flow?limit=24&offset=0")
      .mockReturnValueOnce("/api/v1/dashboard/shelves/flow?limit=24&offset=24")
    apiFetch
      .mockResolvedValueOnce({ items: [], limit: 24, offset: 0, next_offset: 24 })
      .mockResolvedValueOnce({ items: [], limit: 24, offset: 24, next_offset: null })

    const { result } = renderHook(() => useShelf("flow", 24), {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    await result.current.fetchNextPage()

    expect(apiUrl).toHaveBeenNthCalledWith(1, "/api/v1/dashboard/shelves/flow", { limit: 24, offset: 0 })
    expect(apiUrl).toHaveBeenNthCalledWith(2, "/api/v1/dashboard/shelves/flow", { limit: 24, offset: 24 })
    expect(apiFetch).toHaveBeenCalledTimes(2)
  })
})
