import { describe, expect, it } from "vitest"
import css from "./index.css?raw"

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
