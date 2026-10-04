import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router"
import { beforeEach, describe, expect, it, vi } from "vitest"
import PeopleShelf, { personToCard } from "./PeopleShelf"
import { avatarUrl } from "@/lib/avatars"
import type { Person } from "@/api/social"

const navigate = vi.fn()
const peopleState: { data?: { items: Person[] }; isPending: boolean; isError: boolean } = {
  isPending: false,
  isError: false,
}

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>()
  return { ...actual, useNavigate: () => navigate }
})

vi.mock("@/api/hooks/usePeople", () => ({
  usePeople: () => peopleState,
}))

vi.mock("@/hooks/useColumns", () => ({
  useColumns: () => ({ cols: 6, isMobile: false }),
}))

const carol: Person = {
  username: "carol",
  avatar: "a03",
  now_playing: { track_id: 7, title: "Signals", artists: "Alpha, Beta", state: "playing" },
}
const bob: Person = { username: "bob", avatar: "a02", now_playing: null }

function renderShelf() {
  return render(
    <MemoryRouter>
      <PeopleShelf />
    </MemoryRouter>
  )
}

describe("PeopleShelf", () => {
  beforeEach(() => {
    navigate.mockReset()
    peopleState.data = undefined
    peopleState.isPending = false
    peopleState.isError = false
  })

  it("renders user cards in API order under the People title", () => {
    peopleState.data = { items: [carol, bob] }

    renderShelf()

    expect(screen.getByRole("heading", { name: "People" })).toBeInTheDocument()
    const avatars = screen.getAllByRole("img")
    expect(avatars.map((img) => img.getAttribute("alt"))).toEqual(["carol", "bob"])
    expect(avatars[0]).toHaveAttribute("src", avatarUrl("a03"))
    expect(avatars[1]).toHaveAttribute("src", avatarUrl("a02"))
  })

  it("shows what a playing user listens to, with a live dot, and only the login otherwise", () => {
    peopleState.data = { items: [carol, bob] }

    renderShelf()

    expect(screen.getByText("Signals — Alpha, Beta")).toBeInTheDocument()
    expect(screen.getAllByTestId("live-dot")).toHaveLength(1)
    expect(screen.queryByRole("button", { name: /^Play / })).not.toBeInTheDocument()
  })

  it("opens the profile on click", () => {
    peopleState.data = { items: [bob] }

    renderShelf()
    fireEvent.click(screen.getByText("bob"))

    expect(navigate).toHaveBeenCalledWith("/u/bob")
  })

  it("is hidden when there is nobody else", () => {
    peopleState.data = { items: [] }

    const { container } = renderShelf()

    expect(container).toBeEmptyDOMElement()
  })

  it("is hidden when the request failed", () => {
    peopleState.isError = true

    const { container } = renderShelf()

    expect(container).toBeEmptyDOMElement()
  })

  it("keeps the shelf's place with a skeleton while loading", () => {
    peopleState.isPending = true

    renderShelf()

    expect(screen.getByTestId("shelf-skeleton")).toBeInTheDocument()
    expect(screen.queryByRole("heading", { name: "People" })).not.toBeInTheDocument()
  })
})

describe("personToCard", () => {
  it("maps a playing user to a live user card", () => {
    expect(personToCard(carol)).toMatchObject({
      id: "carol",
      type: "user",
      title: "carol",
      subtitle: "Signals — Alpha, Beta",
      live: true,
    })
  })

  it("maps an idle user to a login-only card", () => {
    expect(personToCard(bob)).toMatchObject({ id: "bob", title: "bob", subtitle: null, live: false })
  })

  it("falls back to the title alone when Navidrome gives no artist", () => {
    const card = personToCard({
      ...carol,
      now_playing: { track_id: null, title: "Untitled", artists: "", state: "starting" },
    })
    expect(card.subtitle).toBe("Untitled")
  })
})
