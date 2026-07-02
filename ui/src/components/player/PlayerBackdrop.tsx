import { useEffect, useRef, useState } from "react"
import { playerLog } from "@/lib/playerLogger"
import styles from "./PlayerBackdrop.module.css"
import Plasma from "./Plasma.tsx"
import {
  artworkBackdropUrl,
  backdropAnimationState,
  resolvePlasmaLayer,
  resolveBackdropLayers,
} from "./playerBackdropUtils.ts"
import {
  keepPlasmaMountedBetweenTracks,
  readTrackAccentTransitionDurationMs,
} from "./plasmaUtils.ts"
import { preloadArtworkImage } from "./playerBarTransitionUtils.ts"

interface PlayerBackdropProps {
  artworkUrl: string | null | undefined
  isPlaying: boolean
}

export default function PlayerBackdrop({
  artworkUrl,
  isPlaying,
}: PlayerBackdropProps) {
  const backdropUrl = artworkBackdropUrl(artworkUrl)
  const animationPlayState = backdropAnimationState(isPlaying)
  const [plasmaReady, setPlasmaReady] = useState(false)
  const [plasmaAccent, setPlasmaAccent] = useState("")
  const [visibleBackdropUrl, setVisibleBackdropUrl] = useState(backdropUrl)
  const [fadingBackdropUrl, setFadingBackdropUrl] = useState<string>()
  const visibleBackdropUrlRef = useRef(visibleBackdropUrl)

  useEffect(() => {
    if (backdropUrl === visibleBackdropUrlRef.current) return

    let cancelled = false
    let cleanupTimeout: number | undefined
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)")

    void preloadArtworkImage(backdropUrl).then(() => {
      if (cancelled) return

      const { visibleUrl, fadingUrl } = resolveBackdropLayers(
        visibleBackdropUrlRef.current,
        backdropUrl
      )
      visibleBackdropUrlRef.current = visibleUrl
      setVisibleBackdropUrl(visibleUrl)
      setFadingBackdropUrl(reducedMotion.matches ? undefined : fadingUrl)

      playerLog("backdrop", "crossfade artwork", {
        from: fadingUrl ?? null,
        to: visibleUrl ?? null,
        durationMs: readTrackAccentTransitionDurationMs(),
      })

      if (!fadingUrl || reducedMotion.matches) return

      cleanupTimeout = window.setTimeout(() => {
        if (!cancelled) {
          setFadingBackdropUrl((current) =>
            current === fadingUrl ? undefined : current
          )
        }
      }, readTrackAccentTransitionDurationMs())
    })

    return () => {
      cancelled = true
      if (cleanupTimeout !== undefined) window.clearTimeout(cleanupTimeout)
    }
  }, [backdropUrl])

  useEffect(() => {
    if (!backdropUrl || !artworkUrl) {
      playerLog("backdrop", "plasma hidden", {
        artworkUrl: artworkUrl ?? null,
        backdropUrl: backdropUrl ?? null,
        reason: "missing-artwork",
      })
      setPlasmaReady(false)
      return
    }

    const ready = document.documentElement.dataset.trackAccentArtwork === artworkUrl
    const accent = ready
      ? (document.documentElement.dataset.trackAccentColor ?? "")
      : ""
    playerLog("backdrop", "artwork changed", {
      artworkUrl,
      backdropUrl,
      rootArtworkUrl: document.documentElement.dataset.trackAccentArtwork ?? "",
      accent,
      ready,
    })
    setPlasmaReady((current) => keepPlasmaMountedBetweenTracks(current, ready))
    if (ready) setPlasmaAccent(accent)

    const handleAccentChange = (event: Event) => {
      const detail = (event as CustomEvent<{
        artworkUrl?: string
        accent?: string
      }>).detail
      playerLog("backdrop", "accent event", {
        artworkUrl,
        eventArtworkUrl: detail?.artworkUrl ?? "",
        accent: detail?.accent ?? "",
      })
      if (detail?.artworkUrl === artworkUrl) {
        playerLog("backdrop", "plasma ready", { artworkUrl })
        setPlasmaAccent(detail?.accent ?? "")
        setPlasmaReady(true)
      }
    }

    window.addEventListener("trackaccentchange", handleAccentChange)
    return () => {
      window.removeEventListener("trackaccentchange", handleAccentChange)
    }
  }, [artworkUrl, backdropUrl])

  // Плазма живёт, пока есть фон; готовность accent'а даёт лишь fade-in по
  // прозрачности — без ремоунта и пересоздания WebGL-контекста между треками.
  const plasmaLayer = resolvePlasmaLayer(Boolean(visibleBackdropUrl), plasmaReady)

  return (
    <div className={styles.backdrop} aria-hidden>
      {fadingBackdropUrl && fadingBackdropUrl !== visibleBackdropUrl && (
        <div
          key={`${fadingBackdropUrl}:previous`}
          className={styles.artworkLayerPrevious}
        >
          <img
            src={fadingBackdropUrl}
            alt=""
            className={styles.artwork}
            style={{ animationPlayState }}
          />
        </div>
      )}
      {visibleBackdropUrl && (
        <div
          key={`${visibleBackdropUrl}:current`}
          className={styles.artworkLayerCurrent}
        >
          <img
            src={visibleBackdropUrl}
            alt=""
            className={styles.artwork}
            style={{ animationPlayState }}
          />
        </div>
      )}
      <div className={styles.scrim} />
      {plasmaLayer.mounted && (
        <div className={styles.plasma} style={{ opacity: plasmaLayer.opacity }}>
          <Plasma
            active={isPlaying}
            accent={plasmaAccent}
            speed={0.1}
            scale={30}
            opacity={0.3}
          />
        </div>
      )}
    </div>
  )
}
