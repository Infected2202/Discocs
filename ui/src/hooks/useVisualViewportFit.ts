import { useEffect, useState } from "react"

export interface VisualViewportFit {
  /** Extra vertical offset, in px, for an overlay centred on the layout viewport. */
  readonly offset: number
  /** Height the overlay must not exceed, or null while unconstrained. */
  readonly maxHeight: number | null
}

const UNCONSTRAINED: VisualViewportFit = { offset: 0, maxHeight: null }

/** Breathing room so the shifted overlay never touches the viewport edges. */
const MARGIN = 32

/**
 * Keeps a viewport-centred overlay inside the *visual* viewport.
 *
 * An on-screen keyboard shrinks the visual viewport without touching the
 * layout viewport, so a `fixed` element centred on the latter ends up sitting
 * behind the keyboard — on a phone that can bury half the dialog. This
 * reports how far to move it and how tall it may be.
 *
 * Desktop browsers report a visual viewport identical to the layout one, so
 * the offset stays 0 there and nothing moves. Browsers without the API at all
 * get the same inert result rather than a broken layout.
 */
export function useVisualViewportFit(active: boolean): VisualViewportFit {
  const [fit, setFit] = useState<VisualViewportFit>(UNCONSTRAINED)

  useEffect(() => {
    const viewport = globalThis.visualViewport
    if (!active || !viewport) {
      setFit(UNCONSTRAINED)
      return
    }

    const update = () => {
      const layoutHeight = document.documentElement.clientHeight
      const visualCentre = viewport.offsetTop + viewport.height / 2
      setFit({
        offset: Math.round(visualCentre - layoutHeight / 2),
        maxHeight: Math.max(0, Math.round(viewport.height - MARGIN)),
      })
    }

    update()
    // `resize` covers the keyboard opening and closing; `scroll` covers the
    // page being panned while it is open, which moves offsetTop.
    viewport.addEventListener("resize", update)
    viewport.addEventListener("scroll", update)
    return () => {
      viewport.removeEventListener("resize", update)
      viewport.removeEventListener("scroll", update)
    }
  }, [active])

  return fit
}
