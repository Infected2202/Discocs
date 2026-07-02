import { createContext, useContext } from "react"

export const ScrollContext = createContext<React.RefObject<HTMLElement | null> | null>(null)

export function useScrollRef() {
  return useContext(ScrollContext)
}
