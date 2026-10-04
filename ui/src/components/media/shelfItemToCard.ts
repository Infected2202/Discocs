import type { Namespace, TFunction } from "i18next"
import type { MediaCardProps } from "./MediaCard"
import type { ShelfItem } from "@/api/types"
import i18n from "@/i18n"
import { formatRelativeTime } from "@/lib/relativeTime"

/**
 * Backend shelf item → MediaCard props (dashboard shelves and the "see all" page).
 * `locale` formats History's relative play time ("3 hr. ago"); callers pass `i18n.language`.
 */
export function shelfItemToCard<Ns extends Namespace>(
  item: ShelfItem,
  onPlay: (item: ShelfItem) => void,
  t: TFunction<Ns>,
  locale: string = i18n.language,
): MediaCardProps {
  const subtitle =
    item.entity_type === "label" && item.release_count !== undefined
      ? [t("releaseCount", { ns: "label", count: item.release_count }), item.top_genres?.join(", ")]
          .filter(Boolean)
          .join(" · ")
      : item.subtitle
  return {
    id: item.entity_id,
    type: item.entity_type,
    title: item.title,
    subtitle,
    subtitleLinks: item.subtitle_links,
    href: item.action?.target,
    reason: item.reason,
    meta: item.played_at ? formatRelativeTime(item.played_at, locale) || null : null,
    artwork: item.artwork,
    onPlay: item.play_action ? () => onPlay(item) : undefined,
  }
}
