import { useEffect, useRef, useState } from "react"
import { playerLog } from "@/lib/playerLogger"
import styles from "./PlayerBackdrop.module.css"
import {
  artworkBackdropUrl,
  backdropAnimationState,
  resolveBackdropLayers,
} from "./playerBackdropUtils.ts"
import { readTrackAccentTransitionDurationMs } from "./plasmaUtils.ts"
import { preloadArtworkImage } from "./playerBarTransitionUtils.ts"

interface PlayerBackdropProps {
  readonly artworkUrl: string | null | undefined
  readonly isPlaying: boolean
}

export default function PlayerBackdrop({
  artworkUrl,
  isPlaying,
}: PlayerBackdropProps) {
  const backdropUrl = artworkBackdropUrl(artworkUrl)
  const animationPlayState = backdropAnimationState(isPlaying)
  const [visibleBackdropUrl, setVisibleBackdropUrl] = useState(backdropUrl)
  const [fadingBackdropUrl, setFadingBackdropUrl] = useState<string>()
  const visibleBackdropUrlRef = useRef(visibleBackdropUrl)

  useEffect(() => {
    if (backdropUrl === visibleBackdropUrlRef.current) return

    let cancelled = false
    let cleanupTimeout: number | undefined
    const reducedMotion = globalThis.matchMedia("(prefers-reduced-motion: reduce)")

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

      cleanupTimeout = globalThis.setTimeout(() => {
        if (!cancelled) {
          setFadingBackdropUrl((current) =>
            current === fadingUrl ? undefined : current
          )
        }
      }, readTrackAccentTransitionDurationMs())
    })

    return () => {
      cancelled = true
      if (cleanupTimeout !== undefined) globalThis.clearTimeout(cleanupTimeout)
    }
  }, [backdropUrl])

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
    </div>
  )
}
