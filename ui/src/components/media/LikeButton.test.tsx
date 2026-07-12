import { fireEvent, render, screen } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import LikeButton from "./LikeButton"

const toggleLike = vi.fn()
const toggleAlbumLike = vi.fn()
const toggleArtistLike = vi.fn()

const state = {
  likedIds: new Set<number>(),
  likedAlbumIds: new Set<number>(),
  likedArtistIds: new Set<number>(),
  toggleLike,
  toggleAlbumLike,
  toggleArtistLike,
}

vi.mock("@/store/navidromeStore", () => ({
  useNavidromeStore: (selector: (s: typeof state) => unknown) => selector(state),
}))

beforeEach(() => {
  state.likedIds = new Set()
  state.likedAlbumIds = new Set()
  state.likedArtistIds = new Set()
  toggleLike.mockReset()
  toggleAlbumLike.mockReset()
  toggleArtistLike.mockReset()
})

describe("LikeButton", () => {
  it("трек: рисует thumbs-up и дёргает toggleLike с id трека", () => {
    render(<LikeButton entity="track" id={7} />)
    const btn = screen.getByRole("button", { name: "Like" })
    expect(btn.querySelector("svg.lucide-thumbs-up")).toBeTruthy()

    fireEvent.click(btn)
    expect(toggleLike).toHaveBeenCalledWith(7)
  })

  it("альбом: рисует heart и дёргает toggleAlbumLike", () => {
    render(<LikeButton entity="album" id={5} />)
    const btn = screen.getByRole("button")
    expect(btn.querySelector("svg.lucide-heart")).toBeTruthy()

    fireEvent.click(btn)
    expect(toggleAlbumLike).toHaveBeenCalledWith(5)
    expect(toggleLike).not.toHaveBeenCalled()
  })

  it("артист: читает likedArtistIds и дёргает toggleArtistLike", () => {
    state.likedArtistIds = new Set([3])
    render(<LikeButton entity="artist" id={3} />)
    const btn = screen.getByRole("button")

    fireEvent.click(btn)
    expect(toggleArtistLike).toHaveBeenCalledWith(3)
  })

  it("control-вариант заливает иконку, когда лайкнуто", () => {
    state.likedIds = new Set([7])
    render(<LikeButton entity="track" id={7} variant="control" />)
    expect(screen.getByRole("button").querySelector("svg")).toHaveAttribute("fill", "currentColor")
  })

  it("row-вариант не заливает даже при лайке — только красит", () => {
    state.likedIds = new Set([7])
    render(<LikeButton entity="track" id={7} variant="row" />)
    const btn = screen.getByRole("button")
    expect(btn.querySelector("svg")).toHaveAttribute("fill", "none")
    expect(btn.className).toContain("text-primary")
  })

  it("клик не всплывает к родителю (не запускает трек при лайке из строки)", () => {
    const parentClick = vi.fn()
    render(
      // eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events
      <div onClick={parentClick}>
        <LikeButton entity="track" id={7} variant="row" />
      </div>,
    )
    // Внутренний stopPropagation не должен пустить событие наверх к строке.
    fireEvent.click(screen.getByRole("button", { name: "Like" }))
    expect(parentClick).not.toHaveBeenCalled()
    expect(toggleLike).toHaveBeenCalledWith(7)
  })
})
