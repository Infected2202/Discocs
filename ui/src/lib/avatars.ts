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

/**
 * A stable pseudo-random rank per key: FNV-1a plus the murmur3 finalizer —
 * keys differing in one digit (a07/a08) must not end up next to each other.
 */
function rank(key: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  hash ^= hash >>> 16
  hash = Math.imul(hash, 0x85ebca6b) >>> 0
  hash ^= hash >>> 13
  hash = Math.imul(hash, 0xc2b2ae35) >>> 0
  hash ^= hash >>> 16
  return hash >>> 0
}

/**
 * Every built-in avatar key in the picker grid order: mixed, not a01, a02, …
 * The order is fixed (a hash of the key), so it never jumps between opens,
 * and newly added avatars land in random spots instead of at the end.
 */
export const AVATAR_KEYS: readonly string[] = Object.keys(AVATAR_URLS).sort(
  (a, b) => rank(a) - rank(b) || a.localeCompare(b),
)

/** URL of a built-in avatar, or null for a missing/unknown key. */
export function avatarUrl(key: string | null | undefined): string | null {
  if (!key) return null
  return AVATAR_URLS[key] ?? null
}
