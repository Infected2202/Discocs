import type { Namespace, TFunction } from "i18next"
import type { MediaCardProps } from "./MediaCard"
import type { ShelfItem } from "@/api/types"

/** Backend shelf item → MediaCard props (dashboard shelves and the "see all" page). */
export function shelfItemToCard<Ns extends Namespace>(
  item: ShelfItem,
  onPlay: (item: ShelfItem) => void,
  t: TFunction<Ns>,
): MediaCardProps {
  const subtitle =
    item.entity_type === "label" && item.release_count !== undefined
      ? t("releaseCount", { ns: "label", count: item.release_count })
      : item.subtitle
  return {
    id: item.entity_id,
    type: item.entity_type,
    title: item.title,
    subtitle,
    subtitleLinks: item.subtitle_links,
    href: item.action?.target,
    reason: item.reason,
    artwork: item.artwork,
    onPlay: item.play_action ? () => onPlay(item) : undefined,
  }
}
