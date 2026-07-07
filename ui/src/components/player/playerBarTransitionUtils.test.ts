import { describe, expect, it, vi } from "vitest"
import { preloadArtworkImage } from "./playerBarTransitionUtils"

describe("preloadArtworkImage", () => {
  it("waits for decode after a load callback without returning a promise callback", async () => {
    const decode = vi.fn().mockResolvedValue(undefined)
    const image = {
      complete: false,
      naturalWidth: 100,
      onload: null as (() => void) | null,
      onerror: null as (() => void) | null,
      decode,
      _src: "",
      get src() {
        return this._src
      },
      set src(value: string) {
        this._src = value
      },
    }

    const pending = preloadArtworkImage("cover://artwork", () => image)
    expect(typeof image.onload).toBe("function")

    image.onload?.()
    await pending

    expect(image.src).toBe("cover://artwork")
    expect(decode).toHaveBeenCalledTimes(1)
  })
})
