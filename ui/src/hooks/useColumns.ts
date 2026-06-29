import { useState, useEffect } from "react"

function getColumns(): number {
  const w = window.innerWidth
  if (w >= 1280) return 8
  if (w >= 1024) return 6
  if (w >= 640) return 4
  return 2
}

export function useColumns(): number {
  const [cols, setCols] = useState(getColumns)
  useEffect(() => {
    const handler = () => setCols(getColumns())
    window.addEventListener("resize", handler)
    return () => window.removeEventListener("resize", handler)
  }, [])
  return cols
}
