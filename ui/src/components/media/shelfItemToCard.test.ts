import { describe, expect, it, vi } from "vitest"
import i18n from "@/i18n"
import { shelfItemToCard } from "./shelfItemToCard"
import type { ShelfItem } from "@/api/types"

function item(overrides: Partial<ShelfItem>): ShelfItem {
  return {
    id: "release:1",
    entity_type: "release",
    entity_id: 1,
    title: "Title",
    subtitle: "Backend subtitle",
    artwork: { url: null, source: "none", placeholder: true },
    reason: null,
    action: { type: "open", target: "/releases/1" },
    play_action: { type: "play", source_type: "release", source_id: 1 },
    ...overrides,
  }
}

describe("shelfItemToCard", () => {
  it("builds a localized release-count subtitle for label cards and gives them no play button", () => {
    const card = shelfItemToCard(
      item({
        id: "label:4",
        entity_type: "label",
        entity_id: 4,
        title: "Warp",
        subtitle: "",
        release_count: 3,
        action: { type: "open", target: "/labels/4" },
        play_action: null,
      }),
      vi.fn(),
      i18n.t,
    )

    expect(card.type).toBe("label")
    expect(card.subtitle).toBe("3 releases")
    expect(card.href).toBe("/labels/4")
    expect(card.onPlay).toBeUndefined()
  })

  it("keeps the backend subtitle and play action for other entities", () => {
    const onPlay = vi.fn()
    const shelfItem = item({})

    const card = shelfItemToCard(shelfItem, onPlay, i18n.t)
    card.onPlay?.()

    expect(card.subtitle).toBe("Backend subtitle")
    expect(onPlay).toHaveBeenCalledWith(shelfItem)
  })
})
