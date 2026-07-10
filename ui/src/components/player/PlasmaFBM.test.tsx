import { act, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const ogl = vi.hoisted(() => ({
  loseContext: vi.fn(),
  rendererCount: 0,
}))

vi.mock("ogl", () => {
  class Renderer {
    gl = {
      canvas: document.createElement("canvas"),
      drawingBufferWidth: 1,
      drawingBufferHeight: 1,
      getExtension: (name: string) => name === "WEBGL_lose_context" ? { loseContext: ogl.loseContext } : null,
    }

    constructor() { ogl.rendererCount += 1 }
    setSize() {}
    render() {}
  }

  class Program {
    uniforms: Record<string, { value: unknown }>

    constructor(_gl: unknown, options: { uniforms: Record<string, { value: unknown }> }) {
      this.uniforms = options.uniforms
    }
  }

  class Mesh {
    constructor(_gl: unknown, _options: unknown) {}
  }

  class Triangle {
    constructor(_gl: unknown) {}
  }

  return { Renderer, Program, Mesh, Triangle }
})

import PlasmaFBM from "./PlasmaFBM"

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

function setDocumentHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, value: hidden })
  document.dispatchEvent(new Event("visibilitychange"))
}

describe("PlasmaFBM background lifecycle", () => {
  beforeEach(() => {
    ogl.loseContext.mockReset()
    ogl.rendererCount = 0
    vi.stubGlobal("ResizeObserver", ResizeObserverStub)
    vi.stubGlobal("matchMedia", () => ({ matches: false }))
    vi.stubGlobal("requestAnimationFrame", () => 1)
    vi.stubGlobal("cancelAnimationFrame", vi.fn())
    setDocumentHidden(false)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    Object.defineProperty(document, "hidden", { configurable: true, value: false })
  })

  it("releases the WebGL context while hidden and recreates it on return", async () => {
    const view = render(<PlasmaFBM active accent="#3b6bff" />)
    expect(ogl.rendererCount).toBe(1)

    await act(async () => setDocumentHidden(true))
    await waitFor(() => expect(ogl.loseContext).toHaveBeenCalledTimes(1))
    expect(view.container.querySelector("canvas")).toBeNull()

    await act(async () => setDocumentHidden(false))
    await waitFor(() => expect(ogl.rendererCount).toBe(2))
    expect(view.container.querySelector("canvas")).not.toBeNull()
  })
})
