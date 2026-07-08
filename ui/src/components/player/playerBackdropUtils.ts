const BACKDROP_ARTWORK_SIZE = 320
const SIZE_PARAM_PATTERN = /([?&])size=\d+/

export type BackdropAnimationState = "running" | "paused"
export interface BackdropLayerState {
  visibleUrl: string | undefined
  fadingUrl: string | undefined
}

function splitHash(url: string) {
  const hashIndex = url.indexOf("#")
  if (hashIndex < 0) {
    return { base: url, hash: "" }
  }
  return {
    base: url.slice(0, hashIndex),
    hash: url.slice(hashIndex),
  }
}

export function artworkBackdropUrl(
  url: string | null | undefined,
  size = BACKDROP_ARTWORK_SIZE
): string | undefined {
  if (!url) return undefined

  const { base, hash } = splitHash(url)

  if (SIZE_PARAM_PATTERN.test(base)) {
    return `${base.replace(SIZE_PARAM_PATTERN, `$1size=${size}`)}${hash}`
  }

  return `${base}${base.includes("?") ? "&" : "?"}size=${size}${hash}`
}

export function backdropAnimationState(
  isPlaying: boolean
): BackdropAnimationState {
  return isPlaying ? "running" : "paused"
}

export function resolveBackdropLayers(
  currentVisibleUrl: string | undefined,
  nextUrl: string | undefined
): BackdropLayerState {
  if (currentVisibleUrl === nextUrl) {
    return {
      visibleUrl: currentVisibleUrl,
      fadingUrl: undefined,
    }
  }

  return {
    visibleUrl: nextUrl,
    fadingUrl: currentVisibleUrl,
  }
}
