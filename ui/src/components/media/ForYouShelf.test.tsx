import { render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import ForYouShelf from "./ForYouShelf"

const playFromEnvelope = vi.fn()
const flowProfileState: { data?: { available?: boolean } } = {}

vi.mock("@/store/playerStore", () => ({
  usePlayerStore: (selector: (state: { playFromEnvelope: typeof playFromEnvelope }) => unknown) =>
    selector({ playFromEnvelope }),
}))

vi.mock("@/api/hooks/useFlowProfile", () => ({
  useFlowProfile: () => flowProfileState,
}))

vi.mock("@/api/playlists", () => ({
  playLikes: vi.fn(),
}))

vi.mock("@/api/flow", () => ({
  startFlow: vi.fn(),
}))

vi.mock("./Shelf", () => ({
  default: ({ items }: { items: Array<{ title: string; subtitle?: string | null }> }) => (
    <div>
      {items.map((item) => (
        <div key={item.title}>
          <span>{item.title}</span>
          <span>{item.subtitle}</span>
        </div>
      ))}
    </div>
  ),
}))

describe("ForYouShelf", () => {
  beforeEach(() => {
    flowProfileState.data = undefined
  })

  it("shows the personal stream subtitle when Flow is available", () => {
    flowProfileState.data = { available: true }

    render(<ForYouShelf />)

    expect(screen.getByText("Your personal stream")).toBeInTheDocument()
  })

  it("shows the profile prompt when Flow exists but is not available", () => {
    flowProfileState.data = { available: false }

    render(<ForYouShelf />)

    expect(screen.getByText("Build your profile first")).toBeInTheDocument()
  })

  it("shows the loading subtitle while Flow profile is still loading", () => {
    render(<ForYouShelf />)

    expect(screen.getByText("Loading…")).toBeInTheDocument()
  })
})
