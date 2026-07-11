// Upgrade an artwork URL to a larger backend-rendered resolution (le=600).
export function hiresArtworkUrl(url: string | null | undefined, size = 600): string | undefined {
  if (!url) return undefined
  if (/size=\d+/.test(url)) return url.replace(/size=\d+/, `size=${size}`)
  const sep = url.includes("?") ? "&" : "?"
  return `${url}${sep}size=${size}`
}
