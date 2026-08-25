import "@testing-library/jest-dom"
import { configure } from "@testing-library/react"
import { afterEach } from "vitest"
import i18n from "./i18n"

// findBy*/waitFor keep their own 1s budget — the testTimeout in vite.config.ts
// does not cover it. Under the full parallel run the contention documented
// there (jsdom is the bottleneck) can push a correct render past that second:
// build #378 failed LoginPage's redirect assertion that passed in #377 on
// identical code, first failure of that test ever. This is the same
// recalibration as testTimeout, and staying well below it keeps a genuinely
// stuck query reported as a findBy failure with a DOM dump rather than a bare
// test timeout.
configure({ asyncUtilTimeout: 5000 })

// Tests assert against English copy; force it regardless of the host
// machine's locale or any language cached from a previous test run.
localStorage.clear()
await i18n.changeLanguage("en")

// A test that exercises the language switcher must not leak "ru" into
// later tests in the same file.
afterEach(async () => {
  if (i18n.resolvedLanguage !== "en") await i18n.changeLanguage("en")
})
