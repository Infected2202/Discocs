import { afterEach, describe, expect, it, vi } from "vitest"
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

  it("captions a label with its release count only, without styles", () => {
    const card = shelfItemToCard(
      item({
        id: "label:5",
        entity_type: "label",
        entity_id: 5,
        title: "Suara",
        subtitle: "",
        release_count: 588,
        top_genres: ["Techno", "Tech House"],
        action: { type: "open", target: "/labels/5" },
        play_action: null,
      }),
      vi.fn(),
      i18n.t,
    )

    expect(card.subtitle).toBe("588 releases")
  })

  it("keeps the backend subtitle and play action for other entities", () => {
    const onPlay = vi.fn()
    const shelfItem = item({})

    const card = shelfItemToCard(shelfItem, onPlay, i18n.t)
    card.onPlay?.()

    expect(card.subtitle).toBe("Backend subtitle")
    expect(onPlay).toHaveBeenCalledWith(shelfItem)
  })

  describe("History relative time", () => {
    const now = new Date(2026, 9, 4, 12, 0, 0)
    const threeHoursAgo = new Date(now.getTime() - 3 * 60 * 60_000).toISOString()

    afterEach(() => {
      vi.useRealTimers()
    })

    it("turns played_at into relative-time meta in the given locale", () => {
      vi.useFakeTimers({ toFake: ["Date"] })
      vi.setSystemTime(now)
      const shelfItem = item({ entity_type: "track", played_at: threeHoursAgo })

      const en = shelfItemToCard(shelfItem, vi.fn(), i18n.t, "en")
      const ru = shelfItemToCard(shelfItem, vi.fn(), i18n.t, "ru")

      const fmt = (locale: string) => new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" })
      expect(en.meta).toBe(fmt("en").format(-3, "hour"))
      expect(ru.meta).toBe(fmt("ru").format(-3, "hour"))
    })

    it("has no meta for items without played_at", () => {
      expect(shelfItemToCard(item({}), vi.fn(), i18n.t, "en").meta).toBeNull()
      expect(shelfItemToCard(item({ played_at: "garbage" }), vi.fn(), i18n.t, "en").meta).toBeNull()
    })
  })
})
