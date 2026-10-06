/// <reference types="node" />
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it } from "vitest"

// Read from disk: vitest swaps the content of imported .css files (also `?raw`)
// for an empty string. The test runner's cwd is the ui/ package root.
const css = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8")

// The hover-only hiding of the track menu button is an unlayered rule, so it
// beats Tailwind utilities. Scoped to a list row it is safe; unscoped it hides
// the button in the player on desktop, where `opacity-100` cannot win.
describe("track menu trigger visibility", () => {
  it("hides the trigger only inside a list row, never globally", () => {
    const hiding = [...css.matchAll(/([^{}]*)\{\s*opacity:\s*0;?\s*\}/g)]
      .map((match) => match[1].trim())
      .filter((selector) => selector.includes("track-menu-trigger"))

    expect(hiding.length).toBeGreaterThan(0)
    for (const selector of hiding) {
      expect(selector).toMatch(/\.group\\\/row\s+\.track-menu-trigger/)
    }
  })

  it("keeps an open or focused trigger visible inside a row", () => {
    expect(css).toMatch(/\.track-menu-trigger\[data-state="open"\]/)
    expect(css).toMatch(/\.track-menu-trigger:focus-visible/)
  })
})
