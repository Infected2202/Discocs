import { describe, expect, it, vi } from "vitest"

vi.mock("signalsmith-stretch?url", () => ({ default: "assets/stretch.worklet.mjs" }))

import {
  createSignalsmithNode,
  resolveSignalsmithWorkletUrl,
  signalsmithAssetPlan,
} from "./assets"
import type { StretchNode } from "./types"

describe("Signalsmith Vite assets", () => {
  it("resolves the emitted worklet asset under the production base URL", () => {
    expect(resolveSignalsmithWorkletUrl("assets/stretch.mjs", "https://music.test/app/"))
      .toBe("https://music.test/app/assets/stretch.mjs")
    expect(resolveSignalsmithWorkletUrl("/assets/stretch.mjs", "https://music.test/app/"))
      .toBe("https://music.test/assets/stretch.mjs")
  })

  it("records that Vite emits the module/worklet while WASM remains embedded", () => {
    expect(signalsmithAssetPlan("https://music.test/app/")).toEqual({
      module: "vite-dynamic-import",
      workletUrl: "https://music.test/app/assets/stretch.worklet.mjs",
      wasm: "embedded-data-uri",
    })
  })

  it("assigns the emitted worklet URL before creating the node", async () => {
    const node = {} as StretchNode
    const factory = Object.assign(vi.fn(async () => node), { moduleUrl: undefined as string | undefined })
    const loader = vi.fn(async () => ({ default: factory }))
    const context = {} as AudioContext

    await expect(createSignalsmithNode(context, undefined, loader, "https://music.test/app/"))
      .resolves.toBe(node)
    expect(factory.moduleUrl).toBe("https://music.test/app/assets/stretch.worklet.mjs")
    expect(factory).toHaveBeenCalledWith(context, undefined)
  })
})
