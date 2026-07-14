import { useState, useEffect } from "react"

export interface ColumnLayout {
  /** Number of cards per row / per slider page. */
  readonly cols: number
  /** True on narrow (phone) viewports, where shelves use the touch layout. */
  readonly isMobile: boolean
}

function getLayout(): ColumnLayout {
  const w = window.innerWidth
  if (w >= 1280) return { cols: 8, isMobile: false }
  if (w >= 1024) return { cols: 6, isMobile: false }
  if (w >= 640) return { cols: 4, isMobile: false }
  // Phones: 4 across — 2 felt absurdly sparse.
  return { cols: 4, isMobile: true }
}

export function useColumns(): ColumnLayout {
  const [layout, setLayout] = useState(getLayout)
  useEffect(() => {
    const handler = () => setLayout(getLayout())
    window.addEventListener("resize", handler)
    return () => window.removeEventListener("resize", handler)
  }, [])
  return layout
}
