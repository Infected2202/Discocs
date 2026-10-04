// Built-in user avatars (social features, plans/social-spec.md §2.4).
// The backend stores only a key from its whitelist (app/avatars.py); the
// images ship in the UI bundle as src/assets/avatars/<key>.webp.

const files = import.meta.glob<string>("../assets/avatars/*.webp", {
  eager: true,
  import: "default",
})

const AVATAR_URLS: Record<string, string> = Object.fromEntries(
  Object.entries(files).map(([path, url]) => [path.replace(/^.*\/([^/]+)\.webp$/, "$1"), url]),
)

/** Every built-in avatar key, sorted (a01, a02, …) — the picker grid order. */
export const AVATAR_KEYS: readonly string[] = Object.keys(AVATAR_URLS).sort()

/** URL of a built-in avatar, or null for a missing/unknown key. */
export function avatarUrl(key: string | null | undefined): string | null {
  if (!key) return null
  return AVATAR_URLS[key] ?? null
}
