import "@testing-library/jest-dom"
import { afterEach } from "vitest"
import i18n from "./i18n"

// Nothing heavier belongs here: this file is evaluated once per test file (97
// of them), so an import added for one test is paid by all of them. Pulling
// @testing-library/react in to raise asyncUtilTimeout globally added ~68s of
// setup across the suite in build #379 and timed out two unrelated tests.
// Per-call `{ timeout }` on the few slow assertions instead.

// Tests assert against English copy; force it regardless of the host
// machine's locale or any language cached from a previous test run.
localStorage.clear()
await i18n.changeLanguage("en")

// A test that exercises the language switcher must not leak "ru" into
// later tests in the same file.
afterEach(async () => {
  if (i18n.resolvedLanguage !== "en") await i18n.changeLanguage("en")
})
