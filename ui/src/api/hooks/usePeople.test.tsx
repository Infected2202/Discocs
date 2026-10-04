import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../client"
import { PEOPLE_REFETCH_INTERVAL_MS, peopleQueryOptions, usePeople } from "./usePeople"

const fetchPeople = vi.fn()

vi.mock("../social", () => ({
  fetchPeople: () => fetchPeople(),
}))

const people = {
  items: [
    {
      username: "bob",
      avatar: "a02",
      now_playing: { track_id: 3, title: "Signals", artists: "Alpha", state: "playing" },
    },
  ],
}

let queryClient: QueryClient

function wrapper({ children }: { readonly children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
}

describe("usePeople", () => {
  beforeEach(() => {
    fetchPeople.mockReset()
    fetchPeople.mockResolvedValue(people)
    queryClient = new QueryClient()
    // Only the polling interval is faked. vi.waitFor (not testing-library's
    // waitFor, which itself polls via the faked setInterval) keeps working.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] })
  })

  afterEach(() => {
    vi.useRealTimers()
    focusManager.setFocused(undefined)
    queryClient.clear()
  })

  it("configures ~15 s polling that pauses in the background", () => {
    const options = peopleQueryOptions()
    expect(options.refetchInterval).toBe(15_000)
    expect(PEOPLE_REFETCH_INTERVAL_MS).toBe(15_000)
    expect(options.refetchIntervalInBackground).toBe(false)
  })

  it("returns the people list and refetches on the interval", async () => {
    focusManager.setFocused(true)
    const { result } = renderHook(() => usePeople(), { wrapper })

    await vi.waitFor(() => expect(result.current.data).toEqual(people))
    expect(fetchPeople).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(PEOPLE_REFETCH_INTERVAL_MS)
    await vi.waitFor(() => expect(fetchPeople).toHaveBeenCalledTimes(2))
  })

  it("does not poll while the document is hidden", async () => {
    focusManager.setFocused(true)
    const { result } = renderHook(() => usePeople(), { wrapper })
    await vi.waitFor(() => expect(result.current.isSuccess).toBe(true))

    focusManager.setFocused(false)
    vi.advanceTimersByTime(PEOPLE_REFETCH_INTERVAL_MS * 3)
    await Promise.resolve()

    expect(fetchPeople).toHaveBeenCalledTimes(1)
  })

  it("does not retry a 403 (no signed-in user)", () => {
    const retry = peopleQueryOptions().retry
    expect(retry(0, new ApiError(403, "forbidden", "no user"))).toBe(false)
    expect(retry(0, new TypeError("Failed to fetch"))).toBe(true)
    expect(retry(1, new TypeError("Failed to fetch"))).toBe(false)
  })
})
