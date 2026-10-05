import type { TFunction } from "i18next"
import type { ProfileShelfItem } from "@/api/profile"
import type { PlaylistSummary, ShelfItem } from "@/api/types"
import type { MediaCardProps } from "@/components/media/MediaCard"
import { shelfItemToCard } from "@/components/media/shelfItemToCard"

/** "N listens" in the viewer's locale. */
export function listensLabel(t: TFunction<"user">, locale: string, count: number): string {
  return t("listenCount", { count, formatted: new Intl.NumberFormat(locale).format(count) })
}

/** A top artist/release card: the listens count replaces the artist links. */
export function profileTopCard(
  item: ProfileShelfItem,
  onPlay: (item: ShelfItem) => void,
  t: TFunction<"user">,
  locale: string,
): MediaCardProps {
  const count = listensLabel(t, locale, item.listens)
  return {
    ...shelfItemToCard(item, onPlay, t, locale),
    // One plain subtitle line instead of the links.
    subtitleLinks: undefined,
    subtitle: item.subtitle ? `${item.subtitle} · ${count}` : count,
  }
}

/** A profile playlist card; `onPlay` starts the playlist's play endpoint. */
export function profilePlaylistCard(
  playlist: PlaylistSummary,
  onPlay: (endpoint: string) => void,
  t: TFunction<"user">,
): MediaCardProps {
  return {
    id: playlist.id,
    type: "playlist",
    title: playlist.title,
    subtitle: t("trackCount", { ns: "playlist", count: playlist.track_count }),
    artwork: playlist.artwork,
    href: playlist.action.target,
    onPlay: () => onPlay(playlist.play_action.endpoint),
  }
}
