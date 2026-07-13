import "@testing-library/jest-dom"
import { afterEach } from "vitest"
import i18n from "./i18n"

// Tests assert against English copy; force it regardless of the host
// machine's locale or any language cached from a previous test run.
localStorage.clear()
await i18n.changeLanguage("en")

// A test that exercises the language switcher must not leak "ru" into
// later tests in the same file.
afterEach(async () => {
  if (i18n.resolvedLanguage !== "en") await i18n.changeLanguage("en")
})
