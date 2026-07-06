import { useEffect, useRef } from "react"
import {
  DEFAULT_ARTWORK_PALETTE,
  extractArtworkPalette,
  type ArtworkPalette,
} from "@/lib/artworkPalette"
import { playerLog } from "@/lib/playerLogger"
import { usePlayerStore } from "@/store/playerStore"

function applyPalette(
  palette: ArtworkPalette,
  artworkUrl: string | null | undefined,
) {
  playerLog("theme", "apply palette", { accent: palette.accent, foreground: palette.foreground, url: artworkUrl ?? null })
  const root = document.documentElement
  root.style.setProperty("--track-accent", palette.accent)
  root.style.setProperty("--track-accent-foreground", palette.foreground)
  root.dataset.trackAccentArtwork = artworkUrl ?? ""
  root.dataset.trackAccentColor = palette.accent
  globalThis.dispatchEvent(
    new CustomEvent("trackaccentchange", {
      detail: {
        artworkUrl: artworkUrl ?? "",
        accent: palette.accent,
        foreground: palette.foreground,
      },
    })
  )
}

export function useArtworkTheme() {
  const artworkUrl = usePlayerStore((state) => state.currentTrack?.artwork?.url)
  const requestId = useRef(0)

  useEffect(() => {
    const currentRequest = ++requestId.current
    const controller = new AbortController()

    if (!artworkUrl) {
      applyPalette(DEFAULT_ARTWORK_PALETTE, null)
      return () => controller.abort()
    }

    extractArtworkPalette(artworkUrl, controller.signal)
      .then((palette) => {
        if (requestId.current === currentRequest) applyPalette(palette, artworkUrl)
      })
      .catch((error: unknown) => {
        if (requestId.current !== currentRequest) return
        if (error instanceof DOMException && error.name === "AbortError") return
        playerLog("theme", "palette extraction failed — using default", { error: String(error), url: artworkUrl })
        applyPalette(DEFAULT_ARTWORK_PALETTE, artworkUrl)
      })

    return () => controller.abort()
  }, [artworkUrl])
}
