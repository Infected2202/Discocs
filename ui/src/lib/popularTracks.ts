import type { ArtistTopTrack } from "@/api/types"

/** Сколько треков «по Deezer» добирать до полки (листается страницами по 5). */
export const POPULAR_TRACKS_LIMIT = 20
const FALLBACK_COUNT = 5

/**
 * «Популярные треки» артиста. Бэкенд уже упорядочил список: сначала свои
 * прослушивания, затем rank Deezer. Прослушанные показываем все, треки только
 * с rank Deezer добираем до POPULAR_TRACKS_LIMIT; если сигналов нет ни у кого —
 * первые пять, чтобы полка не пустовала.
 */
export function pickPopularTracks(items: readonly ArtistTopTrack[]): ArtistTopTrack[] {
  const played = items.filter((t) => t.play_count > 0)
  const ranked = items.filter((t) => t.play_count === 0 && t.deezer_rank != null)
  const picked = [...played, ...ranked.slice(0, Math.max(0, POPULAR_TRACKS_LIMIT - played.length))]
  return picked.length > 0 ? picked : items.slice(0, FALLBACK_COUNT)
}
