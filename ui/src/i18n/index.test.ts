import { afterEach, describe, expect, it } from "vitest"
import i18n, { LANGUAGE_STORAGE_KEY } from "./index"

describe("i18n", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en")
  })

  it("translates a known key in both supported languages", async () => {
    await i18n.changeLanguage("en")
    expect(i18n.t("actions.cancel", { ns: "common" })).toBe("Cancel")

    await i18n.changeLanguage("ru")
    expect(i18n.t("actions.cancel", { ns: "common" })).toBe("Отмена")
  })

  it("persists the active language to localStorage under the shared key", async () => {
    await i18n.changeLanguage("ru")
    expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("ru")
  })

  it("falls back to English for a namespace key missing in the target language", () => {
    const enOnly = i18n.addResource("en", "common", "onlyInEnglish", "English only")
    expect(enOnly).toBeTruthy()
    expect(i18n.t("onlyInEnglish", { ns: "common", lng: "ru" })).toBe("English only")
  })

  it("selects the correct Russian plural category for a counted key", async () => {
    await i18n.changeLanguage("ru")
    expect(i18n.t("itemCount", { ns: "media", count: 1 })).toBe("1 элемент")
    expect(i18n.t("itemCount", { ns: "media", count: 2 })).toBe("2 элемента")
    expect(i18n.t("itemCount", { ns: "media", count: 5 })).toBe("5 элементов")
  })

  it("translates dashboard shelf titles by their backend key in both languages", async () => {
    await i18n.changeLanguage("en")
    expect(i18n.t("shelves.recently_added", { ns: "dashboard" })).toBe("Recently Added")
    expect(i18n.t("shelves.liked_artists", { ns: "dashboard" })).toBe("Favourite Artists")

    await i18n.changeLanguage("ru")
    expect(i18n.t("shelves.recently_added", { ns: "dashboard" })).toBe("Недавно добавленное")
    expect(i18n.t("shelves.liked_artists", { ns: "dashboard" })).toBe("Любимые артисты")
  })
})
