import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { useToggleLabelLike } from "./useLabel"
import type { LabelResponse } from "../types"

const apiFetch = vi.fn()

vi.mock("../client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  apiUrl: (path: string) => path,
}))

function labelResponse(liked: boolean): LabelResponse {
  return {
    label: {
      id: 5,
      name: "Trip",
      release_count: 2,
      liked,
      artwork: { url: null, source: "placeholder", placeholder: true },
      description: null,
      links: [],
    },
    links: {},
  }
}

function setup(initialLiked: boolean) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
  })
  queryClient.setQueryData(["label", 5], labelResponse(initialLiked))
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const wrapper = ({ children }: { readonly children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(() => useToggleLabelLike(5), { wrapper })
  return { queryClient, invalidate, result }
}

const liked = (queryClient: QueryClient) =>
  queryClient.getQueryData<LabelResponse>(["label", 5])?.label.liked

describe("useToggleLabelLike", () => {
  afterEach(() => apiFetch.mockReset())

  it("flips the heart before the server answers and refreshes the labels shelf", async () => {
    let resolve: (value: unknown) => void = () => {}
    apiFetch.mockReturnValueOnce(new Promise((r) => { resolve = r }))
    const { queryClient, invalidate, result } = setup(false)

    act(() => result.current.mutate(true))

    await waitFor(() => expect(liked(queryClient)).toBe(true))
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/labels/5/like", { method: "PUT" })

    resolve({ label_id: 5, liked: true })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["shelf", "labels"] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] })
  })

  it("unlikes with DELETE and rolls the heart back when the request fails", async () => {
    apiFetch.mockRejectedValueOnce(new Error("offline"))
    const { queryClient, result } = setup(true)

    act(() => result.current.mutate(false))

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/labels/5/like", { method: "DELETE" })
    expect(liked(queryClient)).toBe(true)
  })
})
